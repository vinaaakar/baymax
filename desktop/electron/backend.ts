// Starts (or attaches to) the local Python backend and reads its local API token.
import { ChildProcess, spawn } from "child_process";
import crypto from "crypto";
import fs from "fs";
import http from "http";
import os from "os";
import path from "path";

export interface BackendHandle { apiBase: string; token: string; child: ChildProcess | null; stop: () => void }

/** GUI-launched apps get launchd's minimal PATH; add Homebrew so whisper-cli/ollama/git are found. */
export function withHomebrewPath(current: string | undefined): string {
  const parts = (current ?? "/usr/bin:/bin:/usr/sbin:/sbin").split(":").filter(Boolean);
  for (const d of ["/opt/homebrew/bin", "/usr/local/bin"]) if (!parts.includes(d)) parts.push(d);
  return parts.join(":");
}

const PORT = Number(process.env.BAYMAX_LOCAL_PORT ?? 8765);
const DATA_DIR = process.env.BAYMAX_DATA_DIR ?? path.join(os.homedir(), ".baymax");

/** Same algorithm as backend/baymax/buildinfo.py: sha256 over (relpath, NUL, bytes, NUL) of every .py, sorted by relpath. */
export function sourceHash(pkgDir: string): string {
  const files: string[] = [];
  const walk = (d: string) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      if (e.name === "__pycache__") continue;
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".py")) files.push(path.relative(pkgDir, p).split(path.sep).join("/"));
    }
  };
  walk(pkgDir);
  files.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  const h = crypto.createHash("sha256");
  for (const rel of files) {
    h.update(Buffer.concat([Buffer.from(rel), Buffer.from([0]), fs.readFileSync(path.join(pkgDir, rel)), Buffer.from([0])]));
  }
  return h.digest("hex").slice(0, 12);
}

function healthInfo(): Promise<{ build?: string; pid?: number } | null> {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port: PORT, path: "/api/health", timeout: 800 }, (res) => {
      let body = "";
      res.on("data", (c) => (body += c));
      res.on("end", () => {
        try {
          resolve(res.statusCode === 200 ? JSON.parse(body) : null);
        } catch {
          resolve(null);
        }
      });
    });
    req.on("error", () => resolve(null));
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
  });
}

/** Stop a backend left running from an older version of the code (only a process that is really `-m baymax serve`). */
async function stopStaleBackend(pid: number | undefined, log: (m: string) => void): Promise<boolean> {
  const { execFileSync } = await import("child_process");
  let target = pid;
  if (!target) {
    try {
      target = Number(execFileSync("lsof", ["-ti", `tcp:${PORT}`, "-sTCP:LISTEN"]).toString().trim().split("\n")[0]);
    } catch {
      return false;
    }
  }
  if (!target) return false;
  let cmd = "";
  try {
    cmd = execFileSync("ps", ["-o", "command=", "-p", String(target)]).toString();
  } catch {
    return false;
  }
  if (!/-m baymax serve/.test(cmd)) {
    log(`port ${PORT} is used by another program (${cmd.trim().slice(0, 80)}); not stopping it`);
    return false;
  }
  log(`stopping stale backend pid ${target} (older code)`);
  process.kill(target, "SIGTERM");
  return waitFor(async () => !(await health()), 8000);
}

function health(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port: PORT, path: "/api/health", timeout: 800 }, (res) => {
      let body = "";
      res.on("data", (c) => (body += c));
      res.on("end", () => {
        try {
          resolve(res.statusCode === 200 && JSON.parse(body).name === "baymax");
        } catch {
          resolve(false);
        }
      });
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

/** Locate the backend sources: packaged resources first, then the repository layout. */
export function backendDir(resourcesPath: string, appPath: string): string | null {
  const candidates = [path.join(resourcesPath, "backend"), path.resolve(appPath, "..", "backend"), path.resolve(appPath, "..", "..", "backend")];
  return candidates.find((c) => fs.existsSync(path.join(c, "baymax", "__main__.py"))) ?? null;
}

function pythonFor(dir: string): string {
  const venv = path.join(dir, ".venv", "bin", "python");
  if (fs.existsSync(venv)) return venv;
  return process.env.BAYMAX_PYTHON ?? "python3";
}

async function waitFor(fn: () => Promise<boolean> | boolean, timeoutMs: number): Promise<boolean> {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (await fn()) return true;
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

export async function startBackend(resourcesPath: string, appPath: string, log: (m: string) => void): Promise<BackendHandle> {
  let child: ChildProcess | null = null;
  const srcDir = backendDir(resourcesPath, appPath);
  const running = await healthInfo();
  if (running && srcDir) {
    const expected = sourceHash(path.join(srcDir, "baymax"));
    if (running.build !== expected) {
      // Attaching would run old code (the cause of "fixed" behaviour not appearing after an update).
      log(`running backend build ${running.build ?? "unknown (pre-fingerprint)"} != source ${expected}`);
      if (!(await stopStaleBackend(running.pid, log))) {
        throw new Error(`An older BAYMAX backend is still running on port ${PORT}. Quit it (Terminal: pkill -f "baymax serve") and start BAYMAX again.`);
      }
    }
  }
  if (!(await health())) {
    const dir = srcDir;
    if (!dir) throw new Error("BAYMAX backend sources not found. Run the backend manually: cd backend && python3 -m baymax serve");
    const py = pythonFor(dir);
    log(`starting backend: ${py} -m baymax serve (cwd ${dir})`);
    child = spawn(py, ["-m", "baymax", "serve"], { cwd: dir, env: { ...process.env, PATH: withHomebrewPath(process.env.PATH), PYTHONUNBUFFERED: "1" }, stdio: ["ignore", "pipe", "pipe"] });
    child.stdout?.on("data", (d) => log(String(d).trim()));
    child.stderr?.on("data", (d) => log(String(d).trim()));
    child.on("exit", (code) => log(`backend exited with code ${code}`));
    const ok = await waitFor(health, 30000);
    if (!ok) {
      child.kill();
      throw new Error(`The backend did not start on 127.0.0.1:${PORT}. Check that Python 3.11+ and the backend dependencies are installed (see docs/SETUP.md).`);
    }
  } else {
    log(`attached to running backend on port ${PORT}`);
  }
  const tokenPath = path.join(DATA_DIR, "local_token");
  await waitFor(() => fs.existsSync(tokenPath), 5000);
  const token = fs.readFileSync(tokenPath, "utf8").trim();
  return {
    apiBase: `http://127.0.0.1:${PORT}`,
    token,
    child,
    stop: () => {
      if (child && child.exitCode === null) child.kill("SIGTERM");
    },
  };
}

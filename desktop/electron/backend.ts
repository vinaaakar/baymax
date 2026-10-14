// Starts (or attaches to) the local Python backend and reads its local API token.
import { ChildProcess, spawn } from "child_process";
import fs from "fs";
import http from "http";
import os from "os";
import path from "path";

export interface BackendHandle { apiBase: string; token: string; child: ChildProcess | null; stop: () => void }

const PORT = Number(process.env.BAYMAX_LOCAL_PORT ?? 8765);
const DATA_DIR = process.env.BAYMAX_DATA_DIR ?? path.join(os.homedir(), ".baymax");

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
  if (!(await health())) {
    const dir = backendDir(resourcesPath, appPath);
    if (!dir) throw new Error("BAYMAX backend sources not found. Run the backend manually: cd backend && python3 -m baymax serve");
    const py = pythonFor(dir);
    log(`starting backend: ${py} -m baymax serve (cwd ${dir})`);
    child = spawn(py, ["-m", "baymax", "serve"], { cwd: dir, env: { ...process.env, PYTHONUNBUFFERED: "1" }, stdio: ["ignore", "pipe", "pipe"] });
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

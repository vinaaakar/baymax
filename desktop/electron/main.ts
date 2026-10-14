// BAYMAX desktop main process.
import { app, BrowserWindow, dialog, globalShortcut, ipcMain, Menu, nativeImage, screen, session, systemPreferences, Tray } from "electron";
import http from "http";
import path from "path";
import { startBackend, type BackendHandle } from "./backend";
import { eyesIconPng } from "./icon";
import { PresenceLink } from "./presence";
import { lookFromWindow } from "./presenceLogic";

type WinName = "companion" | "conversation" | "workspace";

const DEV_URL = process.env.BAYMAX_DEV_URL; // e.g. http://localhost:5173 when running `npm run dev`
const logs: string[] = [];
const log = (m: string) => {
  logs.push(`${new Date().toISOString()} ${m}`);
  if (logs.length > 500) logs.shift();
  if (process.env.BAYMAX_DEBUG) console.log(m);
};

let backend: BackendHandle | null = null;
let tray: Tray | null = null;
const windows: Partial<Record<WinName, BrowserWindow>> = {};
let cursorTimer: NodeJS.Timeout | null = null;
let quitting = false;
let presence: PresenceLink | null = null;
let sleeping = false;

function rendererUrl(name: WinName): string {
  if (DEV_URL) return `${DEV_URL}/#/${name}`;
  return `file://${path.join(__dirname, "..", "dist", "index.html")}#/${name}`;
}

function createWindow(name: WinName): BrowserWindow {
  const existing = windows[name];
  if (existing && !existing.isDestroyed()) {
    existing.show();
    existing.focus();
    return existing;
  }
  const { workArea } = screen.getPrimaryDisplay();
  const common = {
    show: false,
    backgroundColor: "#180509",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
      backgroundThrottling: true,
    },
  } as const;
  const win =
    name === "companion"
      ? new BrowserWindow({
          // Landscape companion: 2D Baymax (head and body) with the status bar on top and controls along the bottom.
          ...common, width: 640, height: 400, x: workArea.x + workArea.width - 660, y: workArea.y + workArea.height - 420,
          frame: false, resizable: true, minWidth: 480, minHeight: 320, alwaysOnTop: true, skipTaskbar: true, roundedCorners: true,
          titleBarStyle: "hidden", hasShadow: true, title: "BAYMAX",
        })
      : name === "conversation"
        ? new BrowserWindow({ ...common, width: 860, height: 640, minWidth: 600, minHeight: 420, title: "BAYMAX — Conversation", titleBarStyle: "hiddenInset" })
        : new BrowserWindow({ ...common, width: 1280, height: 820, minWidth: 820, minHeight: 560, title: "BAYMAX — Workspace", titleBarStyle: "hiddenInset" });
  if (name === "companion") win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.once("ready-to-show", () => win.show());
  win.on("close", (e) => {
    if (!quitting && name === "companion") {
      e.preventDefault(); // closing the companion hides it; quit from the menu bar
      win.hide();
    }
  });
  win.on("closed", () => delete windows[name]);
  // Never navigate away or open arbitrary windows from the renderer.
  win.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith(rendererUrl(name).split("#")[0])) e.preventDefault();
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    // Artifact previews open in a sandboxed in-app window (no preload, no node), so the local API
    // token in the URL never reaches an external browser or its history.
    if (backend && url.startsWith(backend.apiBase + "/api/artifacts/")) {
      return { action: "allow", overrideBrowserWindowOptions: { width: 900, height: 700, backgroundColor: "#180509",
        webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } } };
    }
    return { action: "deny" };
  });
  win.loadURL(rendererUrl(name));
  windows[name] = win;
  return win;
}

function startCursorTracking(): void {
  if (cursorTimer) return;
  // 15 Hz is enough for gentle eye tracking and keeps CPU use negligible.
  cursorTimer = setInterval(() => {
    const win = windows.companion;
    if (sleeping || !win || win.isDestroyed() || !win.isVisible()) return; // sleep suspends cursor tracking
    const p = screen.getCursorScreenPoint();
    win.webContents.send("baymax:cursor", lookFromWindow(p, win.getBounds()));
  }, 66);
}

function apiPost(pathname: string, body: unknown): void {
  if (!backend) return;
  const data = JSON.stringify(body);
  const req = http.request(`${backend.apiBase}${pathname}`, {
    method: "POST", headers: { Authorization: `Bearer ${backend.token}`, "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) },
  });
  req.on("error", (e) => log(`api error: ${e.message}`));
  req.end(data);
}

function buildTray(): void {
  const img = nativeImage.createFromBuffer(eyesIconPng(22, 2), { scaleFactor: 2 });
  img.setTemplateImage(true);
  tray = new Tray(img);
  tray.setToolTip("BAYMAX");
  const menu = Menu.buildFromTemplate([
    { label: "Show Companion", click: () => createWindow("companion") },
    { label: "Hide Companion", click: () => windows.companion?.hide() },
    { label: "Conversation", click: () => createWindow("conversation") },
    { label: "Engineering Workspace", click: () => createWindow("workspace") },
    { type: "separator" },
    { label: "Sleep", click: () => apiPost("/api/sleep", { sleeping: true }) },
    { label: "Wake", click: () => apiPost("/api/sleep", { sleeping: false }) },
    { type: "separator" },
    { label: "Emergency Stop", accelerator: "CmdOrCtrl+Shift+.", click: () => apiPost("/api/estop", { engaged: true, reason: "menu bar" }) },
    { label: "Reset Emergency Stop", click: () => apiPost("/api/estop", { engaged: false }) },
    { type: "separator" },
    { label: "Show Backend Log", click: () => dialog.showMessageBox({ message: "Backend log (latest)", detail: logs.slice(-40).join("\n") }) },
    { label: "Quit BAYMAX", accelerator: "CmdOrCtrl+Q", click: () => { quitting = true; app.quit(); } },
  ]);
  tray.setContextMenu(menu);
}

ipcMain.handle("baymax:config", () => {
  if (!backend) throw new Error("backend not ready");
  return { apiBase: backend.apiBase, token: backend.token };
});
// The companion shows only the face; everything else is in this right-click menu (and the menu-bar tray).
ipcMain.on("baymax:companionMenu", (e) => {
  const win = BrowserWindow.fromWebContents(e.sender);
  Menu.buildFromTemplate([
    { label: "Conversation", click: () => createWindow("conversation") },
    { label: "Engineering Workspace", click: () => createWindow("workspace") },
    { type: "separator" },
    sleeping ? { label: "Wake", click: () => apiPost("/api/sleep", { sleeping: false }) } : { label: "Sleep", click: () => apiPost("/api/sleep", { sleeping: true }) },
    { type: "separator" },
    { label: "Emergency Stop", accelerator: "CmdOrCtrl+Shift+.", click: () => apiPost("/api/estop", { engaged: true, reason: "companion menu" }) },
    { label: "Hide Companion", click: () => windows.companion?.hide() },
  ]).popup(win ? { window: win } : undefined);
});

ipcMain.on("baymax:open", (_e, name: WinName) => {
  if (name === "companion" || name === "conversation" || name === "workspace") createWindow(name);
});
ipcMain.handle("baymax:mic", async () => {
  if (process.platform !== "darwin") return true;
  const status = systemPreferences.getMediaAccessStatus("microphone");
  if (status === "granted") return true;
  if (status === "denied" || status === "restricted") return false;
  return systemPreferences.askForMediaAccess("microphone");
});

app.whenReady().then(async () => {
  // Only the microphone may be requested, and only by our own pages.
  session.defaultSession.setPermissionRequestHandler((wc, permission, cb) => {
    const own = wc.getURL().startsWith("file://") || (DEV_URL ? wc.getURL().startsWith(DEV_URL) : false);
    cb(own && permission === "media");
  });
  if (process.platform === "darwin") app.dock?.hide();
  try {
    backend = await startBackend(process.resourcesPath, app.getAppPath(), log);
  } catch (e) {
    dialog.showErrorBox("BAYMAX could not start its backend", (e as Error).message);
    app.quit();
    return;
  }
  buildTray();
  // Emergency Stop is always one keystroke away, even though the companion no longer shows a STOP button.
  if (!globalShortcut.register("CmdOrCtrl+Shift+.", () => apiPost("/api/estop", { engaged: true, reason: "keyboard shortcut" })))
    log("could not register the Emergency Stop shortcut (another app may use Cmd+Shift+.)");
  app.on("will-quit", () => globalShortcut.unregisterAll());
  createWindow("companion");
  startCursorTracking();
  presence = new PresenceLink(backend.apiBase, backend.token, {
    log,
    onSleep: (s) => (sleeping = s),
    onGazeTarget: (t) => {
      const win = windows.companion;
      if (!win || win.isDestroyed()) return;
      win.webContents.send("baymax:gaze", { ...lookFromWindow(t, win.getBounds()), label: t.label, durationMs: t.durationMs });
    },
  });
  presence.start();
  if (process.env.BAYMAX_OPEN_WORKSPACE) createWindow("workspace");
});

app.on("before-quit", () => {
  quitting = true;
  if (cursorTimer) clearInterval(cursorTimer);
  presence?.stop();
  backend?.stop();
});
app.on("window-all-closed", () => {
  // keep running in the menu bar
});
app.on("activate", () => createWindow("companion"));

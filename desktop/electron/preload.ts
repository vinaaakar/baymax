// Minimal, explicit bridge between the sandboxed renderer and the main process.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("baymax", {
  platform: process.platform,
  config: (): Promise<{ apiBase: string; token: string }> => ipcRenderer.invoke("baymax:config"),
  openWindow: (name: "companion" | "conversation" | "workspace") => ipcRenderer.send("baymax:open", name),
  requestMicrophone: (): Promise<boolean> => ipcRenderer.invoke("baymax:mic"),
  onGaze: (cb: (g: { x: number; y: number; label: string; durationMs: number }) => void) => {
    const handler = (_e: unknown, g: { x: number; y: number; label: string; durationMs: number }) => cb(g);
    ipcRenderer.on("baymax:gaze", handler);
    return () => ipcRenderer.removeListener("baymax:gaze", handler);
  },
  onCursor: (cb: (p: { x: number; y: number }) => void) => {
    const handler = (_e: unknown, p: { x: number; y: number }) => cb(p);
    ipcRenderer.on("baymax:cursor", handler);
    return () => ipcRenderer.removeListener("baymax:cursor", handler);
  },
});

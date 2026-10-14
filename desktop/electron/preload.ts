// Minimal, explicit bridge between the sandboxed renderer and the main process.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("baymax", {
  platform: process.platform,
  config: (): Promise<{ apiBase: string; token: string }> => ipcRenderer.invoke("baymax:config"),
  openWindow: (name: "companion" | "conversation" | "workspace") => ipcRenderer.send("baymax:open", name),
  requestMicrophone: (): Promise<boolean> => ipcRenderer.invoke("baymax:mic"),
  onCursor: (cb: (p: { x: number; y: number }) => void) => {
    const handler = (_e: unknown, p: { x: number; y: number }) => cb(p);
    ipcRenderer.on("baymax:cursor", handler);
    return () => ipcRenderer.removeListener("baymax:cursor", handler);
  },
});

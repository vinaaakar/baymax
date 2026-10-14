#!/usr/bin/env python3
"""End-to-end cursor pipeline test on X11 (Linux stand-in for macOS).

Real components: the Electron app (main process reads the cursor with screen.getCursorScreenPoint),
the Python backend, the TLS-pinned + HMAC-authenticated device protocol (v2).
Simulated component: the iPhone (this script plays the phone's protocol role).
The X pointer is moved with XWarpPointer, exactly like a user moving the mouse.

Usage (inside an X display, e.g. `xvfb-run -s "-screen 0 1512x982x24" python3 cursor_pipeline_x11.py`):
  requires: backend venv with python-xlib, built desktop app (npm run build), Electron binary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import aiohttp
from Xlib import display as xdisplay

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from baymax.security.pairing import compute_mac  # noqa: E402

PORT, LAN, CDP = 18765, 18766, 19229


async def face_state(s: aiohttp.ClientSession, shot: Path | None = None) -> list[dict]:
    """Reads what the real Electron companion face is drawing (renderer, expression, pupil offsets) over CDP."""
    out = []
    async with s.get(f"http://127.0.0.1:{CDP}/json") as r:
        targets = [t for t in await r.json() if t.get("type") == "page"]
    for t in targets:
        async with s.ws_connect(t["webSocketDebuggerUrl"], max_msg_size=0) as ws:
            expr = ("(() => { const el = document.querySelector('.character-canvas'); return el ? JSON.stringify({url: location.href, "
                    "renderer: el.dataset.renderer, expression: el.dataset.expression, pupil: el.dataset.pupil, "
                    "webgl: !!document.querySelector('.character-canvas canvas'), w: el.clientWidth, h: el.clientHeight}) : null })()")
            await ws.send_json({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}})
            res = await ws.receive_json(timeout=15)
            val = res.get("result", {}).get("result", {}).get("value")
            if not val:
                continue
            info = json.loads(val)
            if shot is not None and info["w"] < 600:  # the compact companion window
                await ws.send_json({"id": 2, "method": "Page.captureScreenshot", "params": {"format": "png"}})
                img = await ws.receive_json(timeout=15)
                import base64
                shot.write_bytes(base64.b64decode(img["result"]["data"]))
                info["screenshot"] = str(shot)
            out.append(info)
    return out


def env(t, p, mid="m"):
    return json.dumps({"v": 2, "type": t, "id": mid, "ts": 0, "payload": p})


async def main() -> int:
    data = Path(tempfile.mkdtemp(prefix="bmx-e2e-"))
    (data / "config.toml").write_text(f'workspace_dir = "{data}/ws"\n[network]\nlocal_port = {PORT}\nlan_port = {LAN}\nlan_host = "127.0.0.1"\n')
    envv = {**os.environ, "BAYMAX_DATA_DIR": str(data), "BAYMAX_LOCAL_PORT": str(PORT)}
    backend = subprocess.Popen([str(ROOT / "backend/.venv/bin/python"), "-m", "baymax", "serve"], cwd=ROOT / "backend", env=envv,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    electron = None
    try:
        async with aiohttp.ClientSession() as s:
            for _ in range(60):
                try:
                    async with s.get(f"http://127.0.0.1:{PORT}/api/health"):
                        break
                except aiohttp.ClientError:
                    await asyncio.sleep(0.25)
            token = (data / "local_token").read_text().strip()
            H = {"Authorization": f"Bearer {token}"}
            async with s.post(f"http://127.0.0.1:{PORT}/api/pairing/start", headers=H) as r:
                pairing = await r.json()

            # --- simulated iPhone: pin the certificate from the "QR code", pair, authenticate (v2)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            phone = await s.ws_connect(f"wss://127.0.0.1:{LAN}/v1/ws", ssl=ctx)
            der = phone._response.connection.transport.get_extra_info("ssl_object").getpeercert(binary_form=True)
            assert hashlib.sha256(der).hexdigest() == pairing["fingerprint"], "certificate pin mismatch"

            async def recv(t, timeout=5.0):
                end = time.monotonic() + timeout
                while True:
                    m = json.loads((await phone.receive(timeout=max(0.01, end - time.monotonic()))).data)
                    if m["type"] == t:
                        return m

            await phone.send_str(env("hello", {"protocol": 2, "minProtocol": 1, "client": "sim-iphone"}))
            await recv("hello.ack")
            await phone.send_str(env("pair.request", {"code": pairing["code"], "deviceId": "iphone-sim-x11", "deviceName": "Simulated iPhone"}))
            key = (await recv("pair.ok"))["payload"]["deviceKey"]
            nonce = (await recv("auth.challenge"))["payload"]["nonce"]
            await phone.send_str(env("auth.response", {"deviceId": "iphone-sim-x11", "mac": compute_mac(key, nonce, "iphone-sim-x11")}))
            await recv("auth.ok")
            print("phone: paired and authenticated over TLS (v2)")

            # --- launch the real Electron app (it attaches to the running backend)
            exe = ROOT / "desktop/node_modules/electron/dist/electron"
            electron = subprocess.Popen([str(exe), str(ROOT / "desktop"), "--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader",
                                         f"--remote-debugging-port={CDP}"],
                                        env=envv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            d = xdisplay.Display()
            root = d.screen().root
            geom = root.get_geometry()
            W, Hh = geom.width, geom.height
            print(f"X display {W}x{Hh}")

            # wait for the first cursor sample (Electron started and connected)
            root.warp_pointer(10, 10)
            d.sync()
            first = None
            for _ in range(80):
                try:
                    first = await recv("cursor.update", timeout=0.5)
                    break
                except asyncio.TimeoutError:
                    root.warp_pointer(10 + (_ % 2) * 5, 10)
                    d.sync()
            assert first, "no cursor.update received from the Electron app"
            print("first sample:", first["payload"])

            # --- move the real pointer along a path and check what the phone receives.
            # X11 limitation (not macOS): Chromium on X11 only learns the pointer position from events delivered to
            # its own windows, so the path stays inside Electron's mapped windows. Coordinates are still global
            # screen coordinates. On macOS getCursorScreenPoint() reads NSEvent.mouseLocation, which is global.
            wins = []

            def walk(w):
                for c in w.query_tree().children:
                    try:
                        a = c.get_attributes()
                        g = c.get_geometry()
                        if a.map_state == 2 and g.width > 50 and g.height > 50:
                            t = c.translate_coords(root, 0, 0)
                            wins.append((-t.x, -t.y, g.width, g.height))
                    except Exception as e:  # windows can vanish while we walk
                        print("  skip window:", e)
                    walk(c)

            for _ in range(40):
                walk(root)
                if wins:
                    break
                await asyncio.sleep(0.25)
            assert wins, "no mapped Electron window found"
            bx, by, bw, bh = max(wins, key=lambda r: r[2] * r[3])
            print(f"largest Electron window: {bw}x{bh} at ({bx},{by})")
            path = [(bx + int(bw * fx), by + int(bh * fy)) for fx, fy in
                    ((0.05, 0.05), (0.95, 0.05), (0.95, 0.95), (0.05, 0.95), (0.5, 0.5), (0.25, 0.33), (0.75, 0.66))]
            errors = []
            latencies = []
            for x, y in path:
                t0 = time.monotonic()
                root.warp_pointer(x, y)
                d.sync()
                while True:
                    m = await recv("cursor.update", timeout=3)
                    p = m["payload"]
                    if os.environ.get("E2E_DEBUG"):
                        print("  recv", p["vx"], p["vy"], "want", round(x / W, 4), round(y / Hh, 4))
                    if abs(p["vx"] - x / W) < 0.002 and abs(p["vy"] - y / Hh) < 0.002:
                        latencies.append((time.monotonic() - t0) * 1000)
                        break
                errors.append(max(abs(p["vx"] - x / W), abs(p["vy"] - y / Hh)))
                print(f"pointer ({x:4d},{y:4d}) -> phone vx={p['vx']:.4f} vy={p['vy']:.4f} display={p['displayId']} "
                      f"{p['displayWidth']}x{p['displayHeight']} latency={latencies[-1]:.0f} ms")

            # --- the companion face (2D SVG) follows the same real pointer: pupils left of centre for a pointer on the left, right for
            # one on the right (the Mac maps the pointer relative to the companion window, 600 px for a full look)
            faces = {}
            for name, (x, y) in (("left", path[0]), ("right", path[1])):
                root.warp_pointer(x, y)
                d.sync()
                await asyncio.sleep(1.2)
                for attempt in range(3):  # DevTools can be slow to answer while the renderer is busy; retry, never guess
                    try:
                        faces[name] = await face_state(s, ROOT / "docs/images/companion-2d-electron.png" if name == "right" else None)
                        if any(f["w"] < 600 for f in faces[name]):
                            break
                    except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                        print(f"  devtools read {attempt + 1} failed: {exc!r}")
                        faces[name] = []
            comp = [(fl, fr) for fl in faces["left"] for fr in faces["right"] if fl["url"] == fr["url"] and fl["w"] < 600]
            face_ok = bool(comp)
            for fl, fr in comp:
                lx, rx = float(fl["pupil"].split(",")[0]), float(fr["pupil"].split(",")[0])
                print(f"companion face: renderer={fr['renderer']} webgl-canvas={fr['webgl']} expression={fl['expression']}/{fr['expression']} "
                      f"pupil x left={lx:.3f} right={rx:.3f}")
                face_ok = face_ok and fr["renderer"] == "svg-2d" and not fr["webgl"] and lx < -0.03 and rx > 0.03 and fr["expression"] == "TRACKING"
            print(f"2D face follows the real pointer: {face_ok}")

            # --- sleep pauses streaming, wake resumes
            async with s.post(f"http://127.0.0.1:{PORT}/api/sleep", json={"sleeping": True}, headers=H):
                pass
            await recv("sleep.state")
            await asyncio.sleep(0.3)
            root.warp_pointer(*path[1])
            d.sync()
            try:
                await recv("cursor.update", timeout=1.0)
                slept_ok = False
            except asyncio.TimeoutError:
                slept_ok = True
            async with s.post(f"http://127.0.0.1:{PORT}/api/sleep", json={"sleeping": False}, headers=H):
                pass
            root.warp_pointer(*path[4])
            d.sync()
            woke = await recv("cursor.update", timeout=3)
            print(f"sleep paused streaming: {slept_ok}; after wake: vx={woke['payload']['vx']:.4f}")
            async with s.get(f"http://127.0.0.1:{PORT}/api/status", headers=H) as r:
                st = (await r.json())["cursor"]
            print("backend relay stats:", {k: st[k] for k in ("received", "published", "rejected")})
            ok = max(errors) < 0.002 and slept_ok and face_ok
            print(f"RESULT: {'PASS' if ok else 'FAIL'} — {len(path)} positions, max error {max(errors):.5f}, "
                  f"median latency {sorted(latencies)[len(latencies) // 2]:.0f} ms")
            await phone.close()
            return 0 if ok else 1
    finally:
        if electron:
            electron.terminate()
        backend.terminate()
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

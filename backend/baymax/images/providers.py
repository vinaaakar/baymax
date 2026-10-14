"""Image generation integration point.

Result kinds are explicit so a placeholder can never pass for a generated image:

* ``generated``   — produced by a real generator from a text prompt
* ``edited``      — produced by a real generator from an input image
* ``reference``   — a user-supplied image copied into the workspace
* ``placeholder`` — an SVG labelled PLACEHOLDER, only when explicitly requested
* ``unsupported`` — no generator configured/available (nothing is written)

Implemented generator: any server speaking the AUTOMATIC1111 web-UI API
(``/sdapi/v1/txt2img`` and ``/img2img``; also SD WebUI Forge, SD.Next).
Paid cloud APIs are intentionally not implemented.
"""

from __future__ import annotations

import asyncio
import base64
import html
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from ..config import ImageConfig

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class ImageUnavailable(Exception):
    pass


@dataclass
class ImageResult:
    kind: str
    path: Path | None
    width: int = 0
    height: int = 0
    provider: str = ""
    prompt: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != PNG_MAGIC or data[12:16] != b"IHDR":
        raise ValueError("not a valid PNG image")
    w, h = struct.unpack(">II", data[16:24])
    if w == 0 or h == 0 or w > 16384 or h > 16384:
        raise ValueError("implausible PNG dimensions")
    return w, h


def image_size(data: bytes) -> tuple[str, int, int]:
    if data[:8] == PNG_MAGIC:
        w, h = png_size(data)
        return "png", w, h
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpg", w, h
            i += 2 + seg
        raise ValueError("JPEG without size marker")
    raise ValueError("unsupported image format (PNG or JPEG required)")


class ImageService:
    def __init__(self, config: ImageConfig) -> None:
        self.config = config

    async def status(self) -> dict[str, Any]:
        if self.config.provider == "none":
            return {"available": False, "provider": "none",
                    "message": "No image generator configured. Install a local Stable Diffusion server with the AUTOMATIC1111 API "
                               "(e.g. SD WebUI Forge, launched with --api) and set images.provider = \"automatic1111\"."}
        if self.config.provider != "automatic1111":
            return {"available": False, "provider": self.config.provider, "message": f"provider '{self.config.provider}' is not implemented"}
        base = self.config.base_url or "http://127.0.0.1:7860"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{base}/sdapi/v1/sd-models", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        models = [m.get("title", "") for m in await r.json()]
                        return {"available": True, "provider": "automatic1111", "baseUrl": base, "models": models[:20], "message": "ready"}
                    return {"available": False, "provider": "automatic1111", "message": f"server responded HTTP {r.status}"}
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            return {"available": False, "provider": "automatic1111", "message": f"no server at {base} ({exc.__class__.__name__})"}

    async def generate(self, prompt: str, out_dir: Path, *, width: int = 768, height: int = 768, steps: int = 25,
                       negative: str = "", init_image: Path | None = None, strength: float = 0.6) -> ImageResult:
        st = await self.status()
        if not st["available"]:
            raise ImageUnavailable(st["message"])
        base = st["baseUrl"]
        body: dict[str, Any] = {"prompt": prompt, "negative_prompt": negative, "width": max(256, min(width, 1536)),
                                "height": max(256, min(height, 1536)), "steps": max(5, min(steps, 80))}
        endpoint = "txt2img"
        if init_image is not None:
            body["init_images"] = [base64.b64encode(init_image.read_bytes()).decode()]
            body["denoising_strength"] = max(0.05, min(strength, 1.0))
            endpoint = "img2img"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{base}/sdapi/v1/{endpoint}", json=body, timeout=aiohttp.ClientTimeout(total=self.config.timeout_s)) as r:
                    if r.status != 200:
                        raise ImageUnavailable(f"generator returned HTTP {r.status}: {(await r.text())[:200]}")
                    data = await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise ImageUnavailable(f"image generation failed: {exc.__class__.__name__}") from None
        images = data.get("images") or []
        if not images:
            raise ImageUnavailable("generator returned no image")
        raw = base64.b64decode(images[0].split(",", 1)[-1])
        w, h = png_size(raw)  # refuse to save anything that isn't a decodable PNG
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{'edit' if init_image else 'gen'}-{abs(hash(prompt)) % 10**8:08d}-{w}x{h}.png"
        path.write_bytes(raw)
        return ImageResult("edited" if init_image else "generated", path, w, h, "automatic1111", prompt, {"steps": body["steps"]})


def placeholder_svg(label: str, width: int = 768, height: int = 512) -> str:
    text = html.escape(label[:80])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<rect width="100%" height="100%" fill="#180509"/><rect x="8" y="8" width="{width - 16}" height="{height - 16}" fill="none" '
            f'stroke="#FF334F" stroke-width="4" stroke-dasharray="16 10"/>'
            f'<text x="50%" y="45%" fill="#FF334F" font-family="system-ui" font-size="44" text-anchor="middle">PLACEHOLDER</text>'
            f'<text x="50%" y="58%" fill="#F5F5F2" font-family="system-ui" font-size="20" text-anchor="middle">{text}</text>'
            f'<text x="50%" y="68%" fill="#c9a9ae" font-family="system-ui" font-size="14" text-anchor="middle">Not a generated image</text></svg>')

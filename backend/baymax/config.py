"""Configuration loading.

Settings come from (lowest to highest precedence):
  1. built-in defaults in this file
  2. ``<data_dir>/config.toml``
  3. environment variables ``BAYMAX_*`` (only a documented subset)

Secrets are never stored here; see :mod:`baymax.security.secrets`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


def default_data_dir() -> Path:
    env = os.environ.get("BAYMAX_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".baymax"


@dataclass
class LLMConfig:
    provider: str = "ollama"  # only local providers are implemented
    base_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.2:3b"
    # Bounded context/output keep memory use predictable on a 16 GB machine.
    num_ctx: int = 4096
    max_output_tokens: int = 512
    temperature: float = 0.4
    request_timeout_s: float = 120.0
    connect_timeout_s: float = 3.0
    keep_alive: str = "10m"


@dataclass
class VoiceConfig:
    stt_engine: str = "whisper.cpp"  # "whisper.cpp" | "none"
    whisper_binary: str = "whisper-cli"
    whisper_model: str = "~/.baymax/models/ggml-base.en.bin"
    stt_timeout_s: float = 60.0
    tts_engine: str = "auto"  # "auto" | "say" | "piper" | "none"
    tts_voice: str = ""
    tts_rate_wpm: int = 185
    piper_binary: str = "piper"
    piper_model: str = ""
    max_audio_seconds: int = 60


@dataclass
class NetworkConfig:
    local_host: str = "127.0.0.1"
    local_port: int = 8765
    lan_enabled: bool = False
    lan_host: str = "0.0.0.0"
    lan_port: int = 8766
    pairing_ttl_s: int = 300
    pairing_max_attempts: int = 5


@dataclass
class ResearchConfig:
    # local_only=True disables every network fetch performed by research tools.
    local_only: bool = False
    search_provider: str = "duckduckgo"  # "duckduckgo" | "searxng"
    searxng_url: str = ""
    max_results: int = 5
    fetch_timeout_s: float = 15.0
    max_page_bytes: int = 1_500_000
    # Tests/LAN docs only: allow fetching private addresses (SSRF guard off). Never enable for general browsing.
    allow_private_fetch: bool = False


@dataclass
class ImageConfig:
    # "none" (default), "automatic1111", "comfyui". Paid APIs are not implemented.
    provider: str = "none"
    base_url: str = ""
    timeout_s: float = 300.0


@dataclass
class SecurityConfig:
    # Whether a paired phone may approve HIGH-risk actions (deny/cancel/estop always allowed).
    remote_high_risk_approval: bool = False
    approval_timeout_s: int = 300
    allow_desktop_control: bool = True
    hardware_control_enabled: bool = False


@dataclass
class AgentConfig:
    max_repair_attempts: int = 3
    command_timeout_s: float = 600.0
    max_command_output_bytes: int = 200_000


@dataclass
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    workspace_dir: Path = field(default_factory=lambda: Path.home() / "Developer" / "BAYMAX" / "workspace")
    log_level: str = "INFO"
    llm: LLMConfig = field(default_factory=LLMConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    images: ImageConfig = field(default_factory=ImageConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "baymax.db"

    @property
    def artifacts_dir(self) -> Path:
        return self.workspace_dir / "artifacts"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.toml"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def to_public_dict(self) -> dict[str, Any]:
        d = _to_dict(self)
        return d


def _to_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return {f.name: _to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _apply(target: Any, values: dict[str, Any]) -> None:
    for f in fields(target):
        if f.name not in values:
            continue
        current = getattr(target, f.name)
        new = values[f.name]
        if is_dataclass(current):
            if isinstance(new, dict):
                _apply(current, new)
            continue
        if isinstance(current, Path):
            setattr(target, f.name, Path(str(new)).expanduser())
        elif isinstance(current, bool):
            setattr(target, f.name, bool(new))
        elif isinstance(current, int) and not isinstance(current, bool):
            setattr(target, f.name, int(new))
        elif isinstance(current, float):
            setattr(target, f.name, float(new))
        else:
            setattr(target, f.name, new)


_ENV_MAP = {
    "BAYMAX_WORKSPACE": ("workspace_dir",),
    "BAYMAX_LOG_LEVEL": ("log_level",),
    "BAYMAX_MODEL": ("llm", "model"),
    "BAYMAX_OLLAMA_URL": ("llm", "base_url"),
    "BAYMAX_LOCAL_PORT": ("network", "local_port"),
    "BAYMAX_LAN_ENABLED": ("network", "lan_enabled"),
    "BAYMAX_LAN_PORT": ("network", "lan_port"),
    "BAYMAX_LOCAL_ONLY": ("research", "local_only"),
}


def _env_value(raw: str) -> Any:
    low = raw.strip().lower()
    if low in ("1", "true", "yes", "on"):
        return True
    if low in ("0", "false", "no", "off"):
        return False
    return raw


def load_settings(data_dir: Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
    settings = Settings()
    if data_dir is not None:
        settings.data_dir = Path(data_dir)
    cfg_path = settings.config_path
    if cfg_path.exists():
        with cfg_path.open("rb") as fh:
            _apply(settings, tomllib.load(fh))
    for env, path in _ENV_MAP.items():
        if env in os.environ:
            nested: dict[str, Any] = {}
            cursor = nested
            for key in path[:-1]:
                cursor = cursor.setdefault(key, {})
            cursor[path[-1]] = _env_value(os.environ[env])
            _apply(settings, nested)
    if overrides:
        _apply(settings, overrides)
    return settings


# Fields the UI may change at runtime. Anything else must be edited in config.toml.
MUTABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "llm.model": ("llm", "model"),
    "llm.temperature": ("llm", "temperature"),
    "llm.num_ctx": ("llm", "num_ctx"),
    "llm.max_output_tokens": ("llm", "max_output_tokens"),
    "voice.tts_engine": ("voice", "tts_engine"),
    "voice.tts_voice": ("voice", "tts_voice"),
    "research.local_only": ("research", "local_only"),
    "security.remote_high_risk_approval": ("security", "remote_high_risk_approval"),
    "security.allow_desktop_control": ("security", "allow_desktop_control"),
    "images.provider": ("images", "provider"),
    "images.base_url": ("images", "base_url"),
}


def update_setting(settings: Settings, key: str, value: Any) -> None:
    if key not in MUTABLE_FIELDS:
        raise KeyError(f"setting {key!r} cannot be changed at runtime")
    path = MUTABLE_FIELDS[key]
    nested: dict[str, Any] = {}
    cursor = nested
    for part in path[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[path[-1]] = value
    _apply(settings, nested)


def save_settings(settings: Settings) -> None:
    """Persist mutable settings to config.toml (simple TOML writer; no secrets)."""
    data = asdict(settings)
    lines: list[str] = [f'workspace_dir = {_toml(str(settings.workspace_dir))}', f"log_level = {_toml(settings.log_level)}", ""]
    for section in ("llm", "voice", "network", "research", "images", "security", "agent"):
        lines.append(f"[{section}]")
        for k, v in data[section].items():
            lines.append(f"{k} = {_toml(v)}")
        lines.append("")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.config_path.write_text("\n".join(lines), encoding="utf-8")


def _toml(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'

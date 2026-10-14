"""Command line entry point.

    python -m baymax serve [--lan]     start the backend (local API, optional LAN listener)
    python -m baymax doctor            report which features are available on this machine
    python -m baymax create "a red car"  generate and validate a 3D model without the server
    python -m baymax validate file.glb validate any GLB file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import load_settings
from .logging_setup import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baymax")
    parser.add_argument("--data-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--lan", action="store_true", help="enable the TLS LAN listener for the iPhone")
    serve.add_argument("--port", type=int, default=None)
    sub.add_parser("doctor")
    create = sub.add_parser("create")
    create.add_argument("prompt")
    create.add_argument("--out", type=Path, default=None)
    val = sub.add_parser("validate")
    val.add_argument("file", type=Path)
    args = parser.parse_args(argv)

    overrides = {"network": {"local_port": args.port}} if getattr(args, "port", None) else None
    settings = load_settings(args.data_dir, overrides)
    configure_logging(settings.log_level, settings.data_dir / "logs" if args.cmd == "serve" else None)

    if args.cmd == "serve":
        from .server.app import run_server
        from .services import build_services

        services = build_services(settings)
        asyncio.run(run_server(services, lan=True if args.lan else None))
        return 0
    if args.cmd == "doctor":
        from .doctor import run_doctor

        print(json.dumps(asyncio.run(run_doctor(settings)), indent=2))
        return 0
    if args.cmd == "create":
        from .creation3d.classifier import RequestClassifier
        from .creation3d.gltf import export_glb
        from .creation3d.planner import Planner
        from .creation3d.registry import GeneratorRegistry
        from .creation3d.validate import validate_glb

        reg = GeneratorRegistry()
        cls = RequestClassifier(reg).classify(args.prompt)
        if cls.status != "supported":
            print(f"No generator for '{cls.unknown_object}'. Supported: {', '.join(reg.supported_types())}", file=sys.stderr)
            return 2
        planner = Planner(reg)
        spec = planner.plan(cls)
        data = export_glb(spec)
        out = args.out or Path(f"{spec.object_type.replace(' ', '-')}.glb")
        out.write_bytes(data)
        rep = validate_glb(out.read_bytes(), planner.generator_for(spec).required(spec.params), spec.object_type)
        print(json.dumps({"file": str(out), **rep.to_dict()}, indent=2))
        return 0 if rep.ok else 1
    if args.cmd == "validate":
        from .creation3d.validate import validate_glb

        rep = validate_glb(args.file.read_bytes())
        print(json.dumps(rep.to_dict(), indent=2))
        return 0 if rep.ok else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())

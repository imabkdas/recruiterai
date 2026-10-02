#!/usr/bin/env python3
"""Dump FastAPI OpenAPI schema to frontend/openapi.json without starting a server."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# If not running under project venv, re-exec with venv python to avoid C-extension ABI mismatch
root = Path(__file__).resolve().parent.parent
venv_python = root / ".venv" / "bin" / "python"
if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
    os.execv(str(venv_python), [str(venv_python), *sys.argv])

src_dir = root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from jobpilot.api.app import create_app  # noqa: E402


def dump_openapi() -> None:
    output_path = root / "frontend" / "openapi.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    app = create_app()
    schema = app.openapi()

    output_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(f"✅ Dumped OpenAPI schema ({len(schema.get('paths', {}))} paths) to {output_path}")


if __name__ == "__main__":
    dump_openapi()

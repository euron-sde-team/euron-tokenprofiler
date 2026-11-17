"""Writes docs/api/openapi.yaml from the FastAPI app (no engine, no network)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml
from tokenprofiler_api.main import create_app
from tokenprofiler_api.settings import ApiSettings

app = create_app(ApiSettings(_env_file=None, data_dir=Path(tempfile.mkdtemp())))
out = Path(__file__).resolve().parents[1] / "docs/api/openapi.yaml"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(yaml.safe_dump(app.openapi(), sort_keys=False, width=100), encoding="utf-8")
print(f"wrote {out}")

"""Load a local .env into os.environ for local runs. No-op in CI (no .env present),
where GitHub Actions injects the same names as real env vars."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | None = None) -> None:
    env = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


scripts_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(scripts_dir))

from codex_session_exporter.exporter import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

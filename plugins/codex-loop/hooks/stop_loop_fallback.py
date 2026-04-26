#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(ROOT))

from codex_loop.hook import main

if __name__ == "__main__":
    raise SystemExit(main())

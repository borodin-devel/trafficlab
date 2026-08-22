#!/usr/bin/env python3
"""Audit a retained Validation Study bundle offline."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.validation_study.audit.lifecycle import main

if __name__ == "__main__":
    raise SystemExit(main())

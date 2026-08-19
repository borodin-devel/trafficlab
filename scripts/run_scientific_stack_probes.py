#!/usr/bin/env python3
"""Generate or verify deterministic scientific-stack probe evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.scientific.probes.mmpp_likelihood import build_probe_evidence, write_probe_evidence

_REPOSITORY = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT = _REPOSITORY / "examples" / "scientific_stack" / "mmpp_cases.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail unless checked evidence is already canonical")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    matched = write_probe_evidence(arguments.output, build_probe_evidence(), check=arguments.check)
    if arguments.check and not matched:
        print(f"scientific probe evidence is stale: {arguments.output}")
        return 1
    action = "verified" if arguments.check else "wrote"
    print(f"{action} {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

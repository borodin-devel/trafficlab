#!/usr/bin/env python3
"""Generate or verify deterministic scientific-stack probe evidence."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, cast

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.scientific.probes.mmpp_likelihood import (
    build_probe_evidence as build_mmpp_evidence,
)
from tests.scientific.probes.mmpp_likelihood import (
    write_probe_evidence as write_mmpp_evidence,
)
from tests.scientific.probes.pymoo_optimizer import (
    build_probe_evidence as build_pymoo_evidence,
)
from tests.scientific.probes.pymoo_optimizer import (
    write_probe_evidence as write_pymoo_evidence,
)
from tests.scientific.probes.scapy_pcapng import (
    build_probe_evidence as build_scapy_evidence,
)
from tests.scientific.probes.scapy_pcapng import (
    check_probe_evidence as check_scapy_evidence,
)
from tests.scientific.probes.scapy_pcapng import (
    write_probe_evidence as write_scapy_evidence,
)

_REPOSITORY = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUTS = {
    "mmpp": _REPOSITORY / "examples" / "scientific_stack" / "mmpp_cases.json",
    "pymoo": _REPOSITORY / "examples" / "scientific_stack" / "pymoo_cases.json",
    "scapy": _REPOSITORY / "examples" / "scientific_stack" / "scapy_cases.json",
}
type ProbeName = Literal["mmpp", "pymoo", "scapy"]


def selected_probe_names(value: str) -> tuple[ProbeName, ...]:
    """Expand one explicit runner selection without fallback or duplication."""
    if value == "all":
        return ("mmpp", "pymoo", "scapy")
    if value in _DEFAULT_OUTPUTS:
        return (cast(ProbeName, value),)
    raise ValueError(f"unknown scientific-stack probe: {value}")


def _run_probe(name: ProbeName, output: Path, *, check: bool, skip_benchmarks: bool) -> bool:
    if name == "mmpp":
        return write_mmpp_evidence(output, build_mmpp_evidence(), check=check)
    if name == "pymoo":
        return write_pymoo_evidence(output, build_pymoo_evidence(), check=check)
    if check:
        return check_scapy_evidence(output)
    return write_scapy_evidence(
        output,
        build_scapy_evidence(run_benchmarks=not skip_benchmarks),
        check=False,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail unless checked evidence is already canonical")
    parser.add_argument("--probe", choices=("all", "mmpp", "pymoo", "scapy"), default="mmpp")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skip-benchmarks",
        action="store_true",
        help="write declared Scapy evidence without expensive benchmark samples",
    )
    arguments = parser.parse_args(argv)
    names = selected_probe_names(cast(str, arguments.probe))
    explicit_output = cast(Path | None, arguments.output)
    if len(names) > 1 and explicit_output is not None:
        print("scientific probe runner: --output requires one named probe", file=sys.stderr)
        return 2
    stale = False
    for name in names:
        output = _DEFAULT_OUTPUTS[name] if explicit_output is None else explicit_output
        matched = _run_probe(
            name,
            output,
            check=cast(bool, arguments.check),
            skip_benchmarks=cast(bool, arguments.skip_benchmarks),
        )
        if cast(bool, arguments.check) and not matched:
            print(f"scientific probe evidence is stale: {output}")
            stale = True
            continue
        action = "verified" if cast(bool, arguments.check) else "wrote"
        print(f"{action} {output}")
    return int(stale)


if __name__ == "__main__":
    raise SystemExit(main())

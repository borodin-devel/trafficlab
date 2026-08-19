#!/usr/bin/env python3
"""Generate or verify deterministic scientific-stack probe evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail unless checked evidence is already canonical")
    parser.add_argument("--probe", choices=("mmpp", "pymoo", "scapy"), default="mmpp")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skip-benchmarks",
        action="store_true",
        help="write declared Scapy evidence without expensive benchmark samples",
    )
    arguments = parser.parse_args()
    output = _DEFAULT_OUTPUTS[arguments.probe] if arguments.output is None else arguments.output
    if arguments.probe == "mmpp":
        matched = write_mmpp_evidence(output, build_mmpp_evidence(), check=arguments.check)
    elif arguments.probe == "pymoo":
        matched = write_pymoo_evidence(output, build_pymoo_evidence(), check=arguments.check)
    elif arguments.check:
        matched = check_scapy_evidence(output)
    else:
        matched = write_scapy_evidence(
            output,
            build_scapy_evidence(run_benchmarks=not arguments.skip_benchmarks),
            check=False,
        )
    if arguments.check and not matched:
        print(f"scientific probe evidence is stale: {output}")
        return 1
    action = "verified" if arguments.check else "wrote"
    print(f"{action} {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

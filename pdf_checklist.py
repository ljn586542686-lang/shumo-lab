"""Alarm-only PDF checklist generator for X5-F0.

This script is called as a timed subprocess by run_case.py so PDF checking can
be circuit-broken without modifying LaTeX or polluting output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from x5_surgical_utils import generate_pdf_checklist


def main() -> int:
    parser = argparse.ArgumentParser(description="X5-F0 PDF checklist")
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    payload = generate_pdf_checklist(Path(args.case_dir), Path(args.run_dir), strict=args.strict)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


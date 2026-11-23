"""Check Python dependencies required by the V3.0 pipeline."""

from __future__ import annotations

import importlib.util
import sys


REQUIRED_MODULES = {
    "yaml": "pyyaml",
    "pandas": "pandas",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "matplotlib": "matplotlib",
    "scipy": "scipy",
}


def check_environment() -> dict:
    missing = []
    present = []
    for module_name, package_name in REQUIRED_MODULES.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(package_name)
        else:
            present.append(package_name)
    return {
        "python": sys.version.split()[0],
        "present": present,
        "missing": missing,
        "status": "ok" if not missing else "fail",
    }


def main() -> None:
    report = check_environment()
    print(f"Python: {report['python']}")
    print("Present:", ", ".join(report["present"]) if report["present"] else "none")
    print("Missing:", ", ".join(report["missing"]) if report["missing"] else "none")
    if report["missing"]:
        print("Install with: python -m pip install -r requirements.txt")
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()

"""check_asset_manifest.py — Literature asset manifest integrity checker.

Reads pipeline_core/literature/assets_manifest_draft.json and checks:
  1. Which files listed in the manifest exist on disk.
  2. SHA256 hash of existing files vs. manifest-recorded hash.
  3. Extra PDF files on disk not in the manifest.

Does NOT:
  - modify any file
  - change citation_ready or formal_reference_allowed
  - require network access
  - run MATLAB
  - create final_submit/

Exit code:
  0 — no missing files, no hash mismatches
  1 — one or more missing files or hash mismatches

Usage:
    python pipeline_core/literature/check_asset_manifest.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_PATH = REPO_ROOT / "pipeline_core" / "literature" / "assets_manifest_draft.json"
ASSETS_ROOT = REPO_ROOT / "pipeline_core" / "literature" / "assets"

BLOCK = "=" * 68


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        print(f"ERROR: manifest not found: {MANIFEST_PATH}")
        sys.exit(1)
    with open(MANIFEST_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def _get_manifest_assets(manifest: dict) -> list[dict]:
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        print("WARNING: manifest 'assets' key missing or not a list — no entries to check.")
        return []
    return assets


def main() -> int:
    print(BLOCK)
    print("Literature Asset Manifest Checker")
    print(f"manifest : {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    print(f"assets   : {ASSETS_ROOT.relative_to(REPO_ROOT)}")
    print(BLOCK)
    print()

    manifest = _load_manifest()
    assets = _get_manifest_assets(manifest)

    total = len(assets)
    missing: list[str] = []
    hash_mismatches: list[tuple[str, str, str]] = []
    warnings: list[str] = []
    ok_count = 0

    print(f"Checking {total} manifest entries...\n")

    for entry in assets:
        path_str = entry.get("path")
        if not path_str:
            warnings.append(f"Entry missing 'path' field: {entry}")
            continue

        expected_hash = entry.get("sha256")
        if not expected_hash:
            warnings.append(f"Entry missing 'sha256' field: {path_str}")

        abs_path = REPO_ROOT / path_str

        if not abs_path.exists():
            missing.append(path_str)
            continue

        if expected_hash:
            actual_hash = _sha256(abs_path)
            if actual_hash.lower() != expected_hash.lower():
                hash_mismatches.append((path_str, expected_hash, actual_hash))
            else:
                ok_count += 1
        else:
            ok_count += 1

    # Extra PDFs on disk not in manifest
    manifest_paths = {
        (REPO_ROOT / e["path"]).resolve()
        for e in assets
        if e.get("path")
    }
    disk_pdfs = set(ASSETS_ROOT.rglob("*.pdf"))
    extra_pdfs = sorted(
        str(p.relative_to(REPO_ROOT)) for p in disk_pdfs if p not in manifest_paths
    )

    # --- Summary ---
    print(BLOCK)
    print("RESULTS")
    print(BLOCK)
    print(f"  total manifest entries : {total}")
    print(f"  ok (present + hash OK) : {ok_count}")
    print(f"  missing files          : {len(missing)}")
    print(f"  hash mismatches        : {len(hash_mismatches)}")
    print(f"  extra PDFs on disk     : {len(extra_pdfs)}")
    print(f"  warnings               : {len(warnings)}")
    print()

    if missing:
        print("MISSING FILES:")
        for p in missing:
            print(f"  MISSING  {p}")
        print()

    if hash_mismatches:
        print("HASH MISMATCHES:")
        for path_str, exp, act in hash_mismatches:
            print(f"  MISMATCH {path_str}")
            print(f"           expected : {exp}")
            print(f"           actual   : {act}")
        print()

    if extra_pdfs:
        print("EXTRA PDFs ON DISK (not in manifest):")
        for p in extra_pdfs:
            print(f"  EXTRA    {p}")
        print()

    if warnings:
        print("WARNINGS:")
        for w in warnings:
            print(f"  WARNING  {w}")
        print()

    # Safety boundary assertions
    final_submit = REPO_ROOT / "final_submit"
    if final_submit.exists():
        print("SAFETY ERROR: final_submit/ exists — this script must not create it.")
        return 1

    # Citation status check: ensure we did NOT change any manifest field
    print("Safety checks:")
    print("  citation_ready modified  : no (read-only)")
    print("  formal_reference_allowed : no (read-only)")
    print("  files modified           : no (read-only)")
    print("  final_submit created     : no")
    print()

    has_errors = bool(missing) or bool(hash_mismatches)
    if has_errors:
        print("EXIT: 1 (errors found — see above)")
        return 1

    print("EXIT: 0 (all present entries verified OK)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

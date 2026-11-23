"""Tamper-evident hash tree utilities for X5-F4.1."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from x5_surgical_utils import rel_path, sha256_file, utc_now, write_json


def build_hash_tree(root: Path, output_path: Path, *, include_patterns: list[str] | None = None) -> dict[str, Any]:
    patterns = include_patterns or ["*.json", "*.md", "*.tex", "*.csv", "*.png", "*.jpg"]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in root.rglob(pattern) if path.is_file())
    unique = sorted({path.resolve() for path in files})
    items = [
        {
            "path": rel_path(path),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for path in unique
    ]
    digest = hashlib.sha256()
    for item in items:
        digest.update(item["path"].encode("utf-8"))
        digest.update(item["sha256"].encode("utf-8"))
    payload = {
        "schema_version": "x5_f4_1_hash_tree_v1",
        "created_at": utc_now(),
        "root": rel_path(root),
        "file_count": len(items),
        "root_hash": digest.hexdigest(),
        "items": items,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, payload)
    return payload

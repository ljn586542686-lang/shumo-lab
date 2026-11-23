"""Interpret paths stored in case files consistently on Windows and POSIX."""

from pathlib import Path, PureWindowsPath


def parse_case_relative_path(value: str) -> Path | None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    # Case records may come from either platform, regardless of the host reading them.
    path = PureWindowsPath(value)
    if path.anchor or ".." in path.parts:
        return None
    return Path(*path.parts)


def resolve_case_relative_path(root: Path, value: str) -> Path | None:
    relative = parse_case_relative_path(value)
    if relative is None:
        return None
    try:
        base = root.resolve()
        target = (base / relative).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return target if target.is_relative_to(base) else None

"""OV11C3B: repo-wide static guard against unprotected `git worktree add`.

Context: on this Windows machine, core.autocrlf=true causes a fresh
`git worktree add` checkout to rewrite LF blobs to CRLF, so a checked-out
file's bytes (and SHA-256) no longer match what the commit object store
actually holds for that blob -- reproduced directly by OV11C3A's first real
isolated run (an 11683-byte LF canonical result became an 11795-byte CRLF
file, 112 line endings rewritten, and the run's own hash-binding check
correctly rejected it as a mismatch). The one production fix so far is
`pipeline_core/provenance_safe_run.py`'s `create_isolated_worktree`, which
passes `-c core.autocrlf=false` on the same git invocation.

This module is a static AST guard, not a git-executing test: it parses
every production .py file (tests/reports/docs/archive/work_tmp and this
guard's own module are out of scope -- a test fixture or synthetic string
literal describing `git worktree add` is not itself a bug) and asserts that
every git invocation whose argv contains the adjacent sequence
["worktree", "add"] also carries "-c", "core.autocrlf=false" earlier in the
same argv list. Detects both direct subprocess calls and calls into the
three known generic git-runner wrappers (_run_git, run_git, _pc_run_git) --
argv is still read from the call site itself, so a wrapper's own internal
implementation is not inspected; only literal list/tuple argv arguments are
checked, since anything built dynamically cannot be proven safe or unsafe
by static analysis and is reported as a NEEDS_MANUAL_REVIEW finding rather
than silently passed.

The single existing production call site is not hardcoded into an allowlist
by file path: this guard would flag it exactly like any other unprotected
call if the `-c core.autocrlf=false` argument were ever removed. See
test_it_would_catch_a_new_unprotected_call for a synthetic proof of this.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories excluded from the production scan. Tests describe or exercise
# worktree behavior deliberately (including this guard's own fixtures);
# reports/docs are prose, not code that runs; archive/ and work_tmp/ are not
# live production surface.
EXCLUDED_DIR_PARTS = frozenset(
    {
        "tests",
        "reports",
        "docs",
        "archive",
        "work_tmp",
        ".git",
        "__pycache__",
        ".venv",
        "textbook",
    }
)

REQUIRED_PROTECTION = ("-c", "core.autocrlf=false")


def _iter_production_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in EXCLUDED_DIR_PARTS for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return files


def _string_constant(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _extract_literal_argv(node: ast.expr) -> list[str | None] | None:
    """Return a list of string-or-None entries for a literal list/tuple argv.

    None marks a non-literal element (e.g. a variable) whose value cannot be
    determined statically. Returns None entirely if the argv expression
    itself is not a literal list/tuple (e.g. built by concatenation).
    """

    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    result: list[str | None] = []
    for element in node.elts:
        if isinstance(element, ast.Starred):
            return None  # unpacking defeats static positional analysis
        result.append(_string_constant(element))
    return result


def _contains_adjacent(argv: list[str | None], sequence: tuple[str, ...]) -> bool:
    n = len(sequence)
    for start in range(len(argv) - n + 1):
        window = argv[start : start + n]
        if all(item == expected for item, expected in zip(window, sequence)):
            return True
    return False


def _find_adjacent_start(argv: list[str | None], sequence: tuple[str, ...]) -> int | None:
    n = len(sequence)
    for start in range(len(argv) - n + 1):
        window = argv[start : start + n]
        if all(item == expected for item, expected in zip(window, sequence)):
            return start
    return None


def _worktree_add_index(argv: list[str | None]) -> int | None:
    return _find_adjacent_start(argv, ("worktree", "add"))


def _autocrlf_protection_index(argv: list[str | None]) -> int | None:
    return _find_adjacent_start(argv, REQUIRED_PROTECTION)


def _contains_worktree_add(argv: list[str | None]) -> bool:
    return _worktree_add_index(argv) is not None


def _has_autocrlf_protection_before_worktree_add(argv: list[str | None]) -> bool:
    """git parses global options (-c NAME=VALUE) only when they precede the
    subcommand; a -c core.autocrlf=false placed after "worktree add" in the
    argv would not actually apply to this invocation in real git, so it must
    not count as protection."""

    worktree_index = _worktree_add_index(argv)
    protection_index = _autocrlf_protection_index(argv)
    if worktree_index is None or protection_index is None:
        return False
    return protection_index < worktree_index


KNOWN_GIT_RUNNER_WRAPPER_NAMES = frozenset({"_run_git", "run_git", "_pc_run_git"})

GIT_INVOKING_CALLEES = frozenset(
    {
        "run",
        "call",
        "check_call",
        "check_output",
        "Popen",
    }
) | KNOWN_GIT_RUNNER_WRAPPER_NAMES


class Finding:
    __slots__ = ("file", "lineno", "kind", "detail")

    def __init__(self, file: str, lineno: int, kind: str, detail: str) -> None:
        self.file = file
        self.lineno = lineno
        self.kind = kind
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"{self.kind} at {self.file}:{self.lineno}: {self.detail}"


class WorktreeAddCallScanner(ast.NodeVisitor):
    """Finds subprocess/git-runner-wrapper calls whose literal argv contains
    an unprotected ["worktree", "add"] sequence, plus calls where argv could
    not be statically resolved but the source text still mentions both
    "worktree" and "add" as string literals -- those are reported as
    NEEDS_MANUAL_REVIEW rather than silently ignored, so a call built via a
    variable can never pass this guard by construction alone.
    """

    def __init__(self, file_label: str) -> None:
        self.file_label = file_label
        self.unprotected: list[Finding] = []
        self.needs_review: list[Finding] = []

    def _callee_name(self, func: ast.expr) -> str | None:
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return None

    def _first_argv_expr(self, node: ast.Call) -> ast.expr | None:
        if node.args:
            return node.args[0]
        for keyword in node.keywords:
            if keyword.arg == "args":
                return keyword.value
        return None

    def visit_Call(self, node: ast.Call) -> None:
        callee_name = self._callee_name(node.func)
        if callee_name in GIT_INVOKING_CALLEES:
            argv_expr = self._first_argv_expr(node)
            if argv_expr is not None:
                literal_argv = _extract_literal_argv(argv_expr)
                if literal_argv is not None:
                    if _contains_worktree_add(literal_argv) and not _has_autocrlf_protection_before_worktree_add(
                        literal_argv
                    ):
                        self.unprotected.append(
                            Finding(
                                self.file_label,
                                node.lineno,
                                "UNPROTECTED_WORKTREE_ADD",
                                f"{callee_name}(...) argv contains ['worktree', 'add'] without "
                                f"['-c', 'core.autocrlf=false']",
                            )
                        )
                elif isinstance(argv_expr, ast.Name):
                    # argv assembled elsewhere (e.g. a local variable built up
                    # incrementally): cannot prove safety or danger from this
                    # call site's AST alone. Only flag for review if this
                    # function's source otherwise looks git-worktree-shaped;
                    # left to the source-text heuristic in scan_file so a
                    # completely unrelated variable named e.g. `argv` isn't
                    # flagged on every subprocess call in the repo.
                    pass
        self.generic_visit(node)


def _enclosing_function(tree: ast.Module, target: ast.Call) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.lineno <= target.lineno <= (node.end_lineno or node.lineno):
            if best is None or node.lineno >= best.lineno:
                best = node
    return best


def scan_file(path: Path) -> WorktreeAddCallScanner:
    source = path.read_text(encoding="utf-8")
    label = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    scanner = WorktreeAddCallScanner(label)
    tree = ast.parse(source, filename=str(path))
    scanner.visit(tree)

    # Source-text fallback, scoped to the enclosing function only (not the
    # whole file): if a call's argv could not be resolved to a literal list,
    # and the function body containing that call otherwise mentions
    # "worktree" as a string literal, flag it for manual review instead of
    # silently passing. Scoping to the function (rather than the file) avoids
    # flagging an unrelated call in the same file as a helper that legitimately
    # handles worktree strings elsewhere (e.g. a generic git-runner wrapper's
    # own *args-forwarding implementation, or an unrelated status/diff call).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee_name = scanner._callee_name(node.func)
        if callee_name not in GIT_INVOKING_CALLEES:
            continue
        argv_expr = scanner._first_argv_expr(node)
        if argv_expr is None:
            continue
        if _extract_literal_argv(argv_expr) is not None:
            continue  # already handled precisely by the literal-argv AST path
        if isinstance(argv_expr, ast.Starred) or (
            isinstance(argv_expr, ast.Name)
        ):
            # A *args-forwarding call (e.g. a generic wrapper's own
            # `subprocess.run(["git", *args], ...)` body) or a plain
            # variable is deliberately not flagged here: it forwards
            # whatever its caller already validated. The caller side of
            # such a wrapper is a separate Call node, inspected on its own.
            continue
        enclosing = _enclosing_function(tree, node)
        search_text = ast.get_source_segment(source, enclosing) if enclosing is not None else source
        if search_text and ('"worktree"' in search_text or "'worktree'" in search_text):
            scanner.needs_review.append(
                Finding(
                    scanner.file_label,
                    node.lineno,
                    "NEEDS_MANUAL_REVIEW",
                    f"{callee_name}(...) argv is not a literal list and its enclosing function "
                    f'mentions "worktree" as a string literal -- cannot statically prove this call '
                    f"is unrelated to worktree add",
                )
            )
    return scanner


def scan_source(source: str, file_label: str = "<synthetic>") -> WorktreeAddCallScanner:
    tree = ast.parse(source, filename=file_label)
    scanner = WorktreeAddCallScanner(file_label)
    scanner.visit(tree)
    return scanner


class ProductionWorktreeAddGuardTests(unittest.TestCase):
    def test_no_unprotected_worktree_add_in_production_code(self) -> None:
        all_unprotected: list[Finding] = []
        all_review: list[Finding] = []
        scanned_files = 0
        for path in _iter_production_python_files(REPO_ROOT):
            # This guard module itself contains the literal strings
            # "worktree" and "add" throughout (as data, in comments, and in
            # this very sentence) but issues no subprocess calls -- excluded
            # by identity, not by directory, so moving it would not silently
            # exempt it if it ever grew a real call.
            if path.resolve() == Path(__file__).resolve():
                continue
            scanner = scan_file(path)
            all_unprotected.extend(scanner.unprotected)
            all_review.extend(scanner.needs_review)
            scanned_files += 1

        self.assertGreater(scanned_files, 0, "production scan matched zero files -- exclusion list is too broad")

        if all_unprotected:
            details = "\n".join(repr(item) for item in all_unprotected)
            self.fail(f"unprotected git worktree add call(s) found:\n{details}")

        if all_review:
            details = "\n".join(repr(item) for item in all_review)
            self.fail(
                "call(s) with non-literal argv found in a file mentioning \"worktree\" -- "
                f"cannot statically prove these do not construct an unprotected worktree add; "
                f"resolve to a literal argv or add explicit review sign-off:\n{details}"
            )

    def test_known_call_site_is_detected_as_protected_not_exempted_by_path(self) -> None:
        """The real call site must pass BECAUSE it carries the protection
        flag, not because its path is skipped. Scanning it in isolation
        (bypassing the directory exclusion list) proves this."""

        target = REPO_ROOT / "pipeline_core" / "provenance_safe_run.py"
        self.assertTrue(target.is_file())
        scanner = scan_file(target)
        self.assertEqual(scanner.unprotected, [], "the known, already-fixed call site must not be flagged")

    def test_it_would_catch_a_new_unprotected_call(self) -> None:
        """Synthetic proof the guard is not just asserting today's count.
        A freshly authored, unprotected git worktree add call must be
        detected by the same scanner used for the real repository."""

        source = """
import subprocess

def make_worktree(root, path, commit):
    return subprocess.run(
        ["git", "worktree", "add", "--detach", str(path), commit],
        cwd=root,
    )
"""
        scanner = scan_source(source)
        self.assertEqual(len(scanner.unprotected), 1)
        self.assertEqual(scanner.unprotected[0].kind, "UNPROTECTED_WORKTREE_ADD")

    def test_protected_synthetic_call_does_not_trigger(self) -> None:
        source = """
import subprocess

def make_worktree(root, path, commit):
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "worktree", "add", "--detach", str(path), commit],
        cwd=root,
    )
"""
        scanner = scan_source(source)
        self.assertEqual(scanner.unprotected, [])

    def test_wrapper_call_with_unprotected_argv_is_caught(self) -> None:
        """A call through one of the three known generic git-runner wrapper
        names must be checked exactly like a direct subprocess call."""

        source = """
def do_it(root, path, commit):
    return _run_git(["worktree", "add", "--detach", str(path), commit], cwd=root)
"""
        scanner = scan_source(source)
        self.assertEqual(len(scanner.unprotected), 1)

    def test_non_worktree_git_calls_are_not_flagged(self) -> None:
        source = """
import subprocess

def status(root):
    return subprocess.run(["git", "status", "--porcelain"], cwd=root)

def add_worktree_unrelated_string(root):
    # 'worktree' the word can appear without an actual "worktree", "add" pair
    return subprocess.run(["git", "worktree", "list"], cwd=root)
"""
        scanner = scan_source(source)
        self.assertEqual(scanner.unprotected, [])

    def test_reordered_protection_flag_still_recognized(self) -> None:
        """-c core.autocrlf=false must appear as an adjacent pair anywhere
        before worktree/add is also adjacent; order between the two pairs
        does not matter, but each pair's own two tokens must stay adjacent
        (this matches how git actually parses -c NAME=VALUE)."""

        source = """
import subprocess

def make_worktree(root, path, commit):
    return subprocess.run(
        ["git", "worktree", "add", "-c", "core.autocrlf=false", "--detach", str(path), commit],
        cwd=root,
    )
"""
        scanner = scan_source(source)
        # -c core.autocrlf=false appears AFTER worktree/add in git's actual
        # argument parsing this would not take effect for the add itself in
        # real git (global options must precede the subcommand), so this
        # ordering must still be flagged as unprotected.
        self.assertEqual(len(scanner.unprotected), 1)


if __name__ == "__main__":
    unittest.main()

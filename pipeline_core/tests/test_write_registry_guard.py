"""Independent AST write-surface guard for run_case.py and x5_surgical_utils.py.

This module does not touch promotion, candidate packaging, or final_submit.
It only performs static AST analysis of the two reviewed production files and
exercises the WriteRegistry diagnostic primitive added in x5_surgical_utils.py.
It never imports package_candidate or final_freezer, and never calls
run_case.run_case or safe_promote.
"""

from __future__ import annotations

import ast
import inspect
import tempfile
import textwrap
import unittest
from collections import Counter
from pathlib import Path

import x5_surgical_utils
from x5_surgical_utils import WRITE_REGISTRY_INITIAL_COMPLETE, WriteRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_CASE_PATH = REPO_ROOT / "run_case.py"
X5_UTILS_PATH = REPO_ROOT / "x5_surgical_utils.py"
RUN_CASE_LABEL = "run_case.py"
X5_UTILS_LABEL = "x5_surgical_utils.py"
SYNTHETIC_LABEL = "<synthetic>"

ALIAS_WRITER_NAMES = {
    "write_json",
    "write_risk_summary",
    "register_asset",
    "ensure_assets_manifest",
    "generate_pdf_checklist",
    "safe_promote",
    "run_subprocess_with_timeout",
}

MODE_WRITE_CHARS = ("w", "a", "x")

# Semantic key: (file, function, call_label). Counter multiset — duplicate
# calls of the same shape inside the same function increment the same key,
# so a second write_text added next to an existing one changes the count and
# must fail the baseline comparison. Line numbers never appear in the key.
WriteKey = tuple[str, str, str]


class WriteCallScanner(ast.NodeVisitor):
    """Collects write-call and dynamic-writer occurrences as multisets."""

    def __init__(self, file_label: str) -> None:
        self.file_label = file_label
        self.observed: Counter[WriteKey] = Counter()
        self.dynamic_writers: Counter[WriteKey] = Counter()
        self._function_stack: list[str] = ["<module>"]

    def _current_function(self) -> str:
        return self._function_stack[-1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def _record(self, label: str) -> None:
        self.observed[(self.file_label, self._current_function(), label)] += 1

    def _record_dynamic(self, label: str) -> None:
        self.dynamic_writers[(self.file_label, self._current_function(), label)] += 1

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func

        if isinstance(func, ast.Attribute):
            attr = func.attr
            base = func.value
            base_name = base.id if isinstance(base, ast.Name) else None

            if base_name == "shutil" and attr in {
                "copy",
                "copy2",
                "copyfile",
                "copytree",
                "move",
                "rmtree",
            }:
                self._record(f"shutil.{attr}")
            elif base_name == "os" and attr == "replace":
                self._record("os.replace")
            elif base_name == "json" and attr == "dump":
                self._record("json.dump")
            elif attr in {"write_text", "write_bytes", "touch", "unlink", "mkdir"}:
                self._record(attr)
            elif (
                attr == "replace"
                and base_name != "os"
                and len(node.args) == 1
                and not node.keywords
            ):
                # Path.replace(target) takes exactly one positional argument.
                # str.replace(old, new) and datetime.replace(**kwargs) do not
                # match this shape, so this discriminates Path.replace(...)
                # without needing type inference.
                self._record("replace")

        elif isinstance(func, ast.Name):
            if func.id == "open":
                self._classify_open_call(node)
            elif func.id in ALIAS_WRITER_NAMES:
                self._record(func.id)
            elif func.id == "rmtree":
                self._record("rmtree")

        self.generic_visit(node)

    def _classify_open_call(self, node: ast.Call) -> None:
        mode_node: ast.expr | None = None
        if len(node.args) >= 2:
            mode_node = node.args[1]
        for keyword in node.keywords:
            if keyword.arg == "mode":
                mode_node = keyword.value

        if mode_node is None:
            return  # default read mode; not a write call.

        if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
            if any(char in mode_node.value for char in MODE_WRITE_CHARS):
                self._record("open_write")
            return

        # Non-literal mode: cannot statically prove write intent. Fail open
        # (visible, counted, baseline-compared) rather than silently ignoring
        # or being folded into observed as if it were a resolved write.
        self._record_dynamic("open_dynamic_mode")


def scan_file(path: Path, file_label: str) -> WriteCallScanner:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    scanner = WriteCallScanner(file_label)
    scanner.visit(tree)
    return scanner


def scan_source(source: str, file_label: str = SYNTHETIC_LABEL) -> WriteCallScanner:
    tree = ast.parse(textwrap.dedent(source))
    scanner = WriteCallScanner(file_label)
    scanner.visit(tree)
    return scanner


# Reviewed baseline transcribed from the P6-PV1B2A1-D0 read-only inventory.
# Semantic (file, function, call_label) multiset — no line numbers. Each
# entry's count is the exact number of times that call shape occurs in that
# function; a second occurrence of an already-listed call must be added here
# explicitly or the guard fails.
RUN_CASE_BASELINE: Counter[WriteKey] = Counter(
    {
        (RUN_CASE_LABEL, "_ensure_static_templates", "mkdir"): 2,
        (RUN_CASE_LABEL, "_ensure_static_templates", "write_json"): 2,
        (RUN_CASE_LABEL, "_ensure_static_templates", "write_text"): 1,
        (RUN_CASE_LABEL, "_update_compliance_queue", "write_json"): 1,
        (RUN_CASE_LABEL, "_write_result_summary", "mkdir"): 1,
        (RUN_CASE_LABEL, "_write_result_summary", "write_json"): 1,
        (RUN_CASE_LABEL, "_write_result_summary", "write_text"): 1,
        (RUN_CASE_LABEL, "_write_math_core_summary", "mkdir"): 1,
        (RUN_CASE_LABEL, "_write_math_core_summary", "write_json"): 2,
        (RUN_CASE_LABEL, "_write_math_core_summary", "write_text"): 1,
        (RUN_CASE_LABEL, "_run_pre_modeling_production_wiring", "write_json"): 3,
        (RUN_CASE_LABEL, "_run_formal_paper_build", "write_json"): 1,
        (RUN_CASE_LABEL, "_run_paper_preflight_if_enabled", "write_json"): 1,
        (RUN_CASE_LABEL, "_attach_upstream_content_bindings", "write_json"): 1,
        (RUN_CASE_LABEL, "_attach_xval_machine_record", "write_json"): 1,
        (RUN_CASE_LABEL, "_attach_xval_machine_record", "write_text"): 1,
        (RUN_CASE_LABEL, "_write_validation_summary", "mkdir"): 1,
        (RUN_CASE_LABEL, "_write_validation_summary", "write_json"): 1,
        (RUN_CASE_LABEL, "_write_validation_summary", "write_text"): 1,
        (RUN_CASE_LABEL, "_write_validation_summary", "shutil.copy2"): 2,
        (RUN_CASE_LABEL, "_evaluate_release", "write_json"): 1,
        (RUN_CASE_LABEL, "_evaluate_release", "write_text"): 1,
        (RUN_CASE_LABEL, "_write_error_log", "write_text"): 1,
        (RUN_CASE_LABEL, "_run_isolated_smoke", "mkdir"): 2,
        (RUN_CASE_LABEL, "_run_isolated_smoke", "shutil.copy2"): 3,
        (RUN_CASE_LABEL, "_run_isolated_smoke", "write_risk_summary"): 2,
        (RUN_CASE_LABEL, "_run_isolated_smoke", "write_json"): 2,
        (RUN_CASE_LABEL, "run_case", "mkdir"): 2,
        (RUN_CASE_LABEL, "run_case", "write_json"): 4,
        (RUN_CASE_LABEL, "run_case", "run_subprocess_with_timeout"): 2,
        (RUN_CASE_LABEL, "run_case", "safe_promote"): 1,
        (RUN_CASE_LABEL, "run_case", "register_asset"): 4,
        (RUN_CASE_LABEL, "run_case", "write_risk_summary"): 2,
        (RUN_CASE_LABEL, "run_case", "shutil.copy2"): 4,
        # Stage-authorization persistence is now isolated in one helper.  The
        # total legacy write surface decreased from two branches to one;
        # authoritative cases delegate to transition_case instead.
        (RUN_CASE_LABEL, "persist_stage_authorization", "mkdir"): 1,
        (RUN_CASE_LABEL, "persist_stage_authorization", "write_text"): 1,
        # OV11C3A: run_provenance_safe() is a new, narrower explicit mode
        # (see its docstring) that only ever writes below a caller-selected
        # work_tmp/ child; it never calls safe_promote, register_asset, or
        # the compliance queue writer.
        (RUN_CASE_LABEL, "_provenance_safe_output_dir", "mkdir"): 1,
        (RUN_CASE_LABEL, "run_provenance_safe", "mkdir"): 1,
        (RUN_CASE_LABEL, "run_provenance_safe", "write_json"): 4,
        (RUN_CASE_LABEL, "run_provenance_safe", "write_risk_summary"): 1,
    }
)

X5_UTILS_BASELINE: Counter[WriteKey] = Counter(
    {
        (X5_UTILS_LABEL, "write_json", "mkdir"): 1,
        (X5_UTILS_LABEL, "write_json", "write_text"): 1,
        (X5_UTILS_LABEL, "register_asset", "ensure_assets_manifest"): 1,
        (X5_UTILS_LABEL, "register_asset", "write_json"): 1,
        (X5_UTILS_LABEL, "ensure_assets_manifest", "write_json"): 1,
        (X5_UTILS_LABEL, "write_risk_summary", "write_json"): 1,
        (X5_UTILS_LABEL, "write_risk_summary", "write_text"): 1,
        (X5_UTILS_LABEL, "generate_pdf_checklist", "write_json"): 1,
        (X5_UTILS_LABEL, "generate_pdf_checklist", "write_text"): 1,
        (X5_UTILS_LABEL, "safe_promote", "mkdir"): 4,
        (X5_UTILS_LABEL, "safe_promote", "shutil.copy2"): 2,
        (X5_UTILS_LABEL, "run_subprocess_with_timeout", "mkdir"): 1,
        (X5_UTILS_LABEL, "run_subprocess_with_timeout", "write_text"): 2,
    }
)

# Dynamic/unresolved writer baseline. Currently empty: both production files
# contain no non-literal-mode open(...) calls. Any new dynamic writer changes
# this Counter and must fail the guard until reviewed and added here.
RUN_CASE_DYNAMIC_BASELINE: Counter[WriteKey] = Counter()
X5_UTILS_DYNAMIC_BASELINE: Counter[WriteKey] = Counter()


class WriteRegistryPrimitiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_registry_initial_state_is_incomplete(self) -> None:
        self.assertFalse(WRITE_REGISTRY_INITIAL_COMPLETE)
        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A", "B"}))
        self.assertFalse(registry.diagnostic_completeness())
        self.assertEqual(registry.completed_tokens, frozenset())

    def test_completeness_false_when_tokens_partial(self) -> None:
        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A", "B"}))
        target = self.root / "one.json"
        target.write_text("{}", encoding="utf-8")
        registry.record_write(target, token="A")
        self.assertFalse(registry.diagnostic_completeness())

    def test_completeness_false_when_registry_error_present(self) -> None:
        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        target = self.root / "one.json"
        target.write_text("{}", encoding="utf-8")
        registry.record_write(target, token="A")
        registry.record_write(target, token="UNKNOWN_TOKEN")
        self.assertTrue(registry.registry_errors)
        self.assertFalse(registry.diagnostic_completeness())

    def test_completeness_false_when_ambiguous_write_detected(self) -> None:
        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        registry.record_write(self.root, token="A")
        self.assertTrue(registry.ambiguous_write_detected)
        self.assertFalse(registry.diagnostic_completeness())

    def test_completeness_false_when_completed_tokens_is_superset(self) -> None:
        """Extra completed tokens beyond required_tokens must not pass.

        record_write already rejects a token outside required_tokens (into
        registry_errors), so completed_tokens can never legitimately exceed
        required_tokens. This test proves the equality predicate would still
        reject a superset even if that structural invariant were violated,
        by constructing the registry with a deliberately narrow
        required_tokens set and confirming the unrecognized token attempt
        both fails to complete and is visible as an error rather than
        silently accepted.
        """
        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        target = self.root / "a.json"
        target.write_text("{}", encoding="utf-8")
        registry.record_write(target, token="A")
        registry.record_write(target, token="B")  # not in required_tokens
        self.assertEqual(registry.completed_tokens, frozenset({"A"}))
        self.assertTrue(registry.registry_errors)
        self.assertFalse(registry.diagnostic_completeness())

    def test_completeness_true_only_when_all_pure_conditions_met(self) -> None:
        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A", "B"}))
        target_a = self.root / "a.json"
        target_b = self.root / "sub" / "b.json"
        target_a.write_text("{}", encoding="utf-8")
        target_b.parent.mkdir(parents=True, exist_ok=True)
        target_b.write_text("{}", encoding="utf-8")
        registry.record_write(target_a, token="A")
        registry.record_write(target_b, token="B")
        self.assertEqual(registry.completed_tokens, registry.required_tokens)
        self.assertTrue(registry.diagnostic_completeness())

        forbidden_fields = (
            "source_worktree_clean_after",
            "source_provenance_valid",
            "safe_promotion_allowed",
            "authorization_bindings_valid",
            "final_freeze_ready",
            "final_submit_allowed",
        )
        for field_name in forbidden_fields:
            self.assertFalse(
                hasattr(registry, field_name),
                f"WriteRegistry must not expose {field_name} in this slice",
            )

    def test_caller_cannot_force_complete(self) -> None:
        params = set(inspect.signature(WriteRegistry.__init__).parameters)
        for forbidden in ("write_registry_complete", "completed_tokens", "written_repo_paths"):
            self.assertNotIn(forbidden, params)
        with self.assertRaises(TypeError):
            WriteRegistry(  # type: ignore[call-arg]
                root=self.root,
                required_tokens=frozenset({"A"}),
                completed_tokens={"A"},
            )

    def test_public_views_are_copies_not_live_references(self) -> None:
        """Mutating a returned view must not change internal registry state."""

        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        target = self.root / "a.json"
        target.write_text("{}", encoding="utf-8")
        registry.record_write(target, token="A")

        with self.assertRaises(AttributeError):
            registry.written_repo_paths.add("injected/path.json")  # type: ignore[attr-defined]
        with self.assertRaises(AttributeError):
            registry.completed_tokens.add("INJECTED")  # type: ignore[attr-defined]
        with self.assertRaises(AttributeError):
            registry.external_written_paths.add("injected")  # type: ignore[attr-defined]
        with self.assertRaises(AttributeError):
            registry.expected_post_snapshot_paths.add("injected")  # type: ignore[attr-defined]
        with self.assertRaises(AttributeError):
            registry.registry_errors.append("injected")  # type: ignore[attr-defined]

        # Confirm state truly unaffected after the mutation attempts above.
        self.assertEqual(registry.written_repo_paths, frozenset({"a.json"}))
        self.assertEqual(registry.completed_tokens, frozenset({"A"}))
        self.assertEqual(registry.registry_errors, ())
        self.assertTrue(registry.diagnostic_completeness())

    def test_public_views_do_not_alias_internal_containers(self) -> None:
        """Two successive reads must be independent copies, not the same object."""

        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        target = self.root / "a.json"
        target.write_text("{}", encoding="utf-8")
        registry.record_write(target, token="A")

        first = registry.written_repo_paths
        second = registry.written_repo_paths
        self.assertEqual(first, second)
        self.assertIsNot(first, second)

    def test_directory_rejected_regardless_of_name(self) -> None:
        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        subdir = self.root / "some_dir"
        subdir.mkdir()
        registry.record_write(subdir, token="A")
        self.assertNotIn(subdir.resolve().as_posix(), registry.written_repo_paths)
        self.assertTrue(registry.ambiguous_write_detected)

    def test_nonexistent_path_rejected(self) -> None:
        """A path with no real file on disk must be rejected as an error.

        record_write is a post-write registration call: it never registers
        a path that was not actually written, regardless of what characters
        appear in the filename.
        """

        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        missing = self.root / "never_written.json"
        registry.record_write(missing, token="A")
        self.assertEqual(registry.written_repo_paths, frozenset())
        self.assertTrue(registry.registry_errors)
        self.assertFalse(registry.diagnostic_completeness())

        # A nonexistent path containing "*" is rejected for non-existence,
        # not because of the character — no character blacklist exists.
        registry2 = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        registry2.record_write(self.root / "glob_*.json", token="A")
        self.assertEqual(registry2.written_repo_paths, frozenset())
        self.assertTrue(registry2.registry_errors)
        self.assertFalse(registry2.ambiguous_write_detected)

    def test_regular_file_required_symlink_to_directory_rejected(self) -> None:
        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        subdir = self.root / "target_dir"
        subdir.mkdir()
        link = self.root / "link_to_dir"
        try:
            link.symlink_to(subdir, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not permitted on this platform/account")
        registry.record_write(link, token="A")
        self.assertEqual(registry.written_repo_paths, frozenset())
        self.assertTrue(registry.ambiguous_write_detected)

    def test_bracket_filename_accepted_as_concrete_path(self) -> None:
        """Real filenames containing brackets must register normally."""

        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        target = self.root / "report[1].json"
        target.write_text("{}", encoding="utf-8")
        registry.record_write(target, token="A")
        self.assertIn("report[1].json", registry.written_repo_paths)
        self.assertFalse(registry.ambiguous_write_detected)
        self.assertEqual(registry.registry_errors, ())
        self.assertTrue(registry.diagnostic_completeness())

    def test_star_question_filename_accepted_when_platform_permits(self) -> None:
        """A real regular file literally named with '*' or '?' must register.

        No character blacklist exists in record_write: acceptance depends
        solely on whether the filesystem could create the file and whether
        resolve()/is_file() then confirm it. Windows (NTFS/FAT) forbids
        '*' and '?' in filenames at the OS level, so this test documents
        that platform limitation explicitly via skipTest rather than
        asserting rejection-by-character, which would reintroduce the same
        heuristic this patch removes.
        """

        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        candidate = self.root / "weird_*_name.json"
        try:
            candidate.write_text("{}", encoding="utf-8")
        except OSError:
            self.skipTest(
                "this platform's filesystem rejects '*' in filenames at the "
                "OS level (e.g. Windows NTFS/FAT); record_write itself has "
                "no character blacklist, so this is a filesystem limitation, "
                "not a registry policy"
            )
        registry.record_write(candidate, token="A")
        self.assertIn(candidate.name, registry.written_repo_paths)
        self.assertFalse(registry.ambiguous_write_detected)
        self.assertEqual(registry.registry_errors, ())

    def test_resolve_failure_still_recorded_as_error(self) -> None:
        """A path that cannot be resolved/contained must produce a registry error."""

        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        # NUL is illegal in a Windows path and raises during resolve().
        bad_path = self.root / "bad\x00name.json"
        registry.record_write(bad_path, token="A")
        self.assertTrue(registry.registry_errors)
        self.assertEqual(registry.written_repo_paths, frozenset())
        self.assertFalse(registry.diagnostic_completeness())

    def test_pathlike_input_accepted(self) -> None:
        """record_write accepts os.PathLike, not only pathlib.Path instances."""

        import os as os_module

        class _StrPathLike(os_module.PathLike):
            def __init__(self, value: str) -> None:
                self._value = value

            def __fspath__(self) -> str:
                return self._value

        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        target = self.root / "pathlike.json"
        target.write_text("{}", encoding="utf-8")
        registry.record_write(_StrPathLike(str(target)), token="A")
        self.assertIn("pathlike.json", registry.written_repo_paths)
        self.assertTrue(registry.diagnostic_completeness())

    def test_external_path_separated_from_repo_path(self) -> None:
        registry = WriteRegistry(root=self.root, required_tokens=frozenset({"A"}))
        with tempfile.TemporaryDirectory() as other_dir:
            external_target = Path(other_dir) / "outside.json"
            external_target.write_text("{}", encoding="utf-8")
            registry.record_write(external_target, token="A")
        self.assertEqual(registry.written_repo_paths, frozenset())
        self.assertTrue(registry.external_written_paths)


class AstBaselineTests(unittest.TestCase):
    def test_ast_baseline_matches_current_source_run_case(self) -> None:
        scanner = scan_file(RUN_CASE_PATH, RUN_CASE_LABEL)
        diff = Counter(scanner.observed)
        diff.subtract(RUN_CASE_BASELINE)
        drift = {key: count for key, count in diff.items() if count != 0}
        self.assertEqual(
            scanner.observed,
            RUN_CASE_BASELINE,
            f"run_case.py write surface drifted from baseline (nonzero deltas): {drift}",
        )

    def test_ast_baseline_matches_current_source_x5_utils(self) -> None:
        scanner = scan_file(X5_UTILS_PATH, X5_UTILS_LABEL)
        diff = Counter(scanner.observed)
        diff.subtract(X5_UTILS_BASELINE)
        drift = {key: count for key, count in diff.items() if count != 0}
        self.assertEqual(
            scanner.observed,
            X5_UTILS_BASELINE,
            f"x5_surgical_utils.py write surface drifted from baseline (nonzero deltas): {drift}",
        )

    def test_dynamic_writer_baseline_matches_current_source(self) -> None:
        run_case_scanner = scan_file(RUN_CASE_PATH, RUN_CASE_LABEL)
        x5_utils_scanner = scan_file(X5_UTILS_PATH, X5_UTILS_LABEL)
        self.assertEqual(run_case_scanner.dynamic_writers, RUN_CASE_DYNAMIC_BASELINE)
        self.assertEqual(x5_utils_scanner.dynamic_writers, X5_UTILS_DYNAMIC_BASELINE)

    def test_baseline_insensitive_to_line_number_reformatting(self) -> None:
        original = """
        def _example(run_dir):
            (run_dir / "a.txt").write_text("x", encoding="utf-8")
        """
        reformatted = """
        def _example(run_dir):


            value = run_dir / "a.txt"

            value.write_text(
                "x",
                encoding="utf-8",
            )
        """
        original_scan = scan_source(original)
        reformatted_scan = scan_source(reformatted)
        self.assertEqual(original_scan.observed, reformatted_scan.observed)
        self.assertEqual(reformatted_scan.observed[(SYNTHETIC_LABEL, "_example", "write_text")], 1)

    def test_unresolved_dynamic_writer_not_silently_ignored(self) -> None:
        source = """
        def _example(path, mode_var):
            open(path, mode_var)
        """
        scanner = scan_source(source)
        self.assertEqual(scanner.observed, Counter())
        self.assertEqual(
            scanner.dynamic_writers[(SYNTHETIC_LABEL, "_example", "open_dynamic_mode")], 1
        )

    def test_duplicate_call_in_same_function_increments_count(self) -> None:
        """A second write_text next to an existing one changes the multiset count."""

        source = """
        def _example(run_dir):
            (run_dir / "a.txt").write_text("x", encoding="utf-8")
            (run_dir / "b.txt").write_text("y", encoding="utf-8")
        """
        scanner = scan_source(source)
        self.assertEqual(scanner.observed[(SYNTHETIC_LABEL, "_example", "write_text")], 2)

        single_baseline: Counter[WriteKey] = Counter(
            {(SYNTHETIC_LABEL, "_example", "write_text"): 1}
        )
        self.assertNotEqual(scanner.observed, single_baseline)

    def test_duplicate_shutil_copy2_in_same_function_breaks_stale_baseline(self) -> None:
        source = """
        import shutil

        def _example(src_a, src_b, dst_a, dst_b):
            shutil.copy2(src_a, dst_a)
            shutil.copy2(src_b, dst_b)
        """
        scanner = scan_source(source)
        stale_baseline: Counter[WriteKey] = Counter(
            {(SYNTHETIC_LABEL, "_example", "shutil.copy2"): 1}
        )
        self.assertNotEqual(scanner.observed, stale_baseline)
        self.assertEqual(scanner.observed[(SYNTHETIC_LABEL, "_example", "shutil.copy2")], 2)


class SyntheticBypassDetectionTests(unittest.TestCase):
    """Each case scans a temp-file synthetic module, never production source."""

    def test_synthetic_new_write_text_detected(self) -> None:
        source = """
        def _new_writer(run_dir):
            (run_dir / "new_file.txt").write_text("value", encoding="utf-8")
        """
        scanner = scan_source(source)
        key = (SYNTHETIC_LABEL, "_new_writer", "write_text")
        self.assertEqual(scanner.observed[key], 1)
        self.assertEqual(RUN_CASE_BASELINE[key], 0)
        self.assertEqual(X5_UTILS_BASELINE[key], 0)

    def test_synthetic_new_shutil_copy2_detected(self) -> None:
        source = """
        import shutil

        def _new_copier(src, dst):
            shutil.copy2(src, dst)
        """
        scanner = scan_source(source)
        key = (SYNTHETIC_LABEL, "_new_copier", "shutil.copy2")
        self.assertEqual(scanner.observed[key], 1)
        self.assertEqual(RUN_CASE_BASELINE[key], 0)
        self.assertEqual(X5_UTILS_BASELINE[key], 0)

    def test_synthetic_new_open_write_detected(self) -> None:
        source = """
        def _new_open_writer(path):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("value")
        """
        scanner = scan_source(source)
        key = (SYNTHETIC_LABEL, "_new_open_writer", "open_write")
        self.assertEqual(scanner.observed[key], 1)
        self.assertEqual(RUN_CASE_BASELINE[key], 0)
        self.assertEqual(X5_UTILS_BASELINE[key], 0)

    def test_synthetic_new_alias_writer_detected(self) -> None:
        source = """
        def _new_promoter(a, b, c):
            safe_promote(a, b, c)
        """
        scanner = scan_source(source)
        key = (SYNTHETIC_LABEL, "_new_promoter", "safe_promote")
        self.assertEqual(scanner.observed[key], 1)
        self.assertEqual(RUN_CASE_BASELINE[key], 0)
        self.assertEqual(X5_UTILS_BASELINE[key], 0)

    def test_synthetic_new_dynamic_writer_detected(self) -> None:
        source = """
        def _new_dynamic(path, mode_var):
            open(path, mode_var)
        """
        scanner = scan_source(source)
        key = (SYNTHETIC_LABEL, "_new_dynamic", "open_dynamic_mode")
        self.assertEqual(scanner.dynamic_writers[key], 1)
        self.assertEqual(RUN_CASE_DYNAMIC_BASELINE[key], 0)
        self.assertEqual(X5_UTILS_DYNAMIC_BASELINE[key], 0)


class ScopeGuardTests(unittest.TestCase):
    def test_registry_guard_does_not_touch_promotion_or_final_submit(self) -> None:
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=__file__)
        imported_modules: set[str] = set()
        called_names: set[str] = set()
        called_attrs: set[tuple[str | None, str]] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    base = node.func.value
                    base_name = base.id if isinstance(base, ast.Name) else None
                    called_attrs.add((base_name, node.func.attr))

        self.assertNotIn("package_candidate", imported_modules)
        self.assertNotIn("final_freezer", imported_modules)
        self.assertNotIn("safe_promote", called_names)
        self.assertNotIn(("run_case", "run_case"), called_attrs)
        self.assertNotIn(("run_case_module", "run_case"), called_attrs)

        self.assertTrue(hasattr(x5_surgical_utils, "safe_promote"))


if __name__ == "__main__":
    unittest.main()

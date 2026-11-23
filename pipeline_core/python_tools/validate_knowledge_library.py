"""Validate the eight-textbook knowledge-card library v3.0.

功能：检查八教材知识卡库 v3.0 的文献条目、索引、知识卡文件、
字段完整性、router 和 README 说明是否满足验收要求。
输入：项目内固定知识库文件。
输出：pipeline_core/literature_library/knowledge_cards/validation_report.md。
使用方法：在项目根目录运行
    python pipeline_core/python_tools/validate_knowledge_library.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import sys


TEXTBOOK_KEYS = [
    "Tan2005MathModel",
    "Pu2024OperationsResearch",
    "Xia2016MathModelMATLAB",
    "Zhang2024ProbabilityStatistics",
    "Si2015ModelingAlgorithmsApplications",
    "Nocedal2006NumericalOptimization",
    "Boyd2004ConvexOptimization",
    "Zhou2016MachineLearning",
]

REQUIRED_CARD_FIELDS = [
    "# 知识卡：",
    "来源文献",
    "知识类别",
    "适用题型",
    "引用等级",
    "核心思想",
    "适用场景",
    "生产线调用",
    "风险",
    "需要人工查书补充",
]

REQUIRED_ROUTER_ITEMS = [
    "凸优化与对偶问题",
    "非线性数值优化问题",
    "大规模优化问题",
    "黑箱优化和无导数优化问题",
    "拟合、参数估计和正则化",
    "机器学习预测与分类",
    "聚类、降维和特征工程",
    "高级机器学习边界",
]

REQUIRED_README_ITEMS = [
    "八教材知识卡库 v3.0",
    "Nocedal2006NumericalOptimization",
    "Boyd2004ConvexOptimization",
    "Zhou2016MachineLearning",
    "citation-auditor",
    "evidence_map",
]

MANUAL_LOOKUP_PHRASE = "公式细节需查原书对应章节"


@dataclass(frozen=True)
class TextRead:
    text: str
    error: str | None = None


@dataclass(frozen=True)
class CardFileCheck:
    number: int
    files: list[Path]
    status: str


@dataclass(frozen=True)
class FieldCheck:
    file: Path
    missing_fields: list[str]
    status: str


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_text_file(path: Path) -> TextRead:
    if not path.exists():
        return TextRead("", "文件缺失")
    if not path.is_file():
        return TextRead("", "不是普通文件")

    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return TextRead(path.read_text(encoding=encoding), None)
        except UnicodeDecodeError as exc:
            last_error = exc
        except OSError as exc:
            return TextRead("", f"读取失败：{exc}")

    return TextRead("", f"编码无法识别：{last_error}")


def status_from_bool(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def display_rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def numbered_card_files(cards_dir: Path) -> dict[int, list[Path]]:
    files_by_number: dict[int, list[Path]] = {}
    if not cards_dir.exists() or not cards_dir.is_dir():
        return files_by_number

    pattern = re.compile(r"^(\d{2,3})_.*\.md$", re.IGNORECASE)
    for path in cards_dir.iterdir():
        if not path.is_file():
            continue
        match = pattern.match(path.name)
        if match:
            files_by_number.setdefault(int(match.group(1)), []).append(path)
    return files_by_number


def check_card_files(cards_dir: Path) -> list[CardFileCheck]:
    by_number = numbered_card_files(cards_dir)
    checks: list[CardFileCheck] = []
    for number in range(1, 104):
        files = sorted(by_number.get(number, []), key=lambda path: path.name)
        if len(files) == 1:
            status = "PASS"
        elif not files:
            status = "FAIL: 文件缺失"
        else:
            status = "FAIL: 编号重复"
        checks.append(CardFileCheck(number, files, status))
    return checks


def check_key_presence(read: TextRead, keys: list[str]) -> list[tuple[str, str]]:
    if read.error:
        return [(key, "FAIL") for key in keys]
    return [(key, status_from_bool(key in read.text)) for key in keys]


def check_literal_presence(read: TextRead, items: list[str]) -> list[tuple[str, str]]:
    if read.error:
        return [(item, "FAIL") for item in items]
    return [(item, status_from_bool(item in read.text)) for item in items]


def check_advanced_card_fields(
    cards_dir: Path, card_checks: list[CardFileCheck]
) -> tuple[list[FieldCheck], list[Path]]:
    field_checks: list[FieldCheck] = []
    manual_lookup_cards: list[Path] = []

    for card_check in card_checks:
        if not 64 <= card_check.number <= 103:
            continue

        if len(card_check.files) != 1:
            placeholder = cards_dir / f"{card_check.number:02d}_*.md"
            field_checks.append(
                FieldCheck(placeholder, REQUIRED_CARD_FIELDS[:], "FAIL")
            )
            continue

        path = card_check.files[0]
        read = read_text_file(path)
        if read.error:
            field_checks.append(FieldCheck(path, REQUIRED_CARD_FIELDS[:], "FAIL"))
            continue

        missing_fields = [
            field for field in REQUIRED_CARD_FIELDS if field not in read.text
        ]
        status = status_from_bool(not missing_fields)
        field_checks.append(FieldCheck(path, missing_fields, status))
        if MANUAL_LOOKUP_PHRASE in read.text:
            manual_lookup_cards.append(path)

    return field_checks, sorted(manual_lookup_cards, key=lambda path: path.name)


def table_two_columns(header_left: str, header_right: str, rows: list[tuple[str, str]]) -> str:
    lines = [
        f"| {header_left} | {header_right} |",
        "|---|---|",
    ]
    for left, right in rows:
        lines.append(f"| {markdown_escape(left)} | {markdown_escape(right)} |")
    return "\n".join(lines)


def build_report() -> tuple[str, bool]:
    root = project_root()
    library_dir = root / "pipeline_core" / "literature_library"
    knowledge_dir = library_dir / "knowledge_cards"
    cards_dir = knowledge_dir / "core_competition_cards"
    references_path = library_dir / "references.bib"
    index_path = library_dir / "literature_index.md"
    router_path = knowledge_dir / "knowledge_card_router.md"
    readme_path = knowledge_dir / "README.md"

    references_read = read_text_file(references_path)
    index_read = read_text_file(index_path)
    router_read = read_text_file(router_path)
    readme_read = read_text_file(readme_path)

    references_checks = check_key_presence(references_read, TEXTBOOK_KEYS)
    index_checks = check_key_presence(index_read, TEXTBOOK_KEYS)
    card_file_checks = check_card_files(cards_dir)
    field_checks, manual_lookup_cards = check_advanced_card_fields(
        cards_dir, card_file_checks
    )
    router_checks = check_literal_presence(router_read, REQUIRED_ROUTER_ITEMS)
    readme_checks = check_literal_presence(readme_read, REQUIRED_README_ITEMS)

    failure_notes: list[str] = []
    for file_name, read in [
        ("references.bib", references_read),
        ("literature_index.md", index_read),
        ("knowledge_card_router.md", router_read),
        ("README.md", readme_read),
    ]:
        if read.error:
            failure_notes.append(f"{file_name}: {read.error}")

    for key, status in references_checks:
        if status != "PASS":
            failure_notes.append(f"references.bib 缺少条目：{key}")
    for key, status in index_checks:
        if status != "PASS":
            failure_notes.append(f"literature_index.md 缺少索引：{key}")
    for check in card_file_checks:
        if check.status != "PASS":
            failure_notes.append(f"知识卡 {check.number:02d}: {check.status}")
    for check in field_checks:
        if check.status != "PASS":
            failure_notes.append(
                f"{display_rel(check.file, root)} 缺少字段："
                + "、".join(check.missing_fields)
            )
    for item, status in router_checks:
        if status != "PASS":
            failure_notes.append(f"router 缺少路由项：{item}")
    for item, status in readme_checks:
        if status != "PASS":
            failure_notes.append(f"README 缺少检查项：{item}")

    overall_pass = not failure_notes
    overall_status = status_from_bool(overall_pass)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    card_file_rows: list[str] = [
        "| 编号 | 文件 | 状态 |",
        "|---|---|---|",
    ]
    for check in card_file_checks:
        if check.files:
            file_name = "<br>".join(display_rel(path, root) for path in check.files)
        else:
            file_name = f"{check.number:02d}_*.md"
        card_file_rows.append(
            f"| {check.number:02d} | {markdown_escape(file_name)} | {check.status} |"
        )

    field_rows: list[str] = [
        "| 文件 | 缺失字段 | 状态 |",
        "|---|---|---|",
    ]
    for check in field_checks:
        missing = "无" if not check.missing_fields else "、".join(check.missing_fields)
        field_rows.append(
            f"| {markdown_escape(display_rel(check.file, root))} | "
            f"{markdown_escape(missing)} | {check.status} |"
        )

    if manual_lookup_cards:
        manual_lookup_section = "\n".join(
            f"- {display_rel(path, root)}" for path in manual_lookup_cards
        )
    else:
        manual_lookup_section = "未发现含有“公式细节需查原书对应章节”的知识卡。"

    if failure_notes:
        conclusion = (
            "当前自动验收结果为 FAIL，暂不建议进入 skill 接入阶段。缺失项清单：\n\n"
            + "\n".join(f"- {note}" for note in failure_notes)
        )
    else:
        conclusion = (
            "当前自动验收结果为 PASS，可以进入 skill 接入阶段。"
            "仍需人工查书补充的知识卡不得直接写入定量公式细节。"
        )

    report = "\n\n".join(
        [
            "# 八教材知识卡库 v3.0 自动验收报告\n\n生成时间：" + generated_at,
            "## 1. PASS / FAIL 总结\n\n总状态：" + overall_status,
            "## 2. references.bib 检查\n\n"
            + table_two_columns("条目", "状态", references_checks),
            "## 3. literature_index.md 检查\n\n"
            + table_two_columns("条目", "状态", index_checks),
            "## 4. 知识卡文件检查\n\n" + "\n".join(card_file_rows),
            "## 5. 字段完整性检查\n\n" + "\n".join(field_rows),
            "## 6. router 检查\n\n"
            + table_two_columns("路由项", "状态", router_checks),
            "## 7. README 检查\n\n"
            + table_two_columns("检查项", "状态", readme_checks),
            "## 8. 仍需人工查书补充\n\n" + manual_lookup_section,
            "## 9. 结论\n\n" + conclusion,
        ]
    )
    return report + "\n", overall_pass


def main() -> None:
    root = project_root()
    report_path = (
        root
        / "pipeline_core"
        / "literature_library"
        / "knowledge_cards"
        / "validation_report.md"
    )

    report, passed = build_report()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"Knowledge library validation: {'PASS' if passed else 'FAIL'}")
    print(f"Report: {report_path.relative_to(root).as_posix()}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

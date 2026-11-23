"""Build a fact-only problem contract candidate from an official PDF.

This module is deliberately limited to intake.  It does not select a model,
write assumptions, choose an objective function, run a router, or execute a
solver.  The built-in PDF reader is a small stdlib fallback for the official
problem PDFs used by this repository when optional PDF packages are absent.
"""

from __future__ import annotations

import hashlib
import json
import re
import zlib
from pathlib import Path
from typing import Any


class ProblemIntakeBlocked(ValueError):
    """Raised when official intake cannot safely produce a candidate."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProblemIntakeBlocked(f"CASE_CONFIG_UNREADABLE: {path}") from exc
    if not isinstance(payload, dict):
        raise ProblemIntakeBlocked("CASE_CONFIG_NOT_OBJECT")
    return payload


def _pdf_objects(pdf_bytes: bytes) -> dict[int, bytes]:
    return {
        int(match.group(1)): match.group(0)
        for match in re.finditer(rb"([0-9]+) [0-9]+ obj.*?endobj", pdf_bytes, re.S)
    }


def _decode_stream(objects: dict[int, bytes], object_number: int) -> bytes:
    object_bytes = objects.get(object_number)
    if object_bytes is None:
        raise ProblemIntakeBlocked(f"PDF_OBJECT_MISSING: {object_number}")
    match = re.search(rb"stream\r?\n(.*?)\r?\nendstream", object_bytes, re.S)
    if match is None:
        raise ProblemIntakeBlocked(f"PDF_STREAM_MISSING: {object_number}")
    raw = match.group(1)
    if b"/FlateDecode" in object_bytes:
        try:
            raw = zlib.decompress(raw)
        except zlib.error as exc:
            raise ProblemIntakeBlocked(f"PDF_STREAM_UNREADABLE: {object_number}") from exc
    return raw


def _to_unicode_map(objects: dict[int, bytes], object_number: int) -> dict[int, str]:
    text = _decode_stream(objects, object_number).decode("latin1")
    mapping: dict[int, str] = {}
    for line in text.splitlines():
        values = re.findall(r"<([0-9A-Fa-f]+)>", line)
        if len(values) == 3:
            start, end, target = (int(value, 16) for value in values)
            for code in range(start, end + 1):
                mapping[code] = chr(target + code - start)
        elif len(values) == 2 and "begin" not in line:
            mapping[int(values[0], 16)] = chr(int(values[1], 16))
    return mapping


def _literal_text(value: str) -> str:
    return (
        value.replace(r"\(", "(")
        .replace(r"\)", ")")
        .replace(r"\\", "\\")
    )


def _decode_hex(value: str, font_map: dict[int, str] | None) -> str:
    raw = bytes.fromhex(value)
    if not font_map:
        return raw.decode("latin1", errors="replace")
    return "".join(
        font_map.get(int.from_bytes(raw[index : index + 2], "big"), "�")
        for index in range(0, len(raw), 2)
    )


def _decode_text_block(block: str, font: int, font_maps: dict[int, dict[int, str]]) -> str:
    values: list[str] = []
    operand_pattern = re.compile(
        r"\[((?:\\.|[^\]])*)\]\s*TJ|\(((?:\\.|[^)])*)\)\s*Tj"
    )
    token_pattern = re.compile(r"<([0-9A-Fa-f]+)>|\(((?:\\.|[^)])*)\)")
    for operand in operand_pattern.finditer(block):
        payload = operand.group(1) if operand.group(1) is not None else operand.group(2)
        for token in token_pattern.finditer(payload):
            if token.group(1) is not None:
                values.append(_decode_hex(token.group(1), font_maps.get(font)))
            else:
                values.append(_literal_text(token.group(2)))
    return "".join(values).strip()


def _extract_pdf_lines(pdf_path: Path) -> list[str]:
    try:
        pdf_bytes = pdf_path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise ProblemIntakeBlocked(f"OFFICIAL_PROBLEM_FILE_UNREADABLE: {pdf_path}") from exc
    objects = _pdf_objects(pdf_bytes)
    page_objects = [
        (number, value)
        for number, value in sorted(objects.items())
        if b"/Type/Page" in value and b"/Contents" in value
    ]
    if not page_objects:
        raise ProblemIntakeBlocked("OFFICIAL_PROBLEM_PDF_HAS_NO_PAGES")

    lines: list[str] = []
    for _, page_object in page_objects:
        content_match = re.search(rb"/Contents\s+([0-9]+)\s+0\s+R", page_object)
        if content_match is None:
            raise ProblemIntakeBlocked("OFFICIAL_PROBLEM_PAGE_CONTENT_MISSING")
        content_number = int(content_match.group(1))
        content = _decode_stream(objects, content_number).decode("latin1")

        font_maps: dict[int, dict[int, str]] = {}
        resources = re.search(rb"/Font<<(.*?)>>", page_object, re.S)
        if resources:
            for font_match in re.finditer(
                rb"/(F[0-9]+)\s+([0-9]+)\s+0\s+R", resources.group(1)
            ):
                font_name = font_match.group(1).decode("ascii")
                font_object = objects.get(int(font_match.group(2)))
                if font_object is None:
                    continue
                unicode_match = re.search(
                    rb"/ToUnicode\s+([0-9]+)\s+0\s+R", font_object
                )
                if unicode_match:
                    font_maps[int(font_name[1:])] = _to_unicode_map(
                        objects, int(unicode_match.group(1))
                    )

        positioned: list[tuple[float, float, str]] = []
        current_font = 0
        for block in re.findall(r"BT(.*?)ET", content, re.S):
            font_match = re.findall(r"/F([0-9]+)\s+[0-9.]+\s+Tf", block)
            if font_match:
                current_font = int(font_match[-1])
            text = _decode_text_block(block, current_font, font_maps)
            if not text:
                continue
            tm_match = re.search(
                r"1 0 0 1\s+([-0-9.]+)\s+([-0-9.]+)\s+Tm", block
            )
            x = float(tm_match.group(1)) if tm_match else 0.0
            y = float(tm_match.group(2)) if tm_match else 0.0
            positioned.append((y, x, text))

        grouped: dict[float, list[tuple[float, str]]] = {}
        for y, x, text in positioned:
            grouped.setdefault(round(y, 1), []).append((x, text))
        for y in sorted(grouped, reverse=True):
            line = "".join(text for _, text in sorted(grouped[y])).strip()
            if line:
                lines.append(line)
    return lines


def _official_files(case_dir: Path, config: dict[str, Any]) -> list[str]:
    configured = config.get("official_problem_statement_files")
    if not isinstance(configured, list) or not configured:
        raise ProblemIntakeBlocked("OFFICIAL_PROBLEM_FILE_NOT_DECLARED")
    if not all(isinstance(item, str) and item.strip() for item in configured):
        raise ProblemIntakeBlocked("OFFICIAL_PROBLEM_FILE_DECLARATION_INVALID")
    resolved: list[str] = []
    for item in configured:
        path = (case_dir / item).resolve()
        try:
            path.relative_to(case_dir.resolve())
        except ValueError as exc:
            raise ProblemIntakeBlocked("OFFICIAL_PROBLEM_FILE_UNSAFE_PATH") from exc
        if not path.is_file():
            raise ProblemIntakeBlocked(f"OFFICIAL_PROBLEM_FILE_MISSING: {item}")
        resolved.append(item.replace("\\", "/"))
    return resolved


def _candidate_markdown(
    *,
    case_id: str,
    source_path: str,
    source_sha256: str,
    extraction: dict[str, Any],
) -> str:
    questions = extraction["questions"]
    question_lines = "\n".join(
        f"{item['number']}. {item['task']}" for item in questions
    )
    return f"""# Problem Contract Candidate

`CONTRACT_STATUS`: `PENDING_MANUAL_REVIEW`
`CANDIDATE_TYPE`: `FACT_ONLY_OFFICIAL_INTAKE`
`CASE_ID`: `{case_id}`

本文件是由正式官方题面 intake 生成的题目契约候选，不是 Gate 1 已确认的正式 contract。Gate 1 通过前不得进入 `router`、knowledge card 或 solver。

## 官方题面来源

- 文件：`{source_path}`
- SHA-256：`{source_sha256}`
- 题面识别：`2019` 年、`B` 题
- 附件：`attachment_files=[]`；当前 case 未声明独立附件

## 题目身份

- 题目标题：`{extraction['problem_identity']['title']}`
- 题号：`B`
- 题面对象：一面牛皮双面鼓、沿鼓身圆周均匀分布的多根等长绳子，以及在鼓面中心上方竖直落下的球

## 题面直接给出的背景与条件

- 团队成员每人牵拉一根绳子，使鼓面保持水平；项目开始时球从鼓面中心上方竖直落下，队员协作将球颠起，使其有节奏地在鼓面上跳动。
- 颠球过程中，队员只能抓握绳子末端，不能接触鼓或绳子的其他位置。
- 排球质量：`270 g`。
- 鼓面直径：`40 cm`；鼓身高度：`22 cm`；鼓质量：`3.6 kg`。
- 队员人数不少于 `8` 人；队员之间的最小距离不小于 `60 cm`。
- 初始落球位置为鼓面中心上方 `40 cm`；球被颠起后应离开鼓面 `40 cm` 以上，低于 `40 cm` 时项目停止。
- 题面目标：使连续颠球次数尽可能多。

## 各小问原始任务

{question_lines}

## 题面中的明确待核对项

- 问题 2 使用 `8` 名队员、绳长 `1.7 m`；鼓面初始水平静止，初始位置较绳子水平位置下降 `11 cm`；题面表 1 给出不同发力时机和力度，要求求 `0.1 s` 时的鼓面倾斜角度。
- 问题 4 使用 `10` 名队员、绳长 `2 m`、球反弹高度 `60 cm`、相对竖直方向 `1°` 的倾斜角度；倾斜方向水平投影指向某两位队员之间，且与两位队员的夹角之比为 `1:2`。
- 表 1 的逐项数值以官方 PDF 第 2 页为准，Gate 1 需人工复核表格列位、正负号和单位。

## Intake 边界

- 本候选只整理官方题面可直接支持的身份、背景、条件、给定参数、表格来源和输出任务。
- 未写入最终模型选择、核心假设、目标函数、求解算法、创新路线或最终结论。
- 题面如存在歧义，保留为 Gate 1 人工确认项，不由 intake 自动选定解释。
"""


def build_problem_contract_candidate(case_dir: str | Path) -> dict[str, Any]:
    """Generate the case-local intake extraction and fact-only contract candidate."""

    case_dir = Path(case_dir).resolve()
    config = _read_json(case_dir / "case_config.json")
    case_id = str(config.get("case_id") or "").strip()
    if not case_id or case_id == "case_template":
        raise ProblemIntakeBlocked("CASE_CONFIG_TEMPLATE_STATE")

    official_files = _official_files(case_dir, config)
    if len(official_files) != 1 or not official_files[0].lower().endswith(".pdf"):
        raise ProblemIntakeBlocked("OFFICIAL_PROBLEM_PDF_REQUIRED")
    source_path = official_files[0]
    source = case_dir / source_path
    lines = _extract_pdf_lines(source)
    full_text = "\n".join(lines)
    if not full_text.strip():
        raise ProblemIntakeBlocked("OFFICIAL_PROBLEM_TEXT_EMPTY")
    if "2019" not in full_text or "同心协力" not in full_text or "题" not in full_text:
        raise ProblemIntakeBlocked("PROBLEM_YEAR_NUMBER_OR_TITLE_UNCONFIRMED")

    extraction = {
        "schema_version": "PROBLEM_INTAKE_V1",
        "intake_status": "CANDIDATE_PENDING_GATE1",
        "case_id": case_id,
        "problem_identity": {
            "year": 2019,
            "group": "B",
            "problem_number": "B",
            "title": "“同心协力”策略研究",
        },
        "official_problem_file": source_path,
        "official_problem_sha256": _sha256(source),
        "official_problem_pages": 2,
        "attachments": config.get("attachment_files") or [],
        "questions": [
            {
                "number": 1,
                "task": "在理想状态下讨论团队的最佳协作策略，并给出该策略下的颠球高度。",
            },
            {
                "number": 2,
                "task": "建立队员发力时机和力度与某一特定时刻鼓面倾斜角度的关系；在题面给定的 8 人、绳长和表 1 条件下，求 0.1 s 时的鼓面倾斜角度。",
            },
            {
                "number": 3,
                "task": "根据问题 2 的模型，判断问题 1 给出的策略是否需要调整，并说明如何调整。",
            },
            {
                "number": 4,
                "task": "在鼓面倾斜且球跳动方向不再竖直时，给出题面指定 10 人场景下所有队员的发力时机及力度，并分析现实情形中的实施效果。",
            },
        ],
        "known_conditions": [
            "排球质量 270 g",
            "鼓面直径 40 cm",
            "鼓身高度 22 cm",
            "鼓质量 3.6 kg",
            "队员人数不少于 8 人",
            "队员最小距离不小于 60 cm",
            "初始落球高度为鼓面中心上方 40 cm",
            "球被颠起后应离开鼓面 40 cm 以上，低于该高度时项目停止",
        ],
        "unsupported_modeling_inferences": [],
        "extracted_line_count": len(lines),
        "extracted_text_sha256": hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
    }

    problem_dir = case_dir / "problem"
    problem_dir.mkdir(parents=True, exist_ok=True)
    (problem_dir / "intake_extraction.json").write_text(
        json.dumps(extraction, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (problem_dir / "problem_contract.md").write_text(
        _candidate_markdown(
            case_id=case_id,
            source_path=source_path,
            source_sha256=extraction["official_problem_sha256"],
            extraction=extraction,
        ),
        encoding="utf-8",
    )
    return {
        "status": "CANDIDATE_GENERATED",
        "intake_status": extraction["intake_status"],
        "case_id": case_id,
        "official_problem_file": source_path,
        "official_problem_sha256": extraction["official_problem_sha256"],
        "candidate_path": "problem/problem_contract.md",
        "extraction_path": "problem/intake_extraction.json",
        "unsupported_modeling_inferences": [],
    }


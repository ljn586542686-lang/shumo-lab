"""Shared plotting style helpers for case-level Python scripts.

This module only configures figure rendering. It must not be used to change
model data, risk scores, optimization results, or any numeric conclusion.
"""

from __future__ import annotations

import warnings
from typing import Any, Sequence


DEFAULT_CHINESE_FONTS: tuple[str, ...] = (
    "Microsoft YaHei",
    "SimHei",
    "Arial Unicode MS",
)


def setup_chinese_plot_style(
    preferred_fonts: Sequence[str] = DEFAULT_CHINESE_FONTS,
) -> dict[str, Any]:
    """Configure matplotlib so plot text can render Chinese labels.

    Returns a small status dictionary that should be recorded in
    `output/validation/output_contract_report.md`.

    The function is intentionally tolerant: if matplotlib or Chinese fonts are
    unavailable, it emits a warning and allows the modeling script to continue.
    """

    try:
        import matplotlib
        from matplotlib import font_manager
    except Exception as exc:  # pragma: no cover - depends on local environment
        warnings.warn(
            f"matplotlib 不可用，无法设置中文图表字体：{exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return {
            "status": "matplotlib_unavailable",
            "font_available": False,
            "selected_font": None,
            "preferred_fonts": list(preferred_fonts),
            "message": "matplotlib 不可用，未能检查中文字体。",
        }

    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    selected_font = next((font for font in preferred_fonts if font in available_fonts), None)

    font_candidates = list(preferred_fonts)
    if selected_font:
        font_candidates = [selected_font] + [font for font in font_candidates if font != selected_font]
    font_candidates += ["DejaVu Sans", "sans-serif"]

    matplotlib.rcParams["font.sans-serif"] = font_candidates
    matplotlib.rcParams["axes.unicode_minus"] = False
    matplotlib.rcParams["svg.fonttype"] = "none"
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["ps.fonttype"] = 42

    if not selected_font:
        warnings.warn(
            "中文字体不可用：未找到 Microsoft YaHei、SimHei 或 Arial Unicode MS；"
            "图表生成可继续，但必须在 output_contract_report.md 中说明。",
            RuntimeWarning,
            stacklevel=2,
        )
        return {
            "status": "font_missing",
            "font_available": False,
            "selected_font": None,
            "preferred_fonts": list(preferred_fonts),
            "message": "中文字体不可用，图表可能回退为默认字体。",
        }

    return {
        "status": "ok",
        "font_available": True,
        "selected_font": selected_font,
        "preferred_fonts": list(preferred_fonts),
        "message": f"已启用中文图表字体：{selected_font}。",
    }

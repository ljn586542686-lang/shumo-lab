from datetime import date
from math import isfinite

def canonicalize_soc_for_propagation(
    raw_soc_kwh: float,
    *,
    lower_bound_kwh: float,
    upper_bound_kwh: float,
    tolerance: float,
    day: date,
    interval_one_based: int,
    source_level: str,
) -> tuple[float, dict[str, object] | None]:
    """仅规范化容差内的边界漂移；真实越界必须 fail-closed。"""

    raw = float(raw_soc_kwh)
    lower = float(lower_bound_kwh)
    upper = float(upper_bound_kwh)
    numeric_tolerance = float(tolerance)
    if not all(isfinite(value) for value in (raw, lower, upper, numeric_tolerance)):
        raise ValueError("Q2_SOC_PROPAGATION_VALUE_NONFINITE")
    if numeric_tolerance <= 0.0 or lower > upper:
        raise ValueError("Q2_SOC_PROPAGATION_BOUND_CONFIGURATION_INVALID")
    canonical = raw
    bound = "NONE"
    if raw < lower:
        if lower - raw > numeric_tolerance:
            raise ValueError("Q2_TRUE_SOC_PROPAGATION_VIOLATION")
        canonical = lower
        bound = "LOWER"
    elif raw > upper:
        if raw - upper > numeric_tolerance:
            raise ValueError("Q2_TRUE_SOC_PROPAGATION_VIOLATION")
        canonical = upper
        bound = "UPPER"
    if bound == "NONE":
        return raw, None
    correction = canonical - raw
    return canonical, {
        "RAW_SOC": raw,
        "CANONICAL_SOC": canonical,
        "CORRECTION": correction,
        "BOUND": bound,
        "DATE": day.isoformat(),
        "INTERVAL": int(interval_one_based),
        "SOURCE_LEVEL": source_level,
    }

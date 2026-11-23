"""Fit three synthetic spring measurements and check a held-out force."""
from math_core.registry import run_model


def main():
    result = run_model(
        {
            "model_type": "statistics.linear_regression",
            "X": [[1.0], [2.0], [3.0]],  # displacement (mm)
            "y": [2.0, 4.0, 6.0],  # force (N)
        },
        run_id="spring-calibration",
    )
    if not result["success"]:
        raise SystemExit(f"Fit failed: {result['failure_reason']}")

    fit = result["primary_results"]
    slope, = fit["beta"]
    intercept = fit["intercept"]
    displacement_mm, reference_n = 4.0, 8.0
    prediction_n = intercept + slope * displacement_mm

    # Scalar comparison adapted from validation/xval_independent.py.
    error_n = abs(prediction_n - reference_n)
    tolerance_n = 1e-9  # numerical tolerance for this exact synthetic example
    passed = error_n <= tolerance_n
    print(f"Slope: {slope:.6g} N/mm")
    print(f"Intercept: {intercept:.3g} N")
    print(f"Prediction at {displacement_mm:g} mm: {prediction_n:.12g} N")
    print(f"Held-out reference: {reference_n:g} N")
    print(f"Absolute error: {error_n:.3g} N")
    print(f"Check (tolerance {tolerance_n:g} N): {'PASS' if passed else 'FAIL'}")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

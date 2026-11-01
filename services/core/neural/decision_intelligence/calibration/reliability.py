from __future__ import annotations

import math
from typing import Any, Union


def calculate_ece(
    probabilities: list[float],
    ground_truth: list[int],
    num_bins: int = 10,
) -> float:
    """
    Computes Expected Calibration Error (ECE) according to Section 8.1:
    ECE = sum_{m=1}^M (|B_m| / N) * |acc(B_m) - conf(B_m)|
    """
    if not probabilities or not ground_truth:
        return 0.0
    if len(probabilities) != len(ground_truth):
        raise ValueError("Probabilities and ground_truth lists must have equal length")
    if num_bins < 1:
        raise ValueError("num_bins must be at least 1")

    n = len(probabilities)
    bin_size = 1.0 / num_bins
    ece = 0.0

    for m in range(num_bins):
        bin_lower = m * bin_size
        bin_upper = (m + 1) * bin_size

        # Find items in bin [lower, upper) or [lower, upper] for last bin
        bin_items = [
            (max(0.0, min(1.0, float(p))), y)
            for p, y in zip(probabilities, ground_truth)
            if (bin_lower <= max(0.0, min(1.0, float(p))) < bin_upper)
            or (m == num_bins - 1 and bin_lower <= max(0.0, min(1.0, float(p))) <= bin_upper)
        ]

        if not bin_items:
            continue

        bin_count = len(bin_items)
        bin_conf = sum(p for p, _ in bin_items) / bin_count
        bin_acc = sum(y for _, y in bin_items) / bin_count

        ece += (bin_count / n) * abs(bin_acc - bin_conf)

    return round(ece, 4)


def calculate_brier_score(
    probabilities: Union[list[float], list[list[float]]],
    ground_truth: Union[list[int], list[list[int]]],
) -> float:
    """
    Computes Brier Score:
    For binary: (1/N) * sum_{i=1}^N (p_i - y_i)^2
    For multi-class: (1/N) * sum_{i=1}^N sum_{k=1}^K (p_{ik} - y_{ik})^2
    """
    if not probabilities or not ground_truth:
        return 0.0
    if len(probabilities) != len(ground_truth):
        raise ValueError("Probabilities and ground_truth lists must have equal length")

    n = len(probabilities)

    # Multi-class check
    if isinstance(probabilities[0], list):
        total_sq_err = 0.0
        for i, p_vec in enumerate(probabilities):  # type: ignore
            y_item = ground_truth[i]
            if isinstance(y_item, list):
                for p, y in zip(p_vec, y_item):
                    total_sq_err += (float(p) - float(y)) ** 2
            else:
                y_idx = int(y_item)
                for k, p in enumerate(p_vec):
                    target = 1.0 if k == y_idx else 0.0
                    total_sq_err += (float(p) - target) ** 2
        return round(total_sq_err / n, 4)
    else:
        # Binary case
        sq_err = sum((float(p) - float(y)) ** 2 for p, y in zip(probabilities, ground_truth))  # type: ignore
        return round(sq_err / n, 4)


def calculate_reliability_diagram(
    probabilities: list[float],
    ground_truth: list[int],
    num_bins: int = 10,
) -> list[dict[str, Any]]:
    """
    Generates binned calibration data for reliability diagrams.
    """
    if not probabilities or not ground_truth:
        return []

    bin_size = 1.0 / num_bins
    diagram_bins = []

    for m in range(num_bins):
        bin_lower = m * bin_size
        bin_upper = (m + 1) * bin_size

        bin_items = [
            (p, y)
            for p, y in zip(probabilities, ground_truth)
            if (bin_lower <= p < bin_upper) or (m == num_bins - 1 and bin_lower <= p <= bin_upper)
        ]

        count = len(bin_items)
        conf = (sum(p for p, _ in bin_items) / count) if count > 0 else (bin_lower + bin_upper) / 2.0
        acc = (sum(y for _, y in bin_items) / count) if count > 0 else 0.0

        diagram_bins.append({
            "bin_index": m,
            "lower_bound": round(bin_lower, 2),
            "upper_bound": round(bin_upper, 2),
            "sample_count": count,
            "confidence": round(conf, 4),
            "accuracy": round(acc, 4),
            "gap": round(abs(acc - conf), 4) if count > 0 else 0.0,
        })

    return diagram_bins


def apply_platt_scaling(prob: float, a: float = 1.0, b: float = 0.0) -> float:
    """
    Applies temperature / Platt sigmoid calibration:
    p_cal = 1 / (1 + exp(-(a * logit(p) + b)))
    """
    clamped_p = max(1e-6, min(1.0 - 1e-6, prob))
    logit = math.log(clamped_p / (1.0 - clamped_p))
    scaled_logit = a * logit + b
    try:
        cal_p = 1.0 / (1.0 + math.exp(-scaled_logit))
    except OverflowError:
        cal_p = 0.0 if scaled_logit < 0 else 1.0
    return round(cal_p, 4)


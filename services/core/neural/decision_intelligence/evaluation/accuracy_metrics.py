from __future__ import annotations

from typing import Any


class AccuracyEvaluator:
    """
    Computes precision, recall, macro-F1, and selective accuracy (Section 8.1 & 13).
    """

    @staticmethod
    def compute_classification_metrics(
        y_true: list[str | int],
        y_pred: list[str | int],
    ) -> dict[str, float]:
        if not y_true or not y_pred:
            return {"accuracy": 0.0, "macro_f1": 0.0, "precision": 0.0, "recall": 0.0}
        if len(y_true) != len(y_pred):
            raise ValueError("y_true and y_pred must have equal lengths")

        str_y_true = [str(x) for x in y_true]
        str_y_pred = [str(x) for x in y_pred]

        n = len(str_y_true)
        correct = sum(1 for yt, yp in zip(str_y_true, str_y_pred) if yt == yp)
        accuracy = correct / n

        # Extract unique classes
        classes = sorted(list(set(str_y_true).union(set(str_y_pred))))
        precisions: list[float] = []
        recalls: list[float] = []
        f1s: list[float] = []

        for c in classes:
            tp = sum(1 for yt, yp in zip(str_y_true, str_y_pred) if yt == c and yp == c)
            fp = sum(1 for yt, yp in zip(str_y_true, str_y_pred) if yt != c and yp == c)
            fn = sum(1 for yt, yp in zip(str_y_true, str_y_pred) if yt == c and yp != c)

            prec = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
            rec = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
            f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

            precisions.append(prec)
            recalls.append(rec)
            f1s.append(f1)

        macro_prec = sum(precisions) / len(classes) if classes else 0.0
        macro_rec = sum(recalls) / len(classes) if classes else 0.0
        macro_f1 = sum(f1s) / len(classes) if classes else 0.0

        return {
            "accuracy": round(accuracy, 4),
            "precision": round(macro_prec, 4),
            "recall": round(macro_rec, 4),
            "macro_f1": round(macro_f1, 4),
            "total_samples": float(n),
        }

    @staticmethod
    def compute_selective_accuracy(
        y_true: list[str | int],
        y_pred: list[str | int],
        confidences: list[float],
        threshold: float = 0.85,
    ) -> dict[str, Any]:
        """
        Computes accuracy conditioned on System 1 accepting the decision (confidence >= threshold).
        """
        if not y_true:
            return {"coverage": 0.0, "selective_accuracy": 0.0, "accepted_count": 0, "escalated_count": 0}
        if not (len(y_true) == len(y_pred) == len(confidences)):
            raise ValueError("y_true, y_pred, and confidences must have equal length")

        accepted_true: list[str | int] = []
        accepted_pred: list[str | int] = []
        escalated_count = 0

        for yt, yp, conf in zip(y_true, y_pred, confidences):
            if conf >= threshold:
                accepted_true.append(yt)
                accepted_pred.append(yp)
            else:
                escalated_count += 1

        total = len(y_true)
        accepted_count = len(accepted_true)
        coverage = accepted_count / total if total > 0 else 0.0

        if accepted_count > 0:
            correct = sum(1 for yt, yp in zip(accepted_true, accepted_pred) if str(yt).strip().lower() == str(yp).strip().lower())
            sel_acc = correct / accepted_count
        else:
            sel_acc = 1.0  # Perfect selectivity when escalating everything

        return {
            "coverage": round(coverage, 4),
            "selective_accuracy": round(sel_acc, 4),
            "accepted_count": accepted_count,
            "escalated_count": escalated_count,
            "total_count": total,
        }

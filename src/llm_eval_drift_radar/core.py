from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


REQUIRED_FIELDS = ("case_id",)
KNOWN_FIELDS = (
    "case_id",
    "prompt",
    "expected",
    "actual",
    "score",
    "pass",
    "category",
    "model",
    "latency_ms",
    "cost_usd",
)

DEFAULT_THRESHOLDS: Dict[str, Any] = {
    "score_drop": 0.05,
    "latency_regression_pct": 20.0,
    "cost_regression_pct": 20.0,
    "fail_on_new_failures": True,
    "fail_on_score_drops": True,
    "fail_on_category_drift": False,
    "fail_on_model_changes": False,
    "fail_on_latency_regressions": False,
    "fail_on_cost_regressions": False,
    "fail_on_missing_cases": False,
    "fail_on_new_cases": False,
}


@dataclass(frozen=True)
class EvalRecord:
    case_id: str
    prompt: str = ""
    expected: str = ""
    actual: str = ""
    score: Optional[float] = None
    passed: Optional[bool] = None
    category: str = ""
    model: str = ""
    latency_ms: Optional[float] = None
    cost_usd: Optional[float] = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaseDiff:
    case_id: str
    status: str
    signals: Tuple[str, ...]
    baseline: Optional[EvalRecord]
    current: Optional[EvalRecord]
    score_delta: Optional[float] = None
    latency_delta_pct: Optional[float] = None
    cost_delta_pct: Optional[float] = None


@dataclass(frozen=True)
class ComparisonResult:
    summary: Mapping[str, Any]
    cases: Tuple[CaseDiff, ...]
    thresholds: Mapping[str, Any]

    @property
    def should_fail(self) -> bool:
        return bool(self.summary.get("should_fail"))


def load_thresholds(path: Optional[str]) -> Dict[str, Any]:
    thresholds = dict(DEFAULT_THRESHOLDS)
    if not path:
        return thresholds
    with open(path, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):
        raise ValueError("thresholds JSON must be an object")
    thresholds.update(loaded)
    return thresholds


def load_records(path: str) -> List[EvalRecord]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".jsonl":
        rows = _read_jsonl(file_path)
    elif suffix == ".csv":
        rows = _read_csv(file_path)
    else:
        raise ValueError(f"unsupported input format for {path!r}; use .jsonl or .csv")
    return [_row_to_record(row, index, str(file_path)) for index, row in enumerate(rows, start=1)]


def compare_runs(
    baseline_records: Sequence[EvalRecord],
    current_records: Sequence[EvalRecord],
    thresholds: Optional[Mapping[str, Any]] = None,
) -> ComparisonResult:
    active_thresholds = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        active_thresholds.update(thresholds)

    baseline = _index_by_case_id(baseline_records, "baseline")
    current = _index_by_case_id(current_records, "current")
    all_case_ids = sorted(set(baseline) | set(current))
    cases: List[CaseDiff] = []
    counts: Dict[str, int] = {
        "total_cases": len(all_case_ids),
        "compared_cases": 0,
        "new_cases": 0,
        "missing_cases": 0,
        "new_failures": 0,
        "fixed": 0,
        "score_drops": 0,
        "category_drifts": 0,
        "model_changes": 0,
        "latency_regressions": 0,
        "cost_regressions": 0,
    }

    fail_reasons: List[str] = []

    for case_id in all_case_ids:
        base = baseline.get(case_id)
        cur = current.get(case_id)
        if base is None:
            counts["new_cases"] += 1
            cases.append(CaseDiff(case_id, "new_case", ("new_case",), None, cur))
            continue
        if cur is None:
            counts["missing_cases"] += 1
            cases.append(CaseDiff(case_id, "missing_case", ("missing_case",), base, None))
            continue

        counts["compared_cases"] += 1
        signals: List[str] = []
        score_delta = _delta(cur.score, base.score)
        latency_delta_pct = _pct_delta(cur.latency_ms, base.latency_ms)
        cost_delta_pct = _pct_delta(cur.cost_usd, base.cost_usd)

        if base.passed is not False and cur.passed is False:
            signals.append("new_failure")
            counts["new_failures"] += 1
        if base.passed is False and cur.passed is not False:
            signals.append("fixed")
            counts["fixed"] += 1
        if score_delta is not None and score_delta <= -float(active_thresholds["score_drop"]):
            signals.append("score_drop")
            counts["score_drops"] += 1
        if base.category and cur.category and base.category != cur.category:
            signals.append("category_drift")
            counts["category_drifts"] += 1
        if base.model and cur.model and base.model != cur.model:
            signals.append("model_change")
            counts["model_changes"] += 1
        if (
            latency_delta_pct is not None
            and latency_delta_pct >= float(active_thresholds["latency_regression_pct"])
        ):
            signals.append("latency_regression")
            counts["latency_regressions"] += 1
        if (
            cost_delta_pct is not None
            and cost_delta_pct >= float(active_thresholds["cost_regression_pct"])
        ):
            signals.append("cost_regression")
            counts["cost_regressions"] += 1

        status = _status_from_signals(signals)
        cases.append(
            CaseDiff(
                case_id=case_id,
                status=status,
                signals=tuple(signals),
                baseline=base,
                current=cur,
                score_delta=score_delta,
                latency_delta_pct=latency_delta_pct,
                cost_delta_pct=cost_delta_pct,
            )
        )

    _append_fail_reason(fail_reasons, active_thresholds, "fail_on_new_failures", counts["new_failures"], "new_failures")
    _append_fail_reason(fail_reasons, active_thresholds, "fail_on_score_drops", counts["score_drops"], "score_drops")
    _append_fail_reason(fail_reasons, active_thresholds, "fail_on_category_drift", counts["category_drifts"], "category_drifts")
    _append_fail_reason(fail_reasons, active_thresholds, "fail_on_model_changes", counts["model_changes"], "model_changes")
    _append_fail_reason(fail_reasons, active_thresholds, "fail_on_latency_regressions", counts["latency_regressions"], "latency_regressions")
    _append_fail_reason(fail_reasons, active_thresholds, "fail_on_cost_regressions", counts["cost_regressions"], "cost_regressions")
    _append_fail_reason(fail_reasons, active_thresholds, "fail_on_missing_cases", counts["missing_cases"], "missing_cases")
    _append_fail_reason(fail_reasons, active_thresholds, "fail_on_new_cases", counts["new_cases"], "new_cases")

    summary = dict(counts)
    summary["should_fail"] = bool(fail_reasons)
    summary["fail_reasons"] = fail_reasons
    summary["pass_rate_baseline"] = _pass_rate(baseline_records)
    summary["pass_rate_current"] = _pass_rate(current_records)
    summary["average_score_baseline"] = _average([record.score for record in baseline_records])
    summary["average_score_current"] = _average([record.score for record in current_records])
    summary["average_latency_ms_baseline"] = _average([record.latency_ms for record in baseline_records])
    summary["average_latency_ms_current"] = _average([record.latency_ms for record in current_records])
    summary["total_cost_usd_baseline"] = _total([record.cost_usd for record in baseline_records])
    summary["total_cost_usd_current"] = _total([record.cost_usd for record in current_records])

    return ComparisonResult(summary=summary, cases=tuple(cases), thresholds=active_thresholds)


def _read_jsonl(path: Path) -> List[Mapping[str, Any]]:
    rows: List[Mapping[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no}: JSONL row must be an object")
            rows.append(row)
    return rows


def _read_csv(path: Path) -> List[Mapping[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: CSV header is required")
        return [dict(row) for row in reader]


def _row_to_record(row: Mapping[str, Any], index: int, source: str) -> EvalRecord:
    for field_name in REQUIRED_FIELDS:
        if _string(row.get(field_name)) == "":
            raise ValueError(f"{source}:{index}: missing required field {field_name!r}")
    return EvalRecord(
        case_id=_string(row.get("case_id")),
        prompt=_string(row.get("prompt")),
        expected=_string(row.get("expected")),
        actual=_string(row.get("actual")),
        score=_optional_float(row.get("score"), "score", index, source),
        passed=_optional_bool(row.get("pass"), "pass", index, source),
        category=_string(row.get("category")),
        model=_string(row.get("model")),
        latency_ms=_optional_float(row.get("latency_ms"), "latency_ms", index, source),
        cost_usd=_optional_float(row.get("cost_usd"), "cost_usd", index, source),
        raw=dict(row),
    )


def _index_by_case_id(records: Sequence[EvalRecord], label: str) -> Dict[str, EvalRecord]:
    indexed: Dict[str, EvalRecord] = {}
    for record in records:
        if record.case_id in indexed:
            raise ValueError(f"duplicate case_id {record.case_id!r} in {label}")
        indexed[record.case_id] = record
    return indexed


def _optional_float(value: Any, field_name: str, index: int, source: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source}:{index}: field {field_name!r} must be numeric") from exc


def _optional_bool(value: Any, field_name: str, index: int, source: str) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "t", "yes", "y", "1", "pass", "passed"}:
        return True
    if text in {"false", "f", "no", "n", "0", "fail", "failed"}:
        return False
    raise ValueError(f"{source}:{index}: field {field_name!r} must be boolean")


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _delta(current: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if current is None or baseline is None:
        return None
    return current - baseline


def _pct_delta(current: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if current is None or baseline is None or baseline == 0:
        return None
    return ((current - baseline) / baseline) * 100.0


def _status_from_signals(signals: Sequence[str]) -> str:
    priority = (
        "new_failure",
        "score_drop",
        "latency_regression",
        "cost_regression",
        "category_drift",
        "model_change",
        "fixed",
    )
    for signal in priority:
        if signal in signals:
            return signal
    return "unchanged"


def _append_fail_reason(
    fail_reasons: List[str],
    thresholds: Mapping[str, Any],
    flag: str,
    count: int,
    reason: str,
) -> None:
    if bool(thresholds.get(flag)) and count > 0:
        fail_reasons.append(reason)


def _pass_rate(records: Sequence[EvalRecord]) -> Optional[float]:
    known = [record.passed for record in records if record.passed is not None]
    if not known:
        return None
    return sum(1 for value in known if value) / len(known)


def _average(values: Iterable[Optional[float]]) -> Optional[float]:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _total(values: Iterable[Optional[float]]) -> Optional[float]:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return sum(numbers)


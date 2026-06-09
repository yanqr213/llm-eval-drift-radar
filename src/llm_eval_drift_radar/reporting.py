from __future__ import annotations

import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional, TextIO
from xml.etree import ElementTree as ET

from .core import CaseDiff, ComparisonResult, EvalRecord


CSV_FIELDS = (
    "case_id",
    "status",
    "signals",
    "baseline_pass",
    "current_pass",
    "baseline_score",
    "current_score",
    "score_delta",
    "baseline_category",
    "current_category",
    "baseline_model",
    "current_model",
    "baseline_latency_ms",
    "current_latency_ms",
    "latency_delta_pct",
    "baseline_cost_usd",
    "current_cost_usd",
    "cost_delta_pct",
)

JUNIT_FAILURE_SIGNALS = {
    "fail_on_new_failures": "new_failure",
    "fail_on_score_drops": "score_drop",
    "fail_on_category_drift": "category_drift",
    "fail_on_model_changes": "model_change",
    "fail_on_latency_regressions": "latency_regression",
    "fail_on_cost_regressions": "cost_regression",
    "fail_on_missing_cases": "missing_case",
    "fail_on_new_cases": "new_case",
}


def render(result: ComparisonResult, output_format: str) -> str:
    if output_format == "json":
        return render_json(result)
    if output_format == "csv":
        return render_csv(result)
    if output_format == "markdown":
        return render_markdown(result)
    if output_format == "junit":
        return render_junit(result)
    raise ValueError(f"unsupported output format {output_format!r}")


def render_json(result: ComparisonResult) -> str:
    payload = {
        "summary": result.summary,
        "thresholds": result.thresholds,
        "cases": [_case_to_dict(case) for case in result.cases],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_csv(result: ComparisonResult) -> str:
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for case in result.cases:
        writer.writerow(_case_csv_row(case))
    return buffer.getvalue()


def render_markdown(result: ComparisonResult) -> str:
    summary = result.summary
    lines: List[str] = [
        "# LLM Eval Drift Report",
        "",
        "## Summary",
        "",
        f"- Result: {'FAIL' if result.should_fail else 'PASS'}",
        f"- Total cases: {summary['total_cases']}",
        f"- Compared cases: {summary['compared_cases']}",
        f"- New failures: {summary['new_failures']}",
        f"- Fixed cases: {summary['fixed']}",
        f"- Score drops: {summary['score_drops']}",
        f"- Category drift: {summary['category_drifts']}",
        f"- Model changes: {summary['model_changes']}",
        f"- Latency regressions: {summary['latency_regressions']}",
        f"- Cost regressions: {summary['cost_regressions']}",
        f"- New cases: {summary['new_cases']}",
        f"- Missing cases: {summary['missing_cases']}",
        f"- Baseline pass rate: {_fmt_pct(summary.get('pass_rate_baseline'))}",
        f"- Current pass rate: {_fmt_pct(summary.get('pass_rate_current'))}",
        f"- Baseline average score: {_fmt_num(summary.get('average_score_baseline'))}",
        f"- Current average score: {_fmt_num(summary.get('average_score_current'))}",
        f"- Baseline total cost: {_fmt_money(summary.get('total_cost_usd_baseline'))}",
        f"- Current total cost: {_fmt_money(summary.get('total_cost_usd_current'))}",
    ]
    fail_reasons = summary.get("fail_reasons") or []
    if fail_reasons:
        lines.extend(["", "Fail reasons: " + ", ".join(str(reason) for reason in fail_reasons)])

    lines.extend(
        [
            "",
            "## Case Changes",
            "",
            "| case_id | status | signals | pass | score delta | category | model | latency | cost |",
            "| --- | --- | --- | --- | ---: | --- | --- | ---: | ---: |",
        ]
    )
    for case in result.cases:
        lines.append(
            "| {case_id} | {status} | {signals} | {passes} | {score_delta} | {category} | {model} | {latency} | {cost} |".format(
                case_id=_md(case.case_id),
                status=_md(case.status),
                signals=_md(", ".join(case.signals) or "-"),
                passes=_md(_transition(_bool(case.baseline), _bool(case.current))),
                score_delta=_fmt_signed(case.score_delta),
                category=_md(_transition(_category(case.baseline), _category(case.current))),
                model=_md(_transition(_model(case.baseline), _model(case.current))),
                latency=_fmt_pct_delta(case.latency_delta_pct),
                cost=_fmt_pct_delta(case.cost_delta_pct),
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_junit(result: ComparisonResult) -> str:
    failing_signals = {
        signal for flag, signal in JUNIT_FAILURE_SIGNALS.items() if bool(result.thresholds.get(flag))
    }
    failing_cases = [
        case for case in result.cases if _case_failure_signals(case, failing_signals)
    ]
    suite = ET.Element(
        "testsuite",
        {
            "name": "llm-eval-drift-radar",
            "tests": str(len(result.cases)),
            "failures": str(len(failing_cases)),
            "errors": "0",
            "skipped": "0",
        },
    )
    properties = ET.SubElement(suite, "properties")
    for key, value in sorted(result.summary.items()):
        ET.SubElement(properties, "property", {"name": str(key), "value": _junit_value(value)})

    for case in result.cases:
        testcase = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "llm_eval_drift_radar.drift",
                "name": case.case_id,
                "time": _junit_time(case),
            },
        )
        case_properties = ET.SubElement(testcase, "properties")
        for key, value in _case_junit_properties(case).items():
            ET.SubElement(case_properties, "property", {"name": key, "value": value})

        failure_signals = _case_failure_signals(case, failing_signals)
        if failure_signals:
            failure = ET.SubElement(
                testcase,
                "failure",
                {
                    "type": "llm_eval_drift_radar.drift",
                    "message": f"{case.status}: {', '.join(failure_signals)}",
                },
            )
            failure.text = _case_failure_text(case, failure_signals)

    return "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n" + ET.tostring(suite, encoding="unicode") + "\n"


def write_output(text: str, output_path: Optional[str], stream: Optional[TextIO] = None) -> None:
    if output_path:
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    else:
        (stream or sys.stdout).write(text)


def _case_to_dict(case: CaseDiff) -> Dict[str, Any]:
    return {
        "case_id": case.case_id,
        "status": case.status,
        "signals": list(case.signals),
        "baseline": _record_to_dict(case.baseline),
        "current": _record_to_dict(case.current),
        "score_delta": case.score_delta,
        "latency_delta_pct": case.latency_delta_pct,
        "cost_delta_pct": case.cost_delta_pct,
    }


def _record_to_dict(record: Optional[EvalRecord]) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    return {
        "case_id": record.case_id,
        "prompt": record.prompt,
        "expected": record.expected,
        "actual": record.actual,
        "score": record.score,
        "pass": record.passed,
        "category": record.category,
        "model": record.model,
        "latency_ms": record.latency_ms,
        "cost_usd": record.cost_usd,
    }


def _case_csv_row(case: CaseDiff) -> Dict[str, Any]:
    base = case.baseline
    cur = case.current
    return {
        "case_id": case.case_id,
        "status": case.status,
        "signals": ",".join(case.signals),
        "baseline_pass": "" if base is None or base.passed is None else str(base.passed).lower(),
        "current_pass": "" if cur is None or cur.passed is None else str(cur.passed).lower(),
        "baseline_score": _blankable(None if base is None else base.score),
        "current_score": _blankable(None if cur is None else cur.score),
        "score_delta": _blankable(case.score_delta),
        "baseline_category": "" if base is None else base.category,
        "current_category": "" if cur is None else cur.category,
        "baseline_model": "" if base is None else base.model,
        "current_model": "" if cur is None else cur.model,
        "baseline_latency_ms": _blankable(None if base is None else base.latency_ms),
        "current_latency_ms": _blankable(None if cur is None else cur.latency_ms),
        "latency_delta_pct": _blankable(case.latency_delta_pct),
        "baseline_cost_usd": _blankable(None if base is None else base.cost_usd),
        "current_cost_usd": _blankable(None if cur is None else cur.cost_usd),
        "cost_delta_pct": _blankable(case.cost_delta_pct),
    }


def _case_failure_signals(case: CaseDiff, failing_signals: set[str]) -> List[str]:
    return [signal for signal in case.signals if signal in failing_signals]


def _case_junit_properties(case: CaseDiff) -> Dict[str, str]:
    return {
        "status": case.status,
        "signals": ",".join(case.signals),
        "baseline_pass": _bool(case.baseline),
        "current_pass": _bool(case.current),
        "score_delta": _blankable(case.score_delta),
        "baseline_model": _model(case.baseline),
        "current_model": _model(case.current),
        "baseline_category": _category(case.baseline),
        "current_category": _category(case.current),
        "latency_delta_pct": _blankable(case.latency_delta_pct),
        "cost_delta_pct": _blankable(case.cost_delta_pct),
    }


def _case_failure_text(case: CaseDiff, failure_signals: List[str]) -> str:
    lines = [
        f"case_id: {case.case_id}",
        f"status: {case.status}",
        f"failure_signals: {', '.join(failure_signals)}",
        f"all_signals: {', '.join(case.signals) or '-'}",
        f"pass: {_transition(_bool(case.baseline), _bool(case.current))}",
        f"score_delta: {_fmt_signed(case.score_delta)}",
        f"category: {_transition(_category(case.baseline), _category(case.current))}",
        f"model: {_transition(_model(case.baseline), _model(case.current))}",
        f"latency_delta: {_fmt_pct_delta(case.latency_delta_pct)}",
        f"cost_delta: {_fmt_pct_delta(case.cost_delta_pct)}",
    ]
    return "\n".join(lines)


def _junit_time(case: CaseDiff) -> str:
    record = case.current or case.baseline
    latency_ms = None if record is None else record.latency_ms
    if latency_ms is None:
        return "0"
    return f"{latency_ms / 1000:.6g}"


def _junit_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


def _blankable(value: Optional[float]) -> str:
    return "" if value is None else f"{value:.6g}"


def _fmt_num(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.4g}"


def _fmt_money(value: Any) -> str:
    return "n/a" if value is None else f"${float(value):.6g}"


def _fmt_pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f}%"


def _fmt_signed(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.4g}"


def _fmt_pct_delta(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def _transition(left: str, right: str) -> str:
    if not left and not right:
        return "-"
    if left == right:
        return left
    return f"{left or '-'} -> {right or '-'}"


def _bool(record: Optional[EvalRecord]) -> str:
    if record is None or record.passed is None:
        return ""
    return "pass" if record.passed else "fail"


def _category(record: Optional[EvalRecord]) -> str:
    return "" if record is None else record.category


def _model(record: Optional[EvalRecord]) -> str:
    return "" if record is None else record.model


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")

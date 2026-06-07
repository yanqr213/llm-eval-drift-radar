import json
import os
import tempfile
import unittest

from llm_eval_drift_radar.core import (
    DEFAULT_THRESHOLDS,
    EvalRecord,
    compare_runs,
    load_records,
    load_thresholds,
)


class LoadRecordsTests(unittest.TestCase):
    def write_temp(self, suffix, content):
        handle = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8", newline="")
        try:
            handle.write(content)
            return handle.name
        finally:
            handle.close()

    def tearDown(self):
        for path in getattr(self, "paths", []):
            if os.path.exists(path):
                os.unlink(path)

    def remember(self, path):
        if not hasattr(self, "paths"):
            self.paths = []
        self.paths.append(path)
        return path

    def test_load_jsonl_records(self):
        path = self.remember(
            self.write_temp(
                ".jsonl",
                '{"case_id":"a","score":0.5,"pass":true,"latency_ms":10,"cost_usd":0.2}\n',
            )
        )
        records = load_records(path)
        self.assertEqual(records[0].case_id, "a")
        self.assertEqual(records[0].score, 0.5)
        self.assertTrue(records[0].passed)

    def test_load_jsonl_skips_blank_lines(self):
        path = self.remember(self.write_temp(".jsonl", '\n{"case_id":"a"}\n\n'))
        self.assertEqual(len(load_records(path)), 1)

    def test_load_csv_records(self):
        path = self.remember(
            self.write_temp(
                ".csv",
                "case_id,score,pass,category,model\nc1,0.9,fail,cat,m1\n",
            )
        )
        record = load_records(path)[0]
        self.assertEqual(record.case_id, "c1")
        self.assertEqual(record.score, 0.9)
        self.assertFalse(record.passed)
        self.assertEqual(record.category, "cat")

    def test_unsupported_format_raises(self):
        path = self.remember(self.write_temp(".txt", "case_id=a"))
        with self.assertRaisesRegex(ValueError, "unsupported input format"):
            load_records(path)

    def test_invalid_jsonl_raises(self):
        path = self.remember(self.write_temp(".jsonl", "{bad json\n"))
        with self.assertRaisesRegex(ValueError, "invalid JSONL"):
            load_records(path)

    def test_jsonl_non_object_raises(self):
        path = self.remember(self.write_temp(".jsonl", "[1,2]\n"))
        with self.assertRaisesRegex(ValueError, "row must be an object"):
            load_records(path)

    def test_missing_case_id_raises(self):
        path = self.remember(self.write_temp(".jsonl", '{"score":1}\n'))
        with self.assertRaisesRegex(ValueError, "missing required field"):
            load_records(path)

    def test_invalid_numeric_raises(self):
        path = self.remember(self.write_temp(".jsonl", '{"case_id":"a","score":"high"}\n'))
        with self.assertRaisesRegex(ValueError, "must be numeric"):
            load_records(path)

    def test_invalid_bool_raises(self):
        path = self.remember(self.write_temp(".jsonl", '{"case_id":"a","pass":"maybe"}\n'))
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            load_records(path)

    def test_load_thresholds_merges_defaults(self):
        path = self.remember(self.write_temp(".json", '{"score_drop": 0.2}'))
        thresholds = load_thresholds(path)
        self.assertEqual(thresholds["score_drop"], 0.2)
        self.assertEqual(thresholds["cost_regression_pct"], DEFAULT_THRESHOLDS["cost_regression_pct"])

    def test_load_thresholds_requires_object(self):
        path = self.remember(self.write_temp(".json", '["bad"]'))
        with self.assertRaisesRegex(ValueError, "must be an object"):
            load_thresholds(path)


class CompareRunsTests(unittest.TestCase):
    def record(self, case_id, score=1.0, passed=True, category="cat", model="m1", latency=100.0, cost=1.0):
        return EvalRecord(
            case_id=case_id,
            score=score,
            passed=passed,
            category=category,
            model=model,
            latency_ms=latency,
            cost_usd=cost,
        )

    def test_detects_new_failure(self):
        result = compare_runs([self.record("a", passed=True)], [self.record("a", passed=False)])
        case = result.cases[0]
        self.assertEqual(case.status, "new_failure")
        self.assertIn("new_failure", case.signals)
        self.assertTrue(result.should_fail)

    def test_detects_fixed_case(self):
        result = compare_runs([self.record("a", passed=False)], [self.record("a", passed=True)])
        self.assertEqual(result.cases[0].status, "fixed")
        self.assertEqual(result.summary["fixed"], 1)

    def test_detects_score_drop_at_threshold(self):
        result = compare_runs([self.record("a", score=0.9)], [self.record("a", score=0.85)])
        self.assertIn("score_drop", result.cases[0].signals)

    def test_score_drop_can_be_tuned(self):
        result = compare_runs(
            [self.record("a", score=0.9)],
            [self.record("a", score=0.85)],
            {"score_drop": 0.1},
        )
        self.assertNotIn("score_drop", result.cases[0].signals)

    def test_detects_category_drift(self):
        result = compare_runs([self.record("a", category="old")], [self.record("a", category="new")])
        self.assertIn("category_drift", result.cases[0].signals)

    def test_detects_model_change(self):
        result = compare_runs([self.record("a", model="m1")], [self.record("a", model="m2")])
        self.assertIn("model_change", result.cases[0].signals)

    def test_detects_latency_regression(self):
        result = compare_runs([self.record("a", latency=100)], [self.record("a", latency=130)])
        self.assertIn("latency_regression", result.cases[0].signals)
        self.assertAlmostEqual(result.cases[0].latency_delta_pct, 30.0)

    def test_detects_cost_regression(self):
        result = compare_runs([self.record("a", cost=1.0)], [self.record("a", cost=1.25)])
        self.assertIn("cost_regression", result.cases[0].signals)
        self.assertAlmostEqual(result.cases[0].cost_delta_pct, 25.0)

    def test_zero_baseline_latency_avoids_percent_delta(self):
        result = compare_runs([self.record("a", latency=0)], [self.record("a", latency=10)])
        self.assertIsNone(result.cases[0].latency_delta_pct)
        self.assertNotIn("latency_regression", result.cases[0].signals)

    def test_detects_new_case(self):
        result = compare_runs([], [self.record("new")])
        self.assertEqual(result.cases[0].status, "new_case")
        self.assertEqual(result.summary["new_cases"], 1)

    def test_detects_missing_case(self):
        result = compare_runs([self.record("old")], [])
        self.assertEqual(result.cases[0].status, "missing_case")
        self.assertEqual(result.summary["missing_cases"], 1)

    def test_duplicate_baseline_case_id_raises(self):
        with self.assertRaisesRegex(ValueError, "duplicate case_id"):
            compare_runs([self.record("a"), self.record("a")], [self.record("a")])

    def test_duplicate_current_case_id_raises(self):
        with self.assertRaisesRegex(ValueError, "duplicate case_id"):
            compare_runs([self.record("a")], [self.record("a"), self.record("a")])

    def test_fail_policy_can_include_model_changes(self):
        result = compare_runs(
            [self.record("a", model="m1")],
            [self.record("a", model="m2")],
            {"fail_on_model_changes": True, "fail_on_score_drops": False},
        )
        self.assertTrue(result.should_fail)
        self.assertIn("model_changes", result.summary["fail_reasons"])

    def test_fail_policy_can_ignore_new_failures(self):
        result = compare_runs(
            [self.record("a", passed=True)],
            [self.record("a", passed=False)],
            {"fail_on_new_failures": False, "fail_on_score_drops": False},
        )
        self.assertFalse(result.should_fail)

    def test_summary_rates_and_averages(self):
        baseline = [self.record("a", score=1.0, passed=True, cost=0.1), self.record("b", score=0.5, passed=False, cost=0.2)]
        current = [self.record("a", score=0.8, passed=True, cost=0.3), self.record("b", score=0.7, passed=True, cost=0.4)]
        result = compare_runs(baseline, current, {"fail_on_score_drops": False})
        self.assertEqual(result.summary["pass_rate_baseline"], 0.5)
        self.assertEqual(result.summary["pass_rate_current"], 1.0)
        self.assertEqual(result.summary["average_score_baseline"], 0.75)
        self.assertAlmostEqual(result.summary["total_cost_usd_current"], 0.7)

    def test_cases_sorted_by_case_id(self):
        result = compare_runs([self.record("b"), self.record("a")], [self.record("b"), self.record("a")])
        self.assertEqual([case.case_id for case in result.cases], ["a", "b"])


if __name__ == "__main__":
    unittest.main()


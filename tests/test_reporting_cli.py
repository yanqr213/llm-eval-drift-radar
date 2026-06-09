import csv
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from xml.etree import ElementTree as ET

from llm_eval_drift_radar.cli import main
from llm_eval_drift_radar.core import EvalRecord, compare_runs
from llm_eval_drift_radar.reporting import render_csv, render_json, render_junit, render_markdown


class ReportingTests(unittest.TestCase):
    def result(self):
        baseline = [
            EvalRecord(case_id="a", score=0.9, passed=True, category="old", model="m1", latency_ms=100, cost_usd=1),
            EvalRecord(case_id="b", score=0.8, passed=False, category="cat", model="m1", latency_ms=100, cost_usd=1),
        ]
        current = [
            EvalRecord(case_id="a", score=0.7, passed=False, category="new", model="m2", latency_ms=140, cost_usd=1.5),
            EvalRecord(case_id="b", score=0.9, passed=True, category="cat", model="m1", latency_ms=90, cost_usd=0.8),
        ]
        return compare_runs(baseline, current, {"fail_on_category_drift": True})

    def test_render_json_has_summary_and_cases(self):
        payload = json.loads(render_json(self.result()))
        self.assertIn("summary", payload)
        self.assertEqual(payload["cases"][0]["case_id"], "a")
        self.assertEqual(payload["cases"][0]["baseline"]["model"], "m1")

    def test_render_csv_has_expected_fields(self):
        rows = list(csv.DictReader(io.StringIO(render_csv(self.result()))))
        self.assertEqual(rows[0]["case_id"], "a")
        self.assertEqual(rows[0]["status"], "new_failure")
        self.assertIn("score_drop", rows[0]["signals"])

    def test_render_markdown_contains_summary(self):
        text = render_markdown(self.result())
        self.assertIn("# LLM Eval Drift Report", text)
        self.assertIn("Result: FAIL", text)
        self.assertIn("a | new_failure", text)

    def test_markdown_escapes_pipe_in_case_id(self):
        result = compare_runs([EvalRecord(case_id="a|b")], [EvalRecord(case_id="a|b")])
        self.assertIn("a\\|b", render_markdown(result))

    def test_render_junit_marks_configured_failures(self):
        suite = ET.fromstring(render_junit(self.result()))
        self.assertEqual(suite.tag, "testsuite")
        self.assertEqual(suite.attrib["tests"], "2")
        self.assertEqual(suite.attrib["failures"], "1")
        failure = suite.find("./testcase[@name='a']/failure")
        self.assertIsNotNone(failure)
        self.assertIn("new_failure", failure.attrib["message"])
        self.assertIn("score_delta", failure.text)

    def test_render_junit_honors_threshold_policy(self):
        result = compare_runs(
            [EvalRecord(case_id="a", score=1.0, passed=True)],
            [EvalRecord(case_id="a", score=0.5, passed=True)],
            {"fail_on_score_drops": False},
        )
        suite = ET.fromstring(render_junit(result))
        self.assertEqual(suite.attrib["failures"], "0")
        self.assertIsNone(suite.find("./testcase/failure"))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.paths = []

    def tearDown(self):
        for path in self.paths:
            if os.path.exists(path):
                os.unlink(path)

    def write_temp(self, suffix, content):
        handle = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8", newline="")
        try:
            handle.write(content)
            self.paths.append(handle.name)
            return handle.name
        finally:
            handle.close()

    def test_cli_outputs_markdown_to_stdout(self):
        baseline = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        current = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--baseline", baseline, "--current", current])
        self.assertEqual(code, 0)
        self.assertIn("LLM Eval Drift Report", buffer.getvalue())

    def test_cli_writes_output_file(self):
        baseline = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        current = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        output = self.write_temp(".md", "")
        code = main(["--baseline", baseline, "--current", current, "--output", output])
        self.assertEqual(code, 0)
        with open(output, "r", encoding="utf-8") as fh:
            self.assertIn("LLM Eval Drift Report", fh.read())

    def test_cli_creates_output_parent_directory(self):
        baseline = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        current = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        root = tempfile.mkdtemp()
        output = os.path.join(root, "nested", "report.md")
        self.paths.append(output)
        code = main(["--baseline", baseline, "--current", current, "--output", output])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(output))

    def test_cli_check_returns_one_on_failure(self):
        baseline = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        current = self.write_temp(".jsonl", '{"case_id":"a","score":0.1,"pass":false}\n')
        code = main(["--baseline", baseline, "--current", current, "--check", "--quiet"])
        self.assertEqual(code, 1)

    def test_cli_returns_zero_without_check_even_on_failure(self):
        baseline = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        current = self.write_temp(".jsonl", '{"case_id":"a","score":0.1,"pass":false}\n')
        code = main(["--baseline", baseline, "--current", current, "--quiet"])
        self.assertEqual(code, 0)

    def test_cli_returns_two_on_input_error(self):
        baseline = self.write_temp(".txt", "bad")
        current = self.write_temp(".jsonl", '{"case_id":"a"}\n')
        with redirect_stderr(io.StringIO()):
            code = main(["--baseline", baseline, "--current", current, "--quiet"])
        self.assertEqual(code, 2)

    def test_cli_json_format(self):
        baseline = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        current = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--baseline", baseline, "--current", current, "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["summary"]["total_cases"], 1)

    def test_cli_junit_format(self):
        baseline = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        current = self.write_temp(".jsonl", '{"case_id":"a","score":0.1,"pass":false}\n')
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--baseline", baseline, "--current", current, "--format", "junit"])
        self.assertEqual(code, 0)
        suite = ET.fromstring(buffer.getvalue())
        self.assertEqual(suite.attrib["failures"], "1")

    def test_cli_thresholds_file_changes_check_result(self):
        baseline = self.write_temp(".jsonl", '{"case_id":"a","score":1,"pass":true}\n')
        current = self.write_temp(".jsonl", '{"case_id":"a","score":0.96,"pass":true}\n')
        thresholds = self.write_temp(".json", '{"score_drop": 0.01, "fail_on_score_drops": true}')
        code = main(["--baseline", baseline, "--current", current, "--thresholds", thresholds, "--check", "--quiet"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

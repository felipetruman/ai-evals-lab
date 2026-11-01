"""Integration tests exercise the actual OpenTelemetry Python TracerProvider."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals_lab.attestation import verify_attestation
from evals_lab.engine import grade_trajectory
from evals_lab.otel import otlp_to_trajectory
from evals_lab.otel_sdk_demo import (
    FaultInjectingExporter,
    evaluate_sdk_run,
    execute_sdk_demo,
)
from opentelemetry.sdk.trace.export import SpanExportResult

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "examples/policy.json").read_text())


class TestOfficialOtelSdk(unittest.TestCase):
    def test_real_sdk_export_and_integrity(self):
        run = execute_sdk_demo()
        self.assertEqual(run.exporter_status["exported_spans"], 4)
        self.assertEqual(run.exporter_status["recorded_attempts"], 4)
        self.assertTrue(run.exporter_status["flush_completed"])
        self.assertEqual(run.exporter_status["export_failures"], 0)
        self.assertTrue(verify_attestation(run.otlp, run.attestation).complete)
        self.assertTrue(evaluate_sdk_run(run, POLICY)["passed"])
        self.assertEqual(run.effects["refunded"], 1)

    def test_real_sdk_correlation(self):
        run = execute_sdk_demo()
        spans = run.otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
        root = next(x for x in spans if x["name"] == "agent.run")
        tools = [x for x in spans if x["name"].startswith("execute_tool")]
        self.assertEqual(len(tools), 3)
        self.assertEqual({x["traceId"] for x in spans}, {root["traceId"]})
        self.assertEqual({x["parentSpanId"] for x in tools}, {root["spanId"]})
        self.assertEqual([x["status"]["code"] for x in tools], ["STATUS_CODE_OK"] * 3)
        self.assertEqual([x["step_id"] for x in otlp_to_trajectory(run.otlp)["calls"]],
                         ["fetch_order", "verify_identity", "issue_refund"])

    def test_sdk_safety_gate_is_not_just_a_report(self):
        run = execute_sdk_demo(skip_identity=True)
        self.assertEqual(run.effects, {"refunded": 0, "balance": 50.0})
        self.assertTrue(verify_attestation(run.otlp, run.attestation).complete)
        report = evaluate_sdk_run(run, POLICY)
        self.assertFalse(report["passed"])
        codes = {v["code"] for v in report["grade"]["violations"]}
        self.assertIn("TOOL_EXECUTION_FAILED", codes)
        self.assertIn("DEPENDENCY_NOT_SATISFIED", codes)

    def test_injected_drop_detected_despite_export_success(self):
        run = execute_sdk_demo(fault="drop_last")
        report = evaluate_sdk_run(run, POLICY)
        self.assertFalse(report["passed"])
        self.assertEqual(run.exporter_status["export_failures"], 0)
        self.assertEqual(run.exporter_status["injected_discards"], 1)
        self.assertEqual(run.exporter_status["exported_spans"], 3)
        self.assertIn("SPAN_LEDGER_MISMATCH", report["completeness"]["reasons"])
        self.assertIn("DROPPED_SPANS", report["completeness"]["reasons"])
        self.assertIsNone(report["grade"])

    def test_sdk_exporter_failure_detected_even_if_flush_returns_true(self):
        run = execute_sdk_demo(fault="exporter_failure")
        report = evaluate_sdk_run(run, POLICY)
        self.assertFalse(report["passed"])
        self.assertEqual(run.exporter_status["export_failures"], 1)
        self.assertTrue(run.exporter_status["flush_completed"])
        self.assertEqual(run.exporter_status["exported_spans"], 0)
        self.assertIn("RUN_NOT_FINALIZED", report["completeness"]["reasons"])
        self.assertIn("INVALID_TOOL_TRACE", report["completeness"]["reasons"])

    def test_sdk_always_off_sampler_rejects_unobserved_execution(self):
        run = execute_sdk_demo(fault="sampler_off")
        report = evaluate_sdk_run(run, POLICY)
        self.assertFalse(report["passed"])
        self.assertEqual(run.exporter_status["exported_spans"], 0)
        self.assertEqual(run.exporter_status["export_calls"], 0)
        self.assertEqual(run.exporter_status["recorded_attempts"], 4)
        self.assertIn("SAMPLING_NOT_DISABLED", report["completeness"]["reasons"])

    def test_sdk_off_cannot_forge_success_by_lack_of_errors(self):
        run = execute_sdk_demo(fault="sampler_off")
        self.assertEqual(run.exporter_status["export_failures"], 0)
        self.assertTrue(run.exporter_status["flush_completed"])
        self.assertFalse(evaluate_sdk_run(run, POLICY)["passed"])

    def test_duplicate_exported_span_is_detected(self):
        run = execute_sdk_demo()
        spans = run.otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
        spans.append(dict(spans[0]))
        reasons = verify_attestation(run.otlp, run.attestation).reasons
        self.assertIn("DUPLICATE_SPAN_ID", reasons)
        self.assertIn("EXPORT_HASH_MISMATCH", reasons)

    def test_mutated_sdk_span_is_detected(self):
        run = execute_sdk_demo()
        spans = run.otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
        next(span for span in spans if span["name"].startswith("execute_tool"))["attributes"][-1]["value"]["stringValue"] = '{"amount": 999}'
        self.assertIn("EXPORT_HASH_MISMATCH", verify_attestation(run.otlp, run.attestation).reasons)

    def test_fault_validation(self):
        with self.assertRaisesRegex(ValueError, "unsupported fault"):
            execute_sdk_demo("unrecognized")
        with self.assertRaisesRegex(ValueError, "unsupported exporter fault"):
            FaultInjectingExporter("unexpected")

    def test_exporter_reports_failure(self):
        exporter = FaultInjectingExporter("exporter_failure")
        self.assertEqual(exporter.export([object()]), SpanExportResult.FAILURE)
        self.assertEqual(exporter.export_failures, 1)
        self.assertEqual(exporter.export_calls, 1)

    def test_exporter_drop_is_only_once(self):
        exporter = FaultInjectingExporter("drop_last")
        self.assertEqual(exporter.export([1, 2]), SpanExportResult.SUCCESS)
        self.assertEqual(exporter.export([3]), SpanExportResult.SUCCESS)
        self.assertEqual(exporter.spans, [1, 3])
        self.assertEqual(exporter.discarded, 1)

    def test_cli_success(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = subprocess.run([sys.executable, "-m", "evals_lab.otel_sdk_demo",
                                   "--policy", str(ROOT / "examples/policy.json"),
                                   "--output-dir", temp], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads((Path(temp) / "evaluation.json").read_text())
            self.assertTrue(report["passed"])
            self.assertTrue((Path(temp) / "otlp.json").exists())
            self.assertTrue((Path(temp) / "attestation.json").exists())

    def test_cli_fault_modes_fail_closed(self):
        for fault in ("drop_last", "exporter_failure", "sampler_off"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                proc = subprocess.run([sys.executable, "-m", "evals_lab.otel_sdk_demo",
                                       "--policy", str(ROOT / "examples/policy.json"),
                                       "--fault", fault, "--output-dir", temp],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                report = json.loads((Path(temp) / "evaluation.json").read_text())
                self.assertFalse(report["passed"])

    def test_cli_skipped_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = subprocess.run([sys.executable, "-m", "evals_lab.otel_sdk_demo",
                                   "--policy", str(ROOT / "examples/policy.json"),
                                   "--skip-identity", "--output-dir", temp],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            report = json.loads((Path(temp) / "evaluation.json").read_text())
            self.assertEqual(report["effects"]["refunded"], 0)
            self.assertTrue(report["completeness"]["complete"])


if __name__ == "__main__":
    unittest.main()

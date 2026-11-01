import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals_lab.attestation import canonical_hash, verify_attestation
from evals_lab.demo import LocalRecorder, LocalRefundService, execute_demo
from evals_lab.engine import grade_trajectory
from evals_lab.otel import otlp_to_trajectory

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / 'examples/policy.json').read_text())


class TestInstrumentedDemo(unittest.TestCase):
    def setUp(self):
        self.trace, self.manifest, self.effects = execute_demo()

    def test_complete_successful_workflow(self):
        report = verify_attestation(self.trace, self.manifest)
        self.assertTrue(report.complete)
        self.assertEqual(report.expected_spans, 3)
        self.assertEqual(report.exported_spans, 3)
        self.assertEqual(self.effects['refunded'], 1)
        self.assertEqual(self.effects['balance'], 25.0)
        self.assertTrue(grade_trajectory(POLICY, otlp_to_trajectory(self.trace)).passed)

    def test_skipped_identity_blocks_refund_and_fails_grade(self):
        trace, manifest, effects = execute_demo(skip_identity=True)
        self.assertTrue(verify_attestation(trace, manifest).complete)
        self.assertEqual(effects['refunded'], 0)
        self.assertEqual(effects['balance'], 50.0)
        grade = grade_trajectory(POLICY, otlp_to_trajectory(trace))
        self.assertFalse(grade.passed)
        self.assertIn('TOOL_EXECUTION_FAILED', {v.code for v in grade.violations})
        self.assertIn('DEPENDENCY_NOT_SATISFIED', {v.code for v in grade.violations})

    def test_detects_missing_span(self):
        self.trace['resourceSpans'][0]['scopeSpans'][0]['spans'].pop()
        report = verify_attestation(self.trace, self.manifest)
        self.assertFalse(report.complete)
        self.assertIn('SPAN_LEDGER_MISMATCH', report.reasons)
        self.assertIn('EXPORT_HASH_MISMATCH', report.reasons)

    def test_detects_mutated_arguments(self):
        attrs = self.trace['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['attributes']
        attrs[-1]['value']['stringValue'] = '{"order_id": "UNEXPECTED"}'
        self.assertIn('EXPORT_HASH_MISMATCH', verify_attestation(self.trace, self.manifest).reasons)

    def test_detects_truncated_attestation(self):
        self.manifest['attempted_span_ids'].pop()
        self.assertIn('SPAN_LEDGER_MISMATCH', verify_attestation(self.trace, self.manifest).reasons)

    def test_detects_unfinalized(self):
        self.manifest['run_finalized'] = False
        self.assertIn('RUN_NOT_FINALIZED', verify_attestation(self.trace, self.manifest).reasons)

    def test_detects_dropped_spans(self):
        self.manifest['dropped_spans'] = 1
        self.assertIn('DROPPED_SPANS', verify_attestation(self.trace, self.manifest).reasons)

    def test_detects_sampling(self):
        self.manifest['sampling'] = 'unknown'
        self.assertIn('SAMPLING_NOT_DISABLED', verify_attestation(self.trace, self.manifest).reasons)

    def test_detects_cross_trace_id(self):
        self.trace['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['traceId'] = 'a'*32
        self.assertIn('TRACE_ID_MISMATCH', verify_attestation(self.trace, self.manifest).reasons)

    def test_detects_duplicate_span_id(self):
        spans = self.trace['resourceSpans'][0]['scopeSpans'][0]['spans']
        spans[1]['spanId'] = spans[0]['spanId']
        self.assertIn('DUPLICATE_SPAN_ID', verify_attestation(self.trace, self.manifest).reasons)

    def test_rejects_forged_empty_ledger(self):
        self.manifest['attempted_span_ids'] = []
        with self.assertRaisesRegex(ValueError, 'attempted_span_ids'):
            verify_attestation(self.trace, self.manifest)

    def test_rejects_noninteger_drop_count(self):
        self.manifest['dropped_spans'] = True
        with self.assertRaisesRegex(ValueError, 'dropped_spans'):
            verify_attestation(self.trace, self.manifest)

    def test_invalid_otlp_status_fails_closed(self):
        self.trace['resourceSpans'][0]['scopeSpans'][0]['spans'][1]['status'] = {'code':'STATUS_CODE_UNSET'}
        with self.assertRaisesRegex(ValueError, 'explicit OK or ERROR'):
            otlp_to_trajectory(self.trace)

    def test_failed_tool_does_not_satisfy_dependency(self):
        native = {'calls': [
            {'step_id':'fetch_order','tool':'orders.get','arguments':{'order_id':'ORDER-001'},'status':'error'},
            {'step_id':'verify_identity','tool':'identity.verify','arguments':{'customer_id':'CUSTOMER-001'}},
            {'step_id':'issue_refund','tool':'refunds.create','arguments':{'order_id':'ORDER-001','amount':25.0}},
        ]}
        grade = grade_trajectory(POLICY,native)
        self.assertFalse(grade.passed)
        self.assertIn('TOOL_EXECUTION_FAILED', {v.code for v in grade.violations})

    def test_recorder_cannot_append_after_finalization(self):
        rec = LocalRecorder()
        rec.call('fetch_order','orders.get',{'order_id':'ORDER-001'},LocalRefundService().get_order)
        rec.finish()
        with self.assertRaisesRegex(RuntimeError,'cannot record'):
            rec.call('fetch_order','orders.get',{'order_id':'ORDER-001'},LocalRefundService().get_order)

    def test_manifest_hash_does_not_depend_on_json_formatting(self):
        doc = json.loads(json.dumps(self.trace,indent=3))
        self.assertEqual(canonical_hash(doc),self.manifest['otlp_sha256'])

    def test_demo_command_success(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = subprocess.run([sys.executable,'-m','evals_lab.demo', '--policy',str(ROOT/'examples/policy.json'),
                                   '--output-dir',temp],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertTrue(json.loads((Path(temp)/'evaluation.json').read_text())['completeness']['complete'])

    def test_demo_command_safety_gate_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = subprocess.run([sys.executable,'-m','evals_lab.demo', '--policy',str(ROOT/'examples/policy.json'),
                                   '--output-dir',temp,'--skip-identity'],capture_output=True,text=True)
            self.assertEqual(proc.returncode,2,proc.stderr)
            self.assertEqual(json.loads((Path(temp)/'evaluation.json').read_text())['effects']['refunded'],0)

    def test_batch_attestation_passes_with_demo(self):
        from evals_lab.batch import evaluate_dataset
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'case.jsonl'
            p.write_text(json.dumps({'case_id':'real_local_demo','expected_pass':True,
                                     'otlp':self.trace,'attestation':self.manifest})+'\n')
            report=evaluate_dataset(POLICY,p)
            self.assertEqual(report.agreement,1.0)

    def test_batch_rejects_incomplete_attestation(self):
        from evals_lab.batch import evaluate_dataset
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'case.jsonl'
            self.manifest['dropped_spans']=1
            p.write_text(json.dumps({'case_id':'tampered','expected_pass':True,
                                     'otlp':self.trace,'attestation':self.manifest})+'\n')
            with self.assertRaisesRegex(ValueError,'attestation failed'):
                evaluate_dataset(POLICY,p)


if __name__ == '__main__':
    unittest.main()

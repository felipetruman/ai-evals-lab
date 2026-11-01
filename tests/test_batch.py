import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals_lab.batch import evaluate_dataset
from evals_lab.otel import otlp_to_trajectory

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples'


def fixture(name):
    return json.loads((EXAMPLES / name).read_text())


class TestBatch(unittest.TestCase):
    def setUp(self):
        self.policy = fixture('policy.json')

    def run_dataset(self, records):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'evals.jsonl'
            path.write_text(''.join(json.dumps(r) + '\n' for r in records))
            return evaluate_dataset(self.policy, path)

    def test_all_four_cases_agree(self):
        report = evaluate_dataset(self.policy, EXAMPLES / 'golden.jsonl')
        self.assertEqual(report.cases_total, 4)
        self.assertEqual(report.matched, 4)
        self.assertEqual(report.mismatched, 0)
        self.assertEqual(report.agreement, 1.0)
        self.assertEqual(report.actual_passed, 2)
        self.assertEqual(report.actual_failed, 2)

    def test_otlp_reverses_spans_by_start_time(self):
        trace = otlp_to_trajectory(fixture('otel_valid.json'))
        self.assertEqual([c['step_id'] for c in trace['calls']],
                         ['fetch_order', 'verify_identity', 'issue_refund'])

    def test_jsonl_mismatch(self):
        report = self.run_dataset([{'case_id':'bad_label','expected_pass':True,
                                    'trace':fixture('missing_safety.json')}])
        self.assertEqual(report.mismatched, 1)
        self.assertEqual(report.agreement, 0)

    def test_duplicate_case_id_rejected(self):
        row = {'case_id':'dup','expected_pass':True,'trace':fixture('valid.json')}
        with self.assertRaisesRegex(ValueError, 'duplicate case_id'):
            self.run_dataset([row,row])

    def test_missing_label_rejected(self):
        with self.assertRaisesRegex(ValueError, 'expected_pass'):
            self.run_dataset([{'case_id':'a','trace':fixture('valid.json')}])

    def test_bad_trace_format_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.run_dataset([{'case_id':'a','expected_pass':True,'trace':{},'otlp':{}}])

    def test_empty_dataset_rejected(self):
        with self.assertRaisesRegex(ValueError, 'at least one'):
            self.run_dataset([])

    def test_blank_line_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.jsonl'
            path.write_text('\n')
            with self.assertRaisesRegex(ValueError, 'blank record'):
                evaluate_dataset(self.policy, path)

    def test_otlp_missing_step_id_rejected(self):
        doc = fixture('otel_valid.json')
        doc['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['attributes'] = [
            a for a in doc['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['attributes']
            if a['key'] != 'evals.step_id'
        ]
        with self.assertRaisesRegex(ValueError, 'evals.step_id'):
            otlp_to_trajectory(doc)

    def test_otlp_missing_arguments_rejected(self):
        doc = fixture('otel_valid.json')
        doc['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['attributes'] = [
            a for a in doc['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['attributes']
            if a['key'] != 'gen_ai.tool.call.arguments'
        ]
        with self.assertRaisesRegex(ValueError, 'requires object arguments'):
            otlp_to_trajectory(doc)

    def test_otlp_mixed_trace_id_rejected(self):
        doc = fixture('otel_valid.json')
        doc['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['traceId'] = 'e'*32
        with self.assertRaisesRegex(ValueError, 'one traceId'):
            otlp_to_trajectory(doc)

    def test_otlp_duplicate_span_id_rejected(self):
        doc = fixture('otel_valid.json')
        spans = doc['resourceSpans'][0]['scopeSpans'][0]['spans']
        spans[1]['spanId'] = spans[0]['spanId']
        with self.assertRaisesRegex(ValueError, 'duplicate spanId'):
            otlp_to_trajectory(doc)

    def test_otlp_invalid_json_arguments_rejected(self):
        doc = fixture('otel_valid.json')
        doc['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['attributes'][-1]['value']['stringValue'] = 'not-json'
        with self.assertRaisesRegex(ValueError, 'not valid JSON'):
            otlp_to_trajectory(doc)

    def test_otlp_object_args(self):
        doc = fixture('otel_valid.json')
        attr = doc['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['attributes'][-1]
        arguments = json.loads(attr['value']['stringValue'])
        attr['value'] = {'kvlistValue': {'values': [
            {'key': k, 'value': {'stringValue': v} if isinstance(v, str) else {'doubleValue': v}}
            for k,v in arguments.items()
        ]}}
        self.assertEqual(len(otlp_to_trajectory(doc)['calls']), 3)

    def test_non_tool_span_is_ignored(self):
        doc = fixture('otel_valid.json')
        doc['resourceSpans'][0]['scopeSpans'][0]['spans'].append({
            'traceId':'f'*32,'spanId':'abcabcabcabcabca',
            'name':'LLM chat','attributes':[{'key':'gen_ai.operation.name','value':{'stringValue':'chat'}}]
        })
        self.assertEqual(len(otlp_to_trajectory(doc)['calls']), 3)

    def test_batch_cli_success_and_failure_codes(self):
        base = [sys.executable, '-m', 'evals_lab.batch_cli', '--policy',str(EXAMPLES/'policy.json')]
        good = subprocess.run(base+['--dataset',str(EXAMPLES/'golden.jsonl')],capture_output=True,text=True)
        self.assertEqual(good.returncode, 0,good.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp)/'wrong.jsonl'
            dataset.write_text(json.dumps({'case_id':'mismatch','expected_pass':True,
                                           'trace':fixture('missing_safety.json')})+'\n')
            bad = subprocess.run(base+['--dataset',str(dataset)],capture_output=True,text=True)
            self.assertEqual(bad.returncode, 2,bad.stderr)
            self.assertEqual(json.loads(bad.stdout)['mismatched'], 1)


if __name__ == '__main__':
    unittest.main()

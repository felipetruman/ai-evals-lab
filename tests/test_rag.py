import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals_lab.rag import evaluate_case, evaluate_jsonl

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'examples/rag-adversarial.jsonl'


def fixture():
    return [json.loads(line) for line in FIXTURE.read_text(encoding='utf-8').splitlines()]


class TestRagAdversarial(unittest.TestCase):
    def setUp(self):
        self.rows = fixture()

    def mutate(self, index, **response_changes):
        row = copy.deepcopy(self.rows[index])
        row['response'].update(response_changes)
        return row

    def test_all_labels_agree(self):
        report = evaluate_jsonl(FIXTURE)
        self.assertEqual(report.cases_total, 14)
        self.assertEqual(report.agreement, 1.0)
        self.assertEqual(report.mismatched, 0)
        self.assertEqual(report.passed, 5)
        self.assertEqual(report.failed, 9)
        self.assertEqual(report.abstention_required, 6)
        self.assertEqual(report.correct_abstentions, 3)

    def test_answer_and_citation(self):
        verdict = evaluate_case(self.rows[0])
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.supporting_source_ids, ['source-a'])

    def test_consistent_multiple_sources(self):
        self.assertTrue(evaluate_case(self.rows[1]).passed)

    def test_safe_abstention_when_missing_evidence(self):
        verdict = evaluate_case(self.rows[2])
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.required_reason, 'insufficient_evidence')

    def test_irrelevant_documents_require_abstention(self):
        self.assertTrue(evaluate_case(self.rows[3]).passed)

    def test_conflict_requires_abstention(self):
        verdict = evaluate_case(self.rows[4])
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.required_reason, 'conflicting_evidence')

    def test_no_citation_detected(self):
        self.assertIn('MISSING_CITATION', evaluate_case(self.rows[5]).violations)

    def test_unsupported_value_detected(self):
        self.assertIn('UNSUPPORTED_ANSWER', evaluate_case(self.rows[6]).violations)

    def test_missed_conflict_abstention(self):
        self.assertIn('MISSED_ABSTENTION_ON_CONFLICT', evaluate_case(self.rows[7]).violations)

    def test_irrelevant_citation(self):
        self.assertIn('IRRELEVANT_CITATION', evaluate_case(self.rows[8]).violations)

    def test_no_evidence_missed_abstention(self):
        self.assertIn('MISSED_ABSTENTION_WITHOUT_EVIDENCE', evaluate_case(self.rows[9]).violations)

    def test_invented_citation(self):
        self.assertIn('UNKNOWN_CITATION', evaluate_case(self.rows[10]).violations)

    def test_unnecessary_abstention(self):
        self.assertIn('UNNECESSARY_ABSTENTION', evaluate_case(self.rows[11]).violations)

    def test_wrong_abstention_reason(self):
        self.assertIn('INCORRECT_ABSTENTION_REASON', evaluate_case(self.rows[12]).violations)

    def test_duplicate_citation(self):
        self.assertIn('DUPLICATE_CITATION', evaluate_case(self.rows[13]).violations)

    def test_abstention_cannot_cite_docs(self):
        row = self.mutate(2, citations=['source-a'])
        self.assertIn('UNKNOWN_CITATION', evaluate_case(row).violations)
        self.assertIn('CITATION_ON_ABSTENTION', evaluate_case(row).violations)

    def test_malformed_documents_rejected(self):
        row = copy.deepcopy(self.rows[0])
        row['context'].append(row['context'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate document'):
            evaluate_case(row)

    def test_duplicate_fact_rejected(self):
        row = copy.deepcopy(self.rows[0])
        row['context'][0]['facts'].append(copy.deepcopy(row['context'][0]['facts'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate fact'):
            evaluate_case(row)

    def test_unknown_abstention_reason_rejected(self):
        with self.assertRaisesRegex(ValueError, 'recognized'):
            evaluate_case(self.mutate(2, reason='unknown'))

    def test_wrong_type_rejected(self):
        with self.assertRaisesRegex(ValueError, 'response.value'):
            evaluate_case(self.mutate(0, value=30))

    def test_no_raw_context_in_reports(self):
        report = evaluate_jsonl(FIXTURE).to_dict()
        self.assertNotIn('refund_days', json.dumps(report))
        self.assertNotIn('source-a', json.dumps(report))

    def test_duplicate_case_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'cases.jsonl'
            p.write_text('\n'.join(json.dumps(x) for x in [self.rows[0],self.rows[0]])+'\n')
            with self.assertRaisesRegex(ValueError, 'duplicate case_id'):
                evaluate_jsonl(p)

    def test_label_mismatch_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'cases.jsonl'
            row=copy.deepcopy(self.rows[0]);row['expected_pass']=False
            p.write_text(json.dumps(row)+'\n')
            self.assertEqual(evaluate_jsonl(p).mismatched,1)

    def test_cli_succeeds_with_gold_fixture(self):
        cmd=[sys.executable,'-m','evals_lab.rag_cli','--dataset',str(FIXTURE)]
        proc=subprocess.run(cmd,capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['agreement'],1)

    def test_cli_gate_blocks_label_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.jsonl'
            row=copy.deepcopy(self.rows[0]);row['expected_pass']=False
            p.write_text(json.dumps(row)+'\n')
            proc=subprocess.run([sys.executable,'-m','evals_lab.rag_cli','--dataset',str(p)],capture_output=True)
            self.assertEqual(proc.returncode,2)

    def test_cli_bad_input_exit_one(self):
        proc=subprocess.run([sys.executable,'-m','evals_lab.rag_cli','--dataset','/missing/file.jsonl'],capture_output=True)
        self.assertEqual(proc.returncode,1)


if __name__=='__main__':
    unittest.main()

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals_lab.rag import evaluate_case, evaluate_jsonl

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/rag-adversarial.jsonl"

def fixture():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]

class TestRagAdversarial(unittest.TestCase):
    def setUp(self):
        self.rows = fixture()

    def mutate(self, index, **changes):
        row = copy.deepcopy(self.rows[index])
        row["response"].update(changes)
        return row

    def test_all_labels_agree(self):
        r = evaluate_jsonl(FIXTURE)
        self.assertEqual((r.cases_total, r.matched, r.passed, r.failed), (14, 14, 5, 9))
        self.assertEqual((r.abstention_required, r.correct_abstentions), (6, 3))
        self.assertEqual(r.agreement, 1.0)

    def test_answer_and_citation(self):
        v = evaluate_case(self.rows[0])
        self.assertTrue(v.passed)
        self.assertEqual(v.supporting_source_ids, ["source-a"])

    def test_consistent_sources(self):
        self.assertTrue(evaluate_case(self.rows[1]).passed)

    def test_missing_evidence_abstention(self):
        self.assertEqual(evaluate_case(self.rows[2]).required_reason, "insufficient_evidence")
        self.assertTrue(evaluate_case(self.rows[2]).passed)

    def test_irrelevant_documents_abstention(self):
        self.assertTrue(evaluate_case(self.rows[3]).passed)

    def test_conflicting_documents_abstention(self):
        self.assertEqual(evaluate_case(self.rows[4]).required_reason, "conflicting_evidence")
        self.assertTrue(evaluate_case(self.rows[4]).passed)

    def test_missing_citation(self):
        self.assertIn("MISSING_CITATION", evaluate_case(self.rows[5]).violations)

    def test_unsupported_answer(self):
        self.assertIn("UNSUPPORTED_ANSWER", evaluate_case(self.rows[6]).violations)

    def test_conflict_missed(self):
        self.assertIn("MISSED_ABSTENTION_ON_CONFLICT", evaluate_case(self.rows[7]).violations)

    def test_irrelevant_citation(self):
        self.assertIn("IRRELEVANT_CITATION", evaluate_case(self.rows[8]).violations)

    def test_missing_evidence_missed(self):
        self.assertIn("MISSED_ABSTENTION_WITHOUT_EVIDENCE", evaluate_case(self.rows[9]).violations)

    def test_fake_source(self):
        self.assertIn("UNKNOWN_CITATION", evaluate_case(self.rows[10]).violations)

    def test_unnecessary_abstention(self):
        self.assertIn("UNNECESSARY_ABSTENTION", evaluate_case(self.rows[11]).violations)

    def test_wrong_abstention_reason(self):
        self.assertIn("INCORRECT_ABSTENTION_REASON", evaluate_case(self.rows[12]).violations)

    def test_duplicate_citation(self):
        self.assertIn("DUPLICATE_CITATION", evaluate_case(self.rows[13]).violations)

    def test_abstention_with_citation_fails(self):
        row = self.mutate(2, citations=["source-a"])
        codes = evaluate_case(row).violations
        self.assertIn("UNKNOWN_CITATION", codes)
        self.assertIn("CITATION_ON_ABSTENTION", codes)

    def test_duplicate_document_rejected(self):
        row = copy.deepcopy(self.rows[0])
        row["context"].append(row["context"][0])
        with self.assertRaisesRegex(ValueError, "duplicate document"):
            evaluate_case(row)

    def test_duplicate_fact_rejected(self):
        row = copy.deepcopy(self.rows[0])
        row["context"][0]["facts"].append(row["context"][0]["facts"][0])
        with self.assertRaisesRegex(ValueError, "duplicate fact"):
            evaluate_case(row)

    def test_unknown_abstention_reason_rejected(self):
        with self.assertRaisesRegex(ValueError, "recognized"):
            evaluate_case(self.mutate(2, reason="unknown"))

    def test_wrong_value_type_rejected(self):
        with self.assertRaisesRegex(ValueError, "response.value"):
            evaluate_case(self.mutate(0, value=30))

    def test_report_omits_raw_facts(self):
        report = json.dumps(evaluate_jsonl(FIXTURE).to_dict())
        self.assertNotIn("refund_days", report)
        self.assertNotIn("source-a", report)

    def test_duplicate_case_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"duplicates.jsonl"
            path.write_text(json.dumps(self.rows[0])+"\n"+json.dumps(self.rows[0])+"\n")
            with self.assertRaisesRegex(ValueError, "duplicate case_id"):
                evaluate_jsonl(path)

    def test_mislabeled_case_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"bad.jsonl"
            row = copy.deepcopy(self.rows[0]); row["expected_pass"] = False
            path.write_text(json.dumps(row)+"\n")
            self.assertEqual(evaluate_jsonl(path).mismatched, 1)

    def test_cli_success(self):
        p = subprocess.run([sys.executable,"-m","evals_lab.rag_cli","--dataset",str(FIXTURE)],
                           capture_output=True,text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["agreement"], 1)

    def test_cli_mismatch_exit_two(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"bad.jsonl"
            row = copy.deepcopy(self.rows[0]);row["expected_pass"] = False
            path.write_text(json.dumps(row)+"\n")
            p = subprocess.run([sys.executable,"-m","evals_lab.rag_cli","--dataset",str(path)],
                               capture_output=True)
            self.assertEqual(p.returncode, 2)

    def test_cli_input_error_exit_one(self):
        p = subprocess.run([sys.executable,"-m","evals_lab.rag_cli","--dataset","/missing/file"],
                           capture_output=True)
        self.assertEqual(p.returncode, 1)

if __name__ == "__main__":
    unittest.main()

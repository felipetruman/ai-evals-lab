"""Deterministic, synthetic-only validation of corpus leakage checks."""
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals_lab.contamination import normalize_text, read_corpus, scan

ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "examples/contamination-train.jsonl"
CLEAN = ROOT / "examples/contamination-eval-clean.jsonl"
LEAKED = ROOT / "examples/contamination-eval-leaked.jsonl"


class TestContamination(unittest.TestCase):
    def setUp(self):
        self.train = read_corpus(TRAIN)
        self.clean = read_corpus(CLEAN)
        self.leaked = read_corpus(LEAKED)

    def read_rows(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rows.jsonl"
            target.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            return read_corpus(target)

    def test_synthetic_clean(self):
        report = scan(self.train, self.clean)
        self.assertEqual((report.flagged_pair_count, report.high_severity_pairs), (0, 0))
        self.assertTrue(report.semantic_available)
        self.assertEqual(report.compared_pairs, 6)
        self.assertEqual(len(report.train_sha256), 64)

    def test_leakage_detected_by_three_methods(self):
        report = scan(self.train, self.leaked)
        matched = {m.eval_id: m for m in report.matches if m.train_id == 'train-01'}
        self.assertEqual(report.flagged_eval_count, 3)
        self.assertIn('EXACT_NORMALIZED', matched['eval-leak-exact'].signals)
        self.assertIn('NGRAM_CONTAINMENT', matched['eval-leak-containment'].signals)
        self.assertEqual(matched['eval-leak-semantic'].severity, 'review')
        self.assertEqual(matched['eval-leak-semantic'].signals, ['EMBEDDING_COSINE'])
        self.assertGreater(matched['eval-leak-semantic'].cosine_similarity, 0.97)

    def test_normalization(self):
        self.assertEqual(normalize_text('  ＡＢＣ\t A  '), 'abc a')
        self.assertEqual(normalize_text('STRASSE'), normalize_text('straße'))

    def test_nonsemantic_records(self):
        rows = self.read_rows([{'id': 'one', 'text': 'Repeat this exact sentence over again'}])
        report = scan(rows, rows)
        self.assertFalse(report.semantic_available)
        self.assertIn('EXACT_NORMALIZED', report.matches[0].signals)

    def test_shuffled_order_stable_hash(self):
        corpus = list(reversed(self.train))
        self.assertEqual(scan(self.train, self.clean).train_sha256, scan(corpus, self.clean).train_sha256)

    def test_same_id_not_automatically_ignored(self):
        rows = self.read_rows([{'id': 'train-01', 'text': 'Agent must verify customer identity before processing any refund request.'}])
        other = self.read_rows([{'id': 'train-01', 'text': 'Agent must verify customer identity before processing any refund request.'}])
        self.assertEqual(scan(rows, other).flagged_pair_count, 1)

    def test_duplicate_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.read_rows([{'id':'same','text':'one'}, {'id':'same','text':'two'}])

    def test_blank_line_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'bad.jsonl'
            target.write_text('{"id":"a","text":"text"}\n\n')
            with self.assertRaisesRegex(ValueError, 'blank'):
                read_corpus(target)

    def test_bad_json_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'bad.jsonl'
            target.write_text('unquoted\n')
            with self.assertRaisesRegex(ValueError, 'invalid JSON'):
                read_corpus(target)

    def test_missing_text_rejected(self):
        with self.assertRaisesRegex(ValueError, 'text'):
            self.read_rows([{'id':'a'}])

    def test_punctuation_only_rejected(self):
        with self.assertRaisesRegex(ValueError, 'words'):
            self.read_rows([{'id':'a','text':'!?..'}])

    def test_zero_vector_rejected(self):
        with self.assertRaisesRegex(ValueError, 'norm'):
            self.read_rows([{'id':'a','text':'text','embedding':[0,0]}])

    def test_invalid_vector_rejected(self):
        with self.assertRaisesRegex(ValueError, 'embedding'):
            self.read_rows([{'id':'a','text':'text','embedding':[True,1]}])

    def test_non_finite_vector_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid JSON'):
            self.read_rows([{'id':'a','text':'text','embedding':[float('nan'),1]}])

    def test_mixed_embedding_coverage_rejected(self):
        other = self.read_rows([{'id':'a','text':'Distinct words and long narrative'}])
        with self.assertRaisesRegex(ValueError, 'partial embedding coverage'):
            scan(self.train, other)

    def test_wrong_embedding_dimension_rejected(self):
        other = self.read_rows([{'id':'a','text':'Distinct words and long narrative', 'embedding':[0,1]}])
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            scan(self.train, other)

    def test_pair_limit_rejected(self):
        with self.assertRaisesRegex(ValueError, 'max_pairs'):
            scan(self.train, self.leaked, max_pairs=8)

    def test_invalid_thresholds_rejected(self):
        for threshold in [0, -1, 1.01, float('nan'), True]:
            with self.subTest(threshold=threshold):
                with self.assertRaises(ValueError):
                    scan(self.train, self.clean, cosine_threshold=threshold)

    def test_invalid_gram_size_rejected(self):
        with self.assertRaises(ValueError):
            scan(self.train, self.clean, ngram_size=1)

    def test_min_shared_gram_blocks_tiny_containment_false_positive(self):
        a=self.read_rows([{'id':'a','text':'quick brown fox jumps'}])
        b=self.read_rows([{'id':'b','text':'quick brown fox runs'}])
        report=scan(a,b,ngram_size=3,min_shared_ngrams=2, containment_threshold=0.5)
        self.assertEqual(report.flagged_pair_count,0)

    def test_cosine_scaling_invariant(self):
        a=self.read_rows([{'id':'a','text':'one two three four','embedding':[2,0]}])
        b=self.read_rows([{'id':'b','text':'five six seven eight','embedding':[0.5,0]}])
        self.assertIn('EMBEDDING_COSINE', scan(a,b).matches[0].signals)

    def test_cli_valid(self):
        cmd=[sys.executable,'-m','evals_lab.contamination_cli','--train',str(TRAIN),'--eval',str(CLEAN)]
        result=subprocess.run(cmd,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['flagged_eval_count'],0)

    def test_cli_flags_leakage(self):
        cmd=[sys.executable,'-m','evals_lab.contamination_cli','--train',str(TRAIN),'--eval',str(LEAKED)]
        result=subprocess.run(cmd,capture_output=True,text=True)
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertEqual(json.loads(result.stdout)['flagged_eval_count'],3)

    def test_cli_fail_on_high_only(self):
        cmd=[sys.executable,'-m','evals_lab.contamination_cli','--train',str(TRAIN),'--eval',str(LEAKED),'--fail-on','none']
        result=subprocess.run(cmd,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_cli_invalid_input(self):
        cmd=[sys.executable,'-m','evals_lab.contamination_cli','--train',str(TRAIN),'--eval','/missing/does-not-exist']
        result=subprocess.run(cmd,capture_output=True,text=True)
        self.assertEqual(result.returncode,1)


class TestReviewLabels(unittest.TestCase):
    def setUp(self):
        self.train = read_corpus(TRAIN)
        self.eval_rows = read_corpus(CLEAN) + read_corpus(LEAKED)

    def test_labeled_precision_recall_and_specificity(self):
        from evals_lab.contamination import review_against_labels
        report = scan(self.train, self.eval_rows)
        review = review_against_labels(report, self.train, self.eval_rows,
                                       ROOT/'examples/contamination-reviewed-pairs.jsonl')
        self.assertEqual((review.true_positives, review.false_positives,
                          review.true_negatives, review.false_negatives), (3, 0, 12, 0))
        self.assertEqual((review.precision, review.recall, review.specificity), (1.0, 1.0, 1.0))

    def test_reject_unknown_review_pair(self):
        from evals_lab.contamination import review_against_labels
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'labels.jsonl'
            p.write_text(json.dumps({'train_id':'unlisted','eval_id':'eval-clean-01','contaminated':False,'reviewer':'test'})+'\n')
            with self.assertRaisesRegex(ValueError,'unknown or duplicate'):
                review_against_labels(scan(self.train,self.eval_rows),self.train,self.eval_rows,p)

    def test_reject_non_boolean_labels(self):
        from evals_lab.contamination import review_against_labels
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'labels.jsonl'
            p.write_text(json.dumps({'train_id':'train-01','eval_id':'eval-clean-01','contaminated':1,'reviewer':'test'})+'\n')
            with self.assertRaisesRegex(ValueError,'boolean'):
                review_against_labels(scan(self.train,self.eval_rows),self.train,self.eval_rows,p)

    def test_cli_review_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=Path(tmp)/'combined.jsonl'
            e.write_text(CLEAN.read_text()+LEAKED.read_text())
            cmd=[sys.executable,'-m','evals_lab.contamination_cli','--train',str(TRAIN),'--eval',str(e),
                 '--review-labels',str(ROOT/'examples/contamination-reviewed-pairs.jsonl'),'--fail-on','none']
            r=subprocess.run(cmd,text=True,capture_output=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads(r.stdout)['labeled_review']['reviewed_pairs'],15)


if __name__ == '__main__':
    unittest.main()

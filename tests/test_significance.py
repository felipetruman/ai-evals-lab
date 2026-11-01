"""Regression tests for the paired, reproducible significance engine."""

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals_lab.significance import Pair, compare_pairs, exact_mcnemar_p_value, read_pairs

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'examples' / 'synthetic-model-comparison.jsonl'


class TestSignificance(unittest.TestCase):
    def test_fixture_counts(self):
        pairs = read_pairs(FIXTURE)
        self.assertEqual(len(pairs), 100)
        r = compare_pairs(pairs, resamples=1000)
        self.assertEqual((r.success_a, r.success_b), (65, 85))
        self.assertEqual((r.discordant_a_only, r.discordant_b_only), (5, 25))
        self.assertEqual(r.delta_pp, 20)
        self.assertTrue(r.statistically_significant)
        self.assertEqual(r.direction, 'B')
        self.assertGreater(r.ci_low_pp, 0)
        self.assertLessEqual(r.ci_high_pp, 100)

    def test_exact_p_6_to_0(self):
        self.assertAlmostEqual(exact_mcnemar_p_value(0, 6), 0.03125)

    def test_exact_p_all_agree(self):
        self.assertEqual(exact_mcnemar_p_value(0, 0), 1)

    def test_exact_p_balanced(self):
        self.assertEqual(exact_mcnemar_p_value(4, 4), 1)

    def test_exact_p_single_discordance(self):
        self.assertEqual(exact_mcnemar_p_value(0, 1), 1)

    def test_exact_p_small_asymmetric(self):
        self.assertAlmostEqual(exact_mcnemar_p_value(1, 7), 18 / 256)

    def test_exact_p_large_n_finite(self):
        p = exact_mcnemar_p_value(50, 80)
        self.assertTrue(0 <= p <= 1 and math.isfinite(p))

    def test_exact_p_rejects_nonint(self):
        for a, b in [(True, 1), (1, -1), (0.0, 5)]:
            with self.subTest(a=a,b=b), self.assertRaises(ValueError):
                exact_mcnemar_p_value(a, b)

    def test_reproducible_with_seed(self):
        pairs = read_pairs(FIXTURE)
        a = compare_pairs(pairs, resamples=500, seed=17)
        b = compare_pairs(pairs, resamples=500, seed=17)
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_original_order_does_not_change_point_estimate(self):
        pairs = read_pairs(FIXTURE)
        self.assertEqual(compare_pairs(pairs, resamples=100).delta_pp,
                         compare_pairs(list(reversed(pairs)), resamples=100).delta_pp)

    def test_reversed_model_direction(self):
        pairs = [Pair(p.case_id, p.model_b_success, p.model_a_success) for p in read_pairs(FIXTURE)]
        r = compare_pairs(pairs, resamples=1000)
        self.assertEqual(r.delta_pp, -20)
        self.assertEqual(r.direction, 'A')
        self.assertEqual(r.discordant_a_only, 25)

    def test_all_equal_degenerate(self):
        r = compare_pairs([Pair(str(i), True, True) for i in range(40)], resamples=100)
        self.assertEqual((r.delta_pp, r.ci_low_pp, r.ci_high_pp, r.p_value), (0, 0, 0, 1))
        self.assertIn('DEGENERATE_BOOTSTRAP', ' '.join(r.warnings))
        self.assertFalse(r.statistically_significant)

    def test_small_sample_not_claimed_as_winner(self):
        r = compare_pairs([Pair(str(i), False, True) for i in range(2)], resamples=100)
        self.assertFalse(r.statistically_significant)
        self.assertEqual(r.direction, 'inconclusive')
        self.assertIn('SMALL_SAMPLE', ' '.join(r.warnings))

    def test_few_discordant_pairs_warning(self):
        p = [Pair(str(i), True, True) for i in range(50)]
        p[0] = Pair('0', False, True)
        r = compare_pairs(p, resamples=100)
        self.assertIn('FEW_DISCORDANT_PAIRS', ' '.join(r.warnings))

    def test_validates_parameters(self):
        pairs = [Pair('a', True, False)]
        for kwargs in [{'confidence':1}, {'confidence':math.nan}, {'alpha':0}, {'alpha':True},
                       {'resamples':99}, {'resamples':True}, {'seed':-1}, {'seed':True},
                       {'model_a':'A', 'model_b':'A'}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                compare_pairs(pairs, **kwargs)

    def test_empty_and_duplicate_pairs(self):
        with self.assertRaises(ValueError):
            compare_pairs([])
        with self.assertRaisesRegex(ValueError, 'distinct'):
            compare_pairs([Pair('dup', True, False), Pair('dup', False, True)], resamples=100)

    def test_pair_boolean_input(self):
        with self.assertRaisesRegex(ValueError, 'boolean'):
            compare_pairs([Pair('a', 1, True)], resamples=100)

    def test_read_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'dataset.jsonl'; p.write_text('')
            with self.assertRaisesRegex(ValueError, 'at least one'):
                read_pairs(p)

    def test_read_duplicate_case_ids(self):
        obj={'case_id':'dup', 'model_a_success':True, 'model_b_success':False}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'dataset.jsonl'; p.write_text(json.dumps(obj)+'\n'+json.dumps(obj)+'\n')
            with self.assertRaisesRegex(ValueError, 'repeated'):
                read_pairs(p)

    def test_read_invalid_json(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'dataset.jsonl'; p.write_text('{invalid}\n')
            with self.assertRaisesRegex(ValueError, 'invalid JSON'):
                read_pairs(p)

    def test_read_non_boolean_input(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'dataset.jsonl'; p.write_text(json.dumps({'case_id':'x','model_a_success':1,'model_b_success':False})+'\n')
            with self.assertRaisesRegex(ValueError, 'booleans'):
                read_pairs(p)

    def test_read_extra_columns_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'dataset.jsonl'; p.write_text(json.dumps({'case_id':'x','model_a_success':True,'model_b_success':False,'pii':'secret'})+'\n')
            with self.assertRaisesRegex(ValueError, 'unexpected'):
                read_pairs(p)

    def test_read_blank_line_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'dataset.jsonl'; p.write_text('\n')
            with self.assertRaisesRegex(ValueError, 'empty JSONL'):
                read_pairs(p)

    def test_cli_saves_report_and_no_input_records(self):
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'stats.json'
            result=subprocess.run([sys.executable,'-m','evals_lab.significance_cli','--dataset',str(FIXTURE),
                                   '--resamples','300','--output',str(output)],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            saved=json.loads(output.read_text())
            self.assertEqual(saved['sample_size'],100)
            self.assertNotIn('case-', output.read_text())
            self.assertEqual(saved, json.loads(result.stdout))

    def test_cli_bad_input_exit_one(self):
        result=subprocess.run([sys.executable,'-m','evals_lab.significance_cli','--dataset',str(FIXTURE),
                               '--confidence','1.2'],text=True,capture_output=True)
        self.assertEqual(result.returncode,1)
        self.assertIn('INPUT_ERROR',result.stderr)


if __name__=='__main__':
    unittest.main()

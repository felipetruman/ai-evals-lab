"""Regression and adversarial checks for data generation/curation guardrails."""
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals_lab.synthetic import (
    canonical, digest, generate_candidates, promote_reviewed, read_candidates,
    validate_candidates, write_new_jsonl,
)
from evals_lab.synthetic_llm import generate_model_candidates

ROOT = Path(__file__).resolve().parents[1]


def sample(name):
    return json.loads((ROOT / 'examples' / name).read_text(encoding='utf-8'))


class TestSynthetic(unittest.TestCase):
    def setUp(self):
        self.policy = sample('policy.json')
        self.seed_trace = sample('valid.json')

    def candidates(self, count=24, seed=42):
        return generate_candidates(self.policy, self.seed_trace, count=count, seed=seed)

    def test_expected_count_and_schema(self):
        records = self.candidates()
        self.assertEqual(len(records), 24)
        validate_candidates(self.policy, records)
        self.assertTrue(all(r['review_status'] == 'pending' for r in records))
        self.assertTrue(all(r['schema'].endswith('/v1') for r in records))

    def test_seed_reproducibility(self):
        self.assertEqual(self.candidates(), self.candidates())

    def test_seed_changes_output(self):
        self.assertNotEqual(self.candidates(seed=42), self.candidates(seed=47))

    def test_unique_case_ids_and_hashes(self):
        cases = self.candidates(80)
        self.assertEqual(len({r['candidate_id'] for r in cases}), 80)
        self.assertEqual(len({r['trace_sha256'] for r in cases}), 80)

    def test_mix_of_positive_and_negative(self):
        cases = self.candidates(48)
        self.assertTrue(any(r['expected_pass'] for r in cases))
        self.assertTrue(any(not r['expected_pass'] for r in cases))

    def test_safety_dependency_mutations(self):
        cases = self.candidates(80)
        categories = {r['mutation'] for r in cases}
        self.assertIn('missing_required_step', categories)
        self.assertIn('dependency_order', categories)
        self.assertIn('unknown_api_parameter', categories)
        self.assertIn('valid_type_boundary', categories)

    def test_base_fixture_not_mutated(self):
        original = copy.deepcopy(self.seed_trace)
        self.candidates(40)
        self.assertEqual(self.seed_trace, original)

    def test_seed_trace_must_pass(self):
        with self.assertRaisesRegex(ValueError, 'seed trace'):
            generate_candidates(self.policy, sample('missing_safety.json'))

    def test_limits(self):
        for value in (0, -3, 501, True, 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.candidates(value)

    def test_negative_seed(self):
        with self.assertRaisesRegex(ValueError, 'seed'):
            self.candidates(seed=-1)

    def test_validate_rejects_tampering(self):
        rows = self.candidates(5)
        rows[0]['trace']['calls'][0]['tool'] = 'tool.tampered'
        with self.assertRaisesRegex(ValueError, 'trace hash mismatch'):
            validate_candidates(self.policy, rows)

    def test_validate_rejects_policy_mismatch(self):
        rows = self.candidates(3)
        self.policy['workflow_id'] = 'tampered'
        with self.assertRaisesRegex(ValueError, 'policy hash mismatch'):
            validate_candidates(self.policy, rows)

    def test_validate_rejects_duplicate(self):
        rows = self.candidates(4)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            validate_candidates(self.policy, rows + [copy.deepcopy(rows[0])])

    def test_validate_rejects_automatic_approval(self):
        rows = self.candidates(2)
        rows[0]['review_status'] = 'approved'
        with self.assertRaisesRegex(ValueError, 'pending'):
            validate_candidates(self.policy, rows)

    def test_default_never_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'dataset.jsonl'
            write_new_jsonl(path, self.candidates(2))
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_new_jsonl(path, self.candidates(2))
            self.assertEqual(before, path.read_bytes())

    def test_promote_explicit_review_only(self):
        rows = self.candidates(3)
        reviews = [
            {'candidate_id': rows[0]['candidate_id'], 'decision': 'approve',
             'reviewer': 'DemoReviewer', 'rationale': 'Tool policy violation is unambiguous'},
            {'candidate_id': rows[1]['candidate_id'], 'decision': 'reject',
             'reviewer': 'DemoReviewer', 'rationale': 'Semantic validity unclear'},
        ]
        curated = promote_reviewed(self.policy, rows, reviews)
        self.assertEqual(len(curated), 1)
        self.assertEqual(curated[0]['case_id'], rows[0]['candidate_id'])
        self.assertEqual(set(curated[0]), {'case_id', 'expected_pass', 'trace'})

    def test_unreviewed_never_promoted(self):
        with self.assertRaisesRegex(ValueError, 'no approved'):
            promote_reviewed(self.policy, self.candidates(2), [])

    def test_bad_review_rejected(self):
        rows = self.candidates(2)
        for review in (
            {'candidate_id': rows[0]['candidate_id'], 'decision': 'approve', 'reviewer': '', 'rationale': 'ok'},
            {'candidate_id': rows[0]['candidate_id'], 'decision': 'approve', 'reviewer': 'R', 'rationale': ''},
            {'candidate_id': rows[0]['candidate_id'], 'decision': 'maybe', 'reviewer': 'R', 'rationale': 'ok'},
            {'candidate_id': 'does-not-exist', 'decision': 'approve', 'reviewer': 'R', 'rationale': 'ok'},
        ):
            with self.subTest(review=review), self.assertRaises(ValueError):
                promote_reviewed(self.policy, rows, [review])

    def test_review_duplicates_rejected(self):
        rows = self.candidates(2)
        review = {'candidate_id': rows[0]['candidate_id'], 'decision': 'approve',
                  'reviewer': 'R', 'rationale': 'ok'}
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            promote_reviewed(self.policy, rows, [review, review])

    def test_read_write_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cases.jsonl'
            rows = self.candidates(6)
            write_new_jsonl(path, rows)
            self.assertEqual(read_candidates(path), rows)

    def test_invalid_jsonl_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cases.jsonl'
            path.write_text('{}\nNOPE\n')
            with self.assertRaisesRegex(ValueError, 'line 2'):
                read_candidates(path)

    def test_batch_evaluator_accepts_human_curated_output(self):
        from evals_lab.batch import evaluate_dataset
        rows = self.candidates(4)
        reviews = [{'candidate_id': r['candidate_id'], 'decision': 'approve',
                    'reviewer': 'R', 'rationale': 'Verified as synthetic example'} for r in rows]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'reviewed.jsonl'
            write_new_jsonl(path, promote_reviewed(self.policy, rows, reviews))
            report = evaluate_dataset(self.policy, path)
            self.assertEqual(report.cases_total, 4)
            self.assertEqual(report.mismatched, 0)

    def test_cli_generates_unreviewed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'candidates.jsonl'
            completed = subprocess.run([sys.executable, '-m', 'evals_lab.synthetic_cli', 'generate',
                '--policy', str(ROOT/'examples/policy.json'), '--seed-trace', str(ROOT/'examples/valid.json'),
                '--count','10','--output',str(target)], text=True, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(len(read_candidates(target)), 10)

    def test_cli_network_requires_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'candidates.jsonl'
            completed = subprocess.run([sys.executable, '-m', 'evals_lab.synthetic_cli', 'generate',
                '--policy', str(ROOT/'examples/policy.json'), '--seed-trace', str(ROOT/'examples/valid.json'),
                '--source','llm','--endpoint','https://example.com/v1/chat/completions',
                '--model','example','--output',str(target)], text=True, capture_output=True)
            self.assertEqual(completed.returncode, 1)
            self.assertIn('--allow-network', completed.stderr)
            self.assertFalse(target.exists())


class _Response:
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, amount):
        return self.body[:amount]


class TestOptionalLLM(unittest.TestCase):
    def setUp(self):
        self.policy = sample('policy.json')
        self.seed_trace = sample('valid.json')

    def mock_api(self, case):
        payload = json.dumps({'choices': [{'message': {'content': json.dumps({'cases': [case]})}}]}).encode()
        def opener(req, timeout):
            self.assertEqual(req.get_method(), 'POST')
            self.assertEqual(timeout, 30)
            return _Response(payload)
        return opener

    def test_llm_mocked_endpoint_returns_pending_candidate(self):
        trace = sample('missing_safety.json')
        cases = generate_model_candidates(self.policy, self.seed_trace,
            endpoint='http://127.0.0.1:8080/v1/chat/completions', model='local-test',
            count=1, opener=self.mock_api({'category':'safety','trace':trace}))
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['generator'], 'llm')
        self.assertFalse(cases[0]['expected_pass'])
        self.assertEqual(cases[0]['review_status'], 'pending')

    def test_plain_http_remote_rejected_before_transport(self):
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            generate_model_candidates(self.policy, self.seed_trace,
                endpoint='http://example.com/v1/chat/completions', model='test')

    def test_url_with_token_rejected(self):
        with self.assertRaisesRegex(ValueError, 'credentials'):
            generate_model_candidates(self.policy, self.seed_trace,
                endpoint='https://token:secret@example.com/v1/chat/completions', model='test')

    def test_invalid_api_response_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid JSON'):
            generate_model_candidates(self.policy, self.seed_trace,
                endpoint='https://example.com/v1/chat/completions', model='test',
                opener=lambda req, timeout: _Response(b'{"broken": 1}'))

    def test_generated_duplicates_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate or unchanged'):
            generate_model_candidates(self.policy, self.seed_trace,
                endpoint='https://example.com/v1/chat/completions', model='test',
                opener=self.mock_api({'category':'identical', 'trace':self.seed_trace}))


if __name__ == '__main__':
    unittest.main()

"""No-network tests for Project 13."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from evals_lab.context_eviction import (
    STRATEGIES, benchmark, evaluate_case, proxy_tokens, read_cases,
    record_cost, retrieve, select_records, validate_case,
)
FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "context-eviction.jsonl"

class ContextEvictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = read_cases(FIXTURE)
        cls.by_id = {c["case_id"]: c for c in cls.cases}

    def test_fixture_count(self):
        self.assertEqual(len(self.cases), 12)

    def test_three_strategies(self):
        self.assertEqual(STRATEGIES, ("recency", "priority", "structured_summary"))

    def test_proxy_token_count(self):
        self.assertEqual(proxy_tokens("hello world!"), 3)

    def test_no_overflow(self):
        for c in self.cases:
            for s in STRATEGIES:
                self.assertLessEqual(evaluate_case(c,s).selected_tokens, c["budget_tokens"])

    def test_paired_results(self):
        report = benchmark(self.cases)
        self.assertEqual(report.cases_total, 12)
        self.assertEqual(len(report.results), 36)
        self.assertEqual(len(report.summaries), 3)

    def test_all_items_fit(self):
        for s in STRATEGIES:
            self.assertTrue(evaluate_case(self.by_id["all_items_fit"], s).correct)

    def test_zero_budget_misses_fact(self):
        for s in STRATEGIES:
            r=evaluate_case(self.by_id["zero_budget_fact_missed"],s)
            self.assertEqual(r.error_kind,"MISSED_FACT")
            self.assertEqual(r.selected_ids,[])

    def test_unknown_key_correctly_abstains(self):
        for s in STRATEGIES:
            r=evaluate_case(self.by_id["unanswerable_with_zero_budget"],s)
            self.assertTrue(r.correct and r.abstained)

    def test_old_priority_beats_recency_in_one_case(self):
        c=self.by_id["early_high_priority_behind_noise"]
        self.assertFalse(evaluate_case(c,"recency").correct)
        self.assertTrue(evaluate_case(c,"priority").correct)

    def test_compaction_fits_long_fact(self):
        c=self.by_id["structured_compression_long_fact"]
        self.assertFalse(evaluate_case(c,"recency").correct)
        self.assertTrue(evaluate_case(c,"structured_summary").correct)

    def test_stale_value_detected(self):
        c=self.by_id["recent_update_corrects_old_fact"]
        self.assertEqual(evaluate_case(c,"priority").error_kind,"STALE_OR_WRONG_FACT")
        self.assertTrue(evaluate_case(c,"recency").correct)

    def test_newest_retained_value_wins(self):
        c=self.by_id["recent_update_corrects_old_fact"]
        selected,_=select_records(validate_case(c),c["budget_tokens"],"priority")
        self.assertEqual(retrieve(selected,"status"),"open")

    def test_summary_does_not_mutate_source(self):
        c=copy.deepcopy(self.cases[0]);before=json.dumps(c,sort_keys=True)
        evaluate_case(c,"structured_summary")
        self.assertEqual(before,json.dumps(c,sort_keys=True))

    def test_empty_benchmark_rejected(self):
        with self.assertRaises(ValueError): benchmark([])

    def test_duplicate_case_rejected(self):
        with self.assertRaisesRegex(ValueError,"duplicate case_id"):
            benchmark([self.cases[0],self.cases[0]])

    def test_duplicate_record_id_rejected(self):
        c=copy.deepcopy(self.cases[0]);c["records"][1]["id"]=c["records"][0]["id"]
        with self.assertRaisesRegex(ValueError,"unique"): validate_case(c)

    def test_missing_label_rejected(self):
        c=copy.deepcopy(self.cases[0]);del c["expected_value"]
        with self.assertRaisesRegex(ValueError,"expected_value"): validate_case(c)

    def test_invalid_budgets_rejected(self):
        for v in (-1,1.5,True,"22",100001):
            with self.subTest(v=v):
                c=copy.deepcopy(self.cases[0]);c["budget_tokens"]=v
                with self.assertRaisesRegex(ValueError,"budget_tokens"): validate_case(c)

    def test_invalid_priority_rejected(self):
        for v in (-1,11,True,2.2,"4"):
            with self.subTest(v=v):
                c=copy.deepcopy(self.cases[0]);c["records"][0]["priority"]=v
                with self.assertRaisesRegex(ValueError,"priority"): validate_case(c)

    def test_fact_text_must_include_value(self):
        c=copy.deepcopy(self.cases[0]);c["records"][0]["text"]="redacted"
        with self.assertRaisesRegex(ValueError,"occur in text"): validate_case(c)

    def test_noise_cannot_have_fact_key(self):
        c=copy.deepcopy(self.cases[0]);c["records"][1]["key"]="language"
        with self.assertRaisesRegex(ValueError,"noise must not"): validate_case(c)

    def test_invalid_strategy(self):
        with self.assertRaisesRegex(ValueError,"unsupported strategy"):
            evaluate_case(self.cases[0],"oracle")

    def test_negative_selector_budget(self):
        with self.assertRaisesRegex(ValueError,"nonnegative"):
            select_records(validate_case(self.cases[0]),-1,"recency")

    def test_report_omits_fact_values(self):
        c=copy.deepcopy(self.by_id["all_items_fit"])
        c["records"][1]["value"]="TOPSECRET_TOKEN_77"
        c["records"][1]["text"]="region TOPSECRET_TOKEN_77"
        c["expected_value"]="TOPSECRET_TOKEN_77"
        self.assertNotIn("TOPSECRET_TOKEN_77",json.dumps(benchmark([c]).to_dict()))

    def test_ground_truth_does_not_impact_selection(self):
        c=copy.deepcopy(self.by_id["all_items_fit"])
        a=evaluate_case(c,"recency")
        c["expected_value"]="wrong"
        b=evaluate_case(c,"recency")
        self.assertEqual(a.selected_ids,b.selected_ids)
        self.assertFalse(b.correct)

    def test_duplicate_jsonl_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"x.jsonl";p.write_text(json.dumps(self.cases[0])+"\n"+json.dumps(self.cases[0])+"\n")
            with self.assertRaisesRegex(ValueError,"duplicate case_id"): read_cases(p)

    def test_blank_jsonl_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"x.jsonl";p.write_text("\n")
            with self.assertRaisesRegex(ValueError,"empty JSONL"): read_cases(p)

    def test_cli_success(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"report.json"
            proc=subprocess.run([sys.executable,"-m","evals_lab.context_cli","--dataset",str(FIXTURE),"--output",str(out)],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertEqual(json.loads(out.read_text())["cases_total"],12)

    def test_cli_gate_fails(self):
        proc=subprocess.run([sys.executable,"-m","evals_lab.context_cli","--dataset",str(FIXTURE),"--min-accuracy","1.0"],capture_output=True,text=True)
        self.assertEqual(proc.returncode,2,proc.stderr)

    def test_cli_bad_input(self):
        proc=subprocess.run([sys.executable,"-m","evals_lab.context_cli","--dataset","missing.jsonl"],capture_output=True,text=True)
        self.assertEqual(proc.returncode,1,proc.stderr)

    def test_summary_stats_are_consistent(self):
        for s in benchmark(self.cases).summaries:
            self.assertEqual(s.total,12)
            self.assertEqual(s.correct+s.missed+s.stale_or_wrong,12)
            self.assertLessEqual(s.token_utilization,1)

    def test_compressed_cost_lower_than_original(self):
        r=validate_case(self.by_id["structured_compression_long_fact"])[0]
        self.assertLess(record_cost(r,"structured_summary"),record_cost(r,"priority"))

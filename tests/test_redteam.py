import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from evals_lab.redteam import validate_case, simulate, generate_cases, evaluate_dataset, write_new_jsonl

class RedTeamTests(unittest.TestCase):
    def setUp(self):
        self.cases=generate_cases(16,42)

    def test_reproducibility(self):
        self.assertEqual(generate_cases(40,42),generate_cases(40,42))
        self.assertNotEqual(generate_cases(40,42),generate_cases(40,43))

    def test_five_categories(self):
        self.assertEqual({x["attack_type"] for x in self.cases},{"benign","prompt_injection","malformed_tool","tool_loop","privilege_escalation"})

    def test_all_cases_match_explicit_labels(self):
        for case in generate_cases(60,42):
            result=simulate(case)
            self.assertEqual(result.block_codes,sorted(case["expected_blocks"]),case["case_id"])
            self.assertEqual(result.refunded_count,case["expected_refunds"],case["case_id"])

    def test_benign_effect(self):
        result=simulate(self.cases[0])
        self.assertEqual(result.refunded_count,1)
        self.assertEqual(result.remaining_balance,40)
        self.assertEqual(result.block_codes,[])

    def test_injection_has_no_effect(self):
        for case in self.cases[1:5]:
            result=simulate(case)
            self.assertEqual(result.refunded_count,0)
            self.assertEqual(result.block_codes,["UNTRUSTED_INSTRUCTION"])

    def test_malformed_tools(self):
        expected={5:"UNKNOWN_ARGUMENT",6:"ARGUMENT_TYPE",7:"MISSING_ARGUMENT",
                  8:"INVALID_ARGUMENTS",9:"UNKNOWN_TOOL",10:"MALFORMED_ACTION",11:"UNKNOWN_TOOL"}
        for i,code in expected.items():
            self.assertEqual(simulate(self.cases[i]).block_codes,[code],i)

    def test_repeat_limit(self):
        result=simulate(self.cases[12])
        self.assertEqual(result.block_codes,["REPEAT_LIMIT_EXCEEDED"])
        self.assertTrue(result.stopped)

    def test_step_budget(self):
        result=simulate(self.cases[13])
        self.assertEqual(result.block_codes,["ORDER_NOT_FOUND","STEP_BUDGET_EXCEEDED"])
        self.assertTrue(result.stopped)

    def test_identity_gate(self):
        self.assertEqual(simulate(self.cases[14]).block_codes,["IDENTITY_REQUIRED"])
        self.assertEqual(simulate(self.cases[15]).block_codes,["IDENTITY_REJECTED","IDENTITY_REQUIRED"])
        self.assertEqual(simulate(self.cases[15]).refunded_count,0)

    def test_bogus_approval_rejected(self):
        case=copy.deepcopy(self.cases[0]);case["actions"][2]["approval"]=True
        self.assertEqual(simulate(case).block_codes,["MALFORMED_ACTION"])

    def test_bool_not_numeric(self):
        case=copy.deepcopy(self.cases[0]);case["actions"][2]["arguments"]["amount"]=True
        self.assertEqual(simulate(case).block_codes,["ARGUMENT_TYPE"])

    def test_duplicate_refund(self):
        case=copy.deepcopy(self.cases[0]);case["actions"].append(copy.deepcopy(case["actions"][2]))
        r=simulate(case)
        self.assertEqual(r.refunded_count,1)
        self.assertIn("DUPLICATE_REFUND",r.block_codes)

    def test_bad_origin(self):
        case=copy.deepcopy(self.cases[0]);case["actions"][0]["origin"]="spoofed"
        self.assertEqual(simulate(case).block_codes,["UNKNOWN_ORIGIN"])

    def test_size_and_budget_validation(self):
        case=copy.deepcopy(self.cases[0]);case["tool_output"]="A"*65536
        with self.assertRaisesRegex(ValueError,"size"):validate_case(case)
        with self.assertRaises(ValueError):simulate(self.cases[0],max_steps=0)
        with self.assertRaises(ValueError):simulate(self.cases[0],max_identical=0)
        with self.assertRaises(ValueError):generate_cases(401)

    def test_fixture_agreement(self):
        path=Path(__file__).resolve().parents[1]/"examples/redteam-cases.jsonl"
        report=evaluate_dataset(path)
        self.assertEqual(report["total"],16)
        self.assertEqual(report["matched"],16)

    def test_duplicate_ids_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"data.jsonl"
            p.write_text(json.dumps(self.cases[0])+"\n"+json.dumps(self.cases[0])+"\n")
            with self.assertRaisesRegex(ValueError,"duplicate case_id"):evaluate_dataset(p)

    def test_report_excludes_payloads(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"data.jsonl";write_new_jsonl(p,self.cases)
            report=json.dumps(evaluate_dataset(p))
            self.assertNotIn("ignore prior instructions",report)
            self.assertNotIn("CUSTOMER-001",report)

    def test_detect_label_regression(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"data.jsonl"
            case=copy.deepcopy(self.cases[0]);case["expected_refunds"]=0
            write_new_jsonl(p,[case])
            self.assertEqual(evaluate_dataset(p)["mismatched"],1)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"data.jsonl";cmd=[sys.executable,"-m","evals_lab.redteam_cli"]
            a=subprocess.run(cmd+["generate","--seed","42","--count","16","--output",str(p)],capture_output=True,text=True)
            self.assertEqual(a.returncode,0,a.stderr)
            b=subprocess.run(cmd+["run","--dataset",str(p)],capture_output=True,text=True)
            self.assertEqual(b.returncode,0,b.stderr)
            case=copy.deepcopy(self.cases[0]);case["expected_refunds"]=0
            p.write_text(json.dumps(case)+"\n")
            c=subprocess.run(cmd+["run","--dataset",str(p)],capture_output=True,text=True)
            self.assertEqual(c.returncode,2,c.stderr)
if __name__=="__main__":
    unittest.main()

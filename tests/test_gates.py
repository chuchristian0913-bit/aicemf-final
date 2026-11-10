"""Gates are executable acceptance criteria: they must PASS on the real case-01
round and -- just as important -- FAIL when the defect they exist for is present.

A gate that cannot fail is decoration. The mutation tests below inject the exact
defect each gate guards against and assert the gate catches it.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pcd.gates import run_gates, g07_tie_out, g11_period_alignment, g01_schema, Ctx
from pcd.util import repo_root, read_json, write_json

ROOT = repo_root()
CASE = "case-01"
ROUND = ROOT / "cases" / CASE / "rounds" / "round-001"
# the full suite (which validates submission identity against the case's open
# request) runs against the ACCEPTED round
ACCEPTED = ROOT / "cases" / CASE / "rounds" / "round-002"


def _run(only=None):
    return run_gates(CASE, ACCEPTED, only=only)


class GateMutationMixin(unittest.TestCase):
    """Build a temp round dir holding a mutated claims file."""

    def _mutated_round(self, mutate):
        tmp = Path(tempfile.mkdtemp(prefix="pcd-gate-test-"))
        (tmp / "note").mkdir(parents=True)
        claims = read_json(ROUND / "note" / "claims.json")
        mutate(claims)
        write_json(tmp / "note" / "claims.json", claims)
        # gates read the submission envelope too; copy it so schema checks engage
        shutil.copy(ROUND / "submission.json", tmp / "submission.json")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        return tmp


class TestFullSuite(unittest.TestCase):
    """All 13 gates against the real frozen snapshot and the round-001 candidate."""

    def test_all_gates_pass(self):
        summary = _run()
        counts = summary["counts"]
        self.assertEqual(counts["FAIL"], 0, msg=json.dumps(summary["blocking_failures"]))
        self.assertEqual(counts["NOT_CHECKED"], 0)
        # G13 must not be silently skipped: a probe that never runs proves nothing
        self.assertEqual(summary["gate_results"]["G13"]["status"], "PASS")
        self.assertEqual(
            summary["gate_results"]["G13"]["evidence"]["interpretation"],
            "post == baseline (no leak) and pre != baseline (probe is live)",
        )


class TestG07TieOut(GateMutationMixin, unittest.TestCase):
    def test_unreferenced_prose_number_fails(self):
        # a percentage in a sentence that no claim produced is an unreferenced
        # number, even when it happens to be right
        def mutate(c):
            c["blocks"].append({"kind": "para", "text": "Sector colour: roughly 17.3% of the panel is European."})
        rd = self._mutated_round(mutate)
        res = g07_tie_out(Ctx(CASE, rd))
        self.assertEqual(res["status"], "FAIL")
        kinds = {f["kind"] for f in res["failures"]}
        self.assertIn("unreferenced_number", kinds)

    def test_signed_percent_is_one_token(self):
        # a signed percentage must be matched as one token; note that -0.4% itself
        # is a real display value in this note (GPN growth), so an unreferenced
        # mutation must use a number no claim produced
        def mutate(c):
            c["blocks"].append({"kind": "para", "text": "Global Payments fell -1.7% and something else moved +2.1% oddly."})
        rd = self._mutated_round(mutate)
        res = g07_tie_out(Ctx(CASE, rd))
        self.assertEqual(res["status"], "FAIL")
        toks = {f.get("token") for f in res["failures"] if f["kind"] == "unreferenced_number"}
        self.assertIn("-1.7%", toks)
        self.assertIn("+2.1%", toks)

    def test_every_claimed_number_ties_out(self):
        ctx = Ctx(CASE, ROUND)
        res = g07_tie_out(ctx)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["evidence"]["tied"], 50)


class TestG11PeriodAlignment(GateMutationMixin, unittest.TestCase):
    def test_missing_reference_fails(self):
        # the footnote exists but the comparative sentences never cite it
        def mutate(c):
            c["footnote_refs"] = []
        rd = self._mutated_round(mutate)
        res = g11_period_alignment(Ctx(CASE, rd))
        self.assertEqual(res["status"], "FAIL")
        kinds = {f["kind"] for f in res["failures"]}
        self.assertIn("footnote_not_referenced", kinds)

    def test_missing_footnote_fails(self):
        def mutate(c):
            c["limitations"] = []
            c["footnote_refs"] = []
        rd = self._mutated_round(mutate)
        res = g11_period_alignment(Ctx(CASE, rd))
        self.assertEqual(res["status"], "FAIL")
        kinds = {f["kind"] for f in res["failures"]}
        self.assertIn("missing_footnote", kinds)

    def test_real_panel_is_mixed_year_ends(self):
        # the panel genuinely mixes Sep (V) with Dec year-ends, so the gate is
        # exercised on every real run rather than vacuously passing
        res = g11_period_alignment(Ctx(CASE, ROUND))
        self.assertTrue(res["evidence"]["mixed"])
        self.assertEqual(res["status"], "PASS")


class TestG10ModelIntegrity(unittest.TestCase):
    def test_model_module_has_no_ticker_literals(self):
        res = run_gates(CASE, None, only=["G10"])["gate_results"]["G10"]
        self.assertEqual(res["status"], "PASS")
        self.assertTrue(res["evidence"]["model_module_free_of_ticker_literals"])

    def test_dcf_reproducible_from_assumptions(self):
        res = run_gates(CASE, None, only=["G10"])["gate_results"]["G10"]
        # every recomputed equity value matches to within 1e-9 relative
        for rec in res["evidence"]["recomputed"]:
            self.assertLess(rec["abs_delta"], max(1.0, abs(rec["recomputed"]) * 1e-9), msg=rec)


class TestG04Cutoff(unittest.TestCase):
    def test_cutoff_gate_is_exercised(self):
        # the freeze deliberately keeps post-cutoff rows so this gate has
        # something to reject; a filter that rejected nothing proves nothing
        res = run_gates(CASE, None, only=["G04"])["gate_results"]["G04"]
        self.assertEqual(res["status"], "PASS")
        self.assertGreater(res["evidence"]["post_cutoff_rows_rejected"], 0)


class TestG01Schema(GateMutationMixin, unittest.TestCase):
    def test_table_spec_mismatch_fails(self):
        # the publish-time defect: six declared columns under a five-column spec
        def mutate(c):
            for b in c["blocks"]:
                if b.get("kind") == "table" and b.get("spec") == "lrrrrr":
                    b["spec"] = "lrrrr"
                    return
        rd = self._mutated_round(mutate)
        res = g01_schema(Ctx(CASE, rd))
        self.assertEqual(res["status"], "FAIL")
        kinds = {f["kind"] for f in res["failures"]}
        self.assertIn("table_spec_mismatch", kinds)

    def test_dangling_footnote_ref_fails(self):
        def mutate(c):
            c["footnote_refs"] = ["period_alignment", "ghost_key"]
        rd = self._mutated_round(mutate)
        res = g01_schema(Ctx(CASE, rd))
        self.assertEqual(res["status"], "FAIL")
        kinds = {f["kind"] for f in res["failures"]}
        self.assertIn("footnote_ref_without_limitation", kinds)

    def test_extra_submission_key_fails(self):
        def mutate(c):
            pass
        rd = self._mutated_round(mutate)
        ctx = Ctx(CASE, rd)
        sub = read_json(rd / "note" / "claims.json")  # noqa: F841 (structure probe only)
        # write a submission with an extra key
        import json as _json

        real = _json.loads((ROUND / "submission.json").read_text(encoding="utf-8"))
        real["note"] = "prose does not belong in a schema"
        (rd / "submission.json").write_text(_json.dumps(real), encoding="utf-8")
        res = g01_schema(ctx)
        self.assertEqual(res["status"], "FAIL")
        kinds = {f["kind"] for f in res["failures"]}
        self.assertIn("extra_keys", kinds)


class TestG13Leakage(unittest.TestCase):
    def test_probe_is_live(self):
        res = run_gates(CASE, None, only=["G13"])["gate_results"]["G13"]
        self.assertEqual(res["status"], "PASS")
        ev = res["evidence"]
        # both halves distinct: post == baseline and pre != baseline
        self.assertEqual(ev["post_perturbation_signature"], ev["baseline_signature"])
        self.assertNotEqual(ev["pre_perturbation_signature"], ev["baseline_signature"])


if __name__ == "__main__":
    unittest.main()

"""Controller behaviour: checkpoints, streaks, stop rules, resume, approval.

State writes are redirected into a temp directory via monkeypatched case_paths,
so the tests never touch the real case-01 audit trail.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pcd.state as st
from pcd.state import State, ControllerError, route_after_review, record_approval, run_stage, advance, inspect


def _state(case_id="t-case", stage="REVIEWED", **kw):
    return State(case_id=case_id, stage=stage, **kw)


class TempCaseMixin(unittest.TestCase):
    """Redirect every state artifact into a temp dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pcd-state-test-"))
        self.paths = {
            "root": self.tmp,
            "case": self.tmp / "case",
            "work": self.tmp / "case" / "work",
            "state": self.tmp / "case" / "state.json",
            "trace": self.tmp / "case" / "trace.jsonl",
            "request": self.tmp / "case" / "request.json",
            "decision": self.tmp / "case" / "decision.json",
            "approval": self.tmp / "case" / "approval.json",
            "output": self.tmp / "output",
        }
        patcher = mock.patch.object(st, "case_paths", lambda cid: self.paths)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))


class TestReviewRouting(TempCaseMixin):
    def test_two_clean_rounds_accept(self):
        s = _state()
        h = "abc123"
        r1 = route_after_review(s, "clean", h)
        self.assertEqual(r1["decision"], "repeat")
        r2 = route_after_review(s, "clean", h)
        self.assertEqual(r2["decision"], "accept")
        self.assertIn("2 consecutive clean", r2["why"])

    def test_changed_candidate_resets_streak(self):
        # a repair cannot inherit the previous candidate's cleanliness
        s = _state()
        route_after_review(s, "clean", "hash-a")
        r = route_after_review(s, "clean", "hash-b")
        self.assertEqual(r["decision"], "repeat")
        self.assertEqual(s.clean_rounds, 1)
        self.assertEqual(s.clean_round_streak_for, "hash-b")

    def test_findings_reset_streak(self):
        s = _state()
        route_after_review(s, "clean", "hash-a")
        s.open_findings = [{"kind": "x"}]
        r = route_after_review(s, "findings", "hash-a")
        self.assertEqual(r["decision"], "repair")
        self.assertEqual(s.clean_rounds, 0)
        self.assertIsNone(s.clean_round_streak_for)

    def test_exhaustion_never_accepts(self):
        # round budget exhausted -> exhaust, even if a clean round arrives
        s = _state(round=3)
        r = route_after_review(s, "findings", "hash-a")
        self.assertEqual(r["decision"], "exhaust")

    def test_clean_after_repair_needs_two_again(self):
        s = _state()
        route_after_review(s, "clean", "h")
        route_after_review(s, "findings", "h")   # streak reset
        r1 = route_after_review(s, "clean", "h")
        self.assertEqual(r1["decision"], "repeat")


class TestApproval(TempCaseMixin):
    def test_named_human_required(self):
        with self.assertRaises(ControllerError):
            record_approval("t-case", "", "approve", "case-01 note")
        with self.assertRaises(ControllerError):
            record_approval("t-case", "   ", "approve", "case-01 note")

    def test_decision_vocabulary(self):
        with self.assertRaises(ControllerError):
            record_approval("t-case", "Christian", "ship it", "case-01 note")

    def test_valid_approval_recorded(self):
        rec = record_approval("t-case", "Christian", "approve", "case-01 note", "ok")
        self.assertEqual(rec["authority"], "human-analyst")
        self.assertTrue(self.paths["approval"].exists())


class TestTransitions(TempCaseMixin):
    def test_illegal_transition_rejected(self):
        s = _state(stage="REVIEWED")
        with self.assertRaises(ControllerError):
            advance(s, "PUBLISHED")

    def test_legal_transition(self):
        s = _state(stage="CANDIDATE_ACCEPTED")
        advance(s, "AWAITING_APPROVAL")
        self.assertEqual(s.stage, "AWAITING_APPROVAL")
        self.assertTrue(self.paths["state"].exists())

    def test_terminal_state_records_reason(self):
        s = _state(stage="MODELED")
        advance(s, "BLOCKED", reason="gate G13 failed twice")
        self.assertEqual(s.terminal_reason, "gate G13 failed twice")


class TestRunStage(TempCaseMixin):
    def _mk(self, inp: Path, out: Path):
        def fn():
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("computed from " + inp.read_text(), encoding="utf-8")
        return fn

    def test_rerun_with_same_inputs_is_skipped(self):
        # completed work is not repeated: this is what makes resume cheap
        inp = self.tmp / "in.txt"
        out = self.tmp / "out.txt"
        inp.write_text("frozen inputs", encoding="utf-8")
        s = _state()
        self.assertEqual(run_stage(s, "model", [inp], [out], self._mk(inp, out)), "ran")
        self.assertEqual(run_stage(s, "model", [inp], [out], self._mk(inp, out)), "skipped")

    def test_missing_output_reruns(self):
        inp = self.tmp / "in.txt"
        out = self.tmp / "out.txt"
        inp.write_text("x", encoding="utf-8")
        s = _state()
        run_stage(s, "model", [inp], [out], self._mk(inp, out))
        out.unlink()
        calls = []
        def fn2():
            calls.append(1)
            self._mk(inp, out)()
        self.assertEqual(run_stage(s, "model", [inp], [out], fn2), "ran")
        self.assertEqual(len(calls), 1)

    def test_changed_inputs_invalidate(self):
        inp = self.tmp / "in.txt"
        out = self.tmp / "out.txt"
        inp.write_text("v1", encoding="utf-8")
        s = _state()
        run_stage(s, "model", [inp], [out], self._mk(inp, out))
        inp.write_text("v2", encoding="utf-8")
        # inputs changed under a completed stage -> new version, not a retry
        self.assertEqual(run_stage(s, "model", [inp], [out], self._mk(inp, out)), "ran")

    def test_stage_must_produce_outputs(self):
        inp = self.tmp / "in.txt"
        inp.write_text("x", encoding="utf-8")
        out = self.tmp / "never.txt"
        s = _state()
        with self.assertRaises(ControllerError):
            run_stage(s, "model", [inp], [out], lambda: None)


class TestInspect(TempCaseMixin):
    def test_completed_case_returns_saved_result(self):
        # a completed case returns its saved result; nothing is recomputed
        s = _state(stage="PUBLISHED")
        self.paths["case"].mkdir(parents=True)
        self.paths["decision"].write_text('{"decision":"publish"}', encoding="utf-8")
        rep = inspect(s)
        self.assertFalse(rep["resumable"])
        self.assertIn("decision.json", rep["result"])

    def test_output_missing_flagged(self):
        s = _state(stage="MODELED")
        s.stage_hashes = {"model": "deadbeef"}
        rep = inspect(s)
        kinds = [p["kind"] for p in rep["problems"]]
        self.assertIn("output_missing", kinds)

    def test_orphan_output_flagged(self):
        s = _state(stage="MODELED")
        self.paths["work"].mkdir(parents=True)
        (self.paths["work"] / "metrics.json").write_text("{}", encoding="utf-8")
        rep = inspect(s)
        kinds = [p["kind"] for p in rep["problems"]]
        self.assertIn("orphan_output", kinds)


if __name__ == "__main__":
    unittest.main()

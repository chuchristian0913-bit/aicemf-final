"""Evolution cycle: QT guard, fixed-case evaluation, registry, rollback."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pcd.evolution as ev
from pcd.util import read_json, write_json, repo_root

CASES = repo_root() / "tests" / "cases" / "g01"
DEV = sorted(CASES.glob("dev-*.json"))
HELDOUT = sorted(CASES.glob("heldout-*.json"))


class TestQTGuard(unittest.TestCase):
    def _proposal(self, touches=None, risk=None):
        return {"id": "prop-test", "touches": touches or ["src/pcd/gates.py:G01"],
                "risk_changes": risk or []}

    def test_scoped_patch_passes(self):
        self.assertEqual(ev.qt_check(self._proposal())["qt"], "pass")

    def test_risk_limit_touch_rejected(self):
        with self.assertRaises(ev.ProposalRejected):
            ev.qt_check(self._proposal(["config/run.toml:max_rounds", "src/pcd/gates.py"]))

    def test_tolerance_touch_rejected(self):
        with self.assertRaises(ev.ProposalRejected):
            ev.qt_check(self._proposal(["gates.toml tolerance_rel loosening"]))

    def test_probe_touch_rejected(self):
        # weakening the leakage probe would make G13 vacuously pass
        with self.assertRaises(ev.ProposalRejected):
            ev.qt_check(self._proposal(["G13 probe_value"]))

    def test_declared_risk_changes_rejected(self):
        with self.assertRaises(ev.ProposalRejected):
            ev.qt_check(self._proposal(risk=["max_retries 2 -> 5"]))


class TestEvaluation(unittest.TestCase):
    def test_v1_misses_the_development_defect(self):
        # the whole reason this cycle exists: envelope-only G01 could not see
        # the table-spec defect that failed at publish time
        res = ev.run_cases(ev._gate_kinds_v1, DEV)
        self.assertEqual(res["passed"], 0)

    def test_v1_1_catches_all_development_cases(self):
        res = ev.run_cases(ev._gate_kinds_v1_1, DEV)
        self.assertEqual(res["passed"], res["total"])

    def test_heldout_not_used_for_revision(self):
        # held-out cases exist only for assessment; the module exposes no path
        # that feeds them back into a patch, and evaluation records that
        evl = ev.evaluate({"id": "prop-test", "touches": ["src/pcd/gates.py:G01"], "risk_changes": []}, DEV, HELDOUT)
        self.assertIn("did not inform the patch", evl["note"])
        self.assertEqual(evl["verdict"], "adopt")


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pcd-evo-"))
        patcher = mock.patch.object(ev, "VERSIONS_DIR", self.tmp)
        patcher.start()
        patcher2 = mock.patch.object(ev, "REGISTRY_PATH", self.tmp / "registry.json")
        patcher2.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(patcher2.stop)
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.prop = {"id": "prop-g01-2", "touches": ["src/pcd/gates.py:G01"], "risk_changes": []}
        self.evl = ev.evaluate(self.prop, DEV, HELDOUT)

    def test_register_then_rollback(self):
        ev.record_version("G01", "v1.1", self.prop, self.evl, "Christian")
        reg = ev.registry()
        self.assertEqual(reg["components"]["G01"]["active"], "v1.1")
        self.assertEqual(reg["components"]["G01"]["versions"][0]["status"], "active")
        ev.rollback("G01", "v1.1", "drill", "Christian")  # same version: still recorded
        ev.record_version("G01", "v1.2", self.prop, self.evl, "Christian")
        reg = ev.registry()
        self.assertEqual(reg["components"]["G01"]["active"], "v1.2")
        self.assertEqual(reg["components"]["G01"]["versions"][0]["status"], "superseded")
        ev.rollback("G01", "v1.1", "suspect regression in note parsing", "Christian")
        reg = ev.registry()
        self.assertEqual(reg["components"]["G01"]["active"], "v1.1")
        self.assertEqual(reg["components"]["G01"]["history"][-1]["event"], "rollback")

    def test_no_anonymous_owner(self):
        with self.assertRaises(ev.ProposalRejected):
            ev.record_version("G01", "v1.1", self.prop, self.evl, "  ")

    def test_rejected_evaluation_cannot_register(self):
        bad = dict(self.evl)
        bad["verdict"] = "reject"
        with self.assertRaises(ev.ProposalRejected):
            ev.record_version("G01", "v1.1", self.prop, bad, "Christian")

    def test_rollback_only_to_registered_versions(self):
        ev.record_version("G01", "v1.1", self.prop, self.evl, "Christian")
        with self.assertRaises(ev.ProposalRejected):
            ev.rollback("G01", "v0.9", "no such version", "Christian")


if __name__ == "__main__":
    unittest.main()

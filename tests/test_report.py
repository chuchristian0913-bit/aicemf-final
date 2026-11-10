"""The renderer's contract: markers survive escaping, dangling markers fail loudly."""

import unittest

from pcd.report import esc_marked, html_marked, fill, fmt, resolve_claims
from pcd.report import _segments


class TestFootnoteMarkers(unittest.TestCase):
    KNOWN = {"period_alignment", "tag_drift"}

    def test_marker_survives_latex_escaping(self):
        # The regression this test pins: escaping first turned [[key]] into
        # {[}{[}key{]}{]} and the published PDF carried a literal marker.
        out = esc_marked("The year-ends do not align. [[period_alignment]]", self.KNOWN)
        self.assertIn(r"\textsuperscript{\dag}", out)
        self.assertNotIn("[[", out)
        self.assertNotIn("{[}", out)

    def test_percent_escaping_still_applies(self):
        out = esc_marked("margin was 57.6% and EBITDA proxy [[tag_drift]]", self.KNOWN)
        self.assertIn("57.6\\%", out)
        self.assertIn(r"\textsuperscript{\dag}", out)

    def test_marker_adjacent_to_special_chars(self):
        out = esc_marked("up & down [[period_alignment]] 50% of A_B", self.KNOWN)
        self.assertIn(r"up \& down \textsuperscript{\dag} 50\% of A\_B", out)

    def test_dangling_marker_raises(self):
        with self.assertRaises(KeyError):
            esc_marked("cites [[nonexistent_key]]", self.KNOWN)

    def test_html_marker(self):
        out = html_marked("does not align. [[period_alignment]]", self.KNOWN)
        self.assertIn("<sup>&dagger;</sup>", out)
        self.assertNotIn("[[", out)

    def test_html_escaping_applies(self):
        out = html_marked("a < b & c [[period_alignment]]", self.KNOWN)
        self.assertIn("a &lt; b &amp; c", out)

    def test_segments_order(self):
        runs = _segments("x [[a]] y", {"a"})
        self.assertEqual(runs, [("text", "x "), ("marker", "a"), ("text", " y")])


class TestFill(unittest.TestCase):
    def test_placeholder_resolves_to_display(self):
        resolved = {"c1": {"display": "40.0"}}
        self.assertEqual(fill("revenue was {c1} bn", resolved), "revenue was 40.0 bn")

    def test_unknown_placeholder_raises(self):
        # "a number in the note must be traceable"
        with self.assertRaises(KeyError):
            fill("growth of {ghost}", {})


class TestResolveClaims(unittest.TestCase):
    def _claim(self, cid, mid, quoted=None, fmtk="pct1"):
        return {"claim_id": cid, "metric_id": mid, "quoted_value": quoted, "fmt": fmtk}

    REG = {"m1": {"value": 0.113, "sources": ["s1"]}}

    def test_drift_detected(self):
        # delta must exceed max(abs_tol=0.5, rel): 2.0 vs 0.113 is far outside both
        resolved, problems = resolve_claims([self._claim("c1", "m1", quoted=2.0)], self.REG)
        self.assertEqual([p["kind"] for p in problems], ["quoted_value_drift"])

    def test_within_tolerance_passes(self):
        _, problems = resolve_claims([self._claim("c1", "m1", quoted=0.1132)], self.REG)
        self.assertEqual(problems, [])

    def test_unknown_metric(self):
        resolved, problems = resolve_claims([self._claim("c1", "nope")], self.REG)
        self.assertEqual([p["kind"] for p in problems], ["unknown_metric"])

    def test_none_value_renders_na(self):
        reg = {"m2": {"value": None, "sources": []}}
        resolved, _ = resolve_claims([self._claim("c2", "m2")], reg)
        self.assertEqual(resolved["c2"]["display"], "n/a")


class TestFmt(unittest.TestCase):
    def test_none_is_na(self):
        self.assertEqual(fmt(None, "usd_b"), "n/a")

    def test_usd_b(self):
        self.assertEqual(fmt(40.0e9, "usd_b"), "40.0")

    def test_pct1(self):
        self.assertEqual(fmt(0.164, "pct1"), "16.4%")

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            fmt(1.0, "lightyears")


if __name__ == "__main__":
    unittest.main()

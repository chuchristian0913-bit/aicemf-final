"""Deterministic helpers must be deterministic, and division must refuse to invent."""

import unittest

from pcd.util import canon, safe_div, sha256_obj, write_json, read_json


class TestSafeDiv(unittest.TestCase):
    def test_normal(self):
        self.assertAlmostEqual(safe_div(10, 4), 2.5)

    def test_zero_denominator_returns_none(self):
        # A zero denominator returns None, never 0 and never an exception the
        # caller is tempted to swallow into a default. "Zero is a number;
        # n/a is an admission."
        self.assertIsNone(safe_div(10, 0))

    def test_none_inputs(self):
        self.assertIsNone(safe_div(None, 5))
        self.assertIsNone(safe_div(5, None))
        self.assertIsNone(safe_div(None, None))

    def test_zero_numerator_is_a_real_zero(self):
        # 0 / 5 == 0 is a legitimate measurement and must NOT be suppressed.
        self.assertEqual(safe_div(0, 5), 0.0)


class TestCanon(unittest.TestCase):
    def test_key_order_irrelevant(self):
        a = canon({"b": 1, "a": {"d": 2, "c": 3}})
        b = canon({"a": {"c": 3, "d": 2}, "b": 1})
        self.assertEqual(a, b)

    def test_stable_hash(self):
        h1 = sha256_obj({"x": [1, 2, 3], "y": "z"})
        h2 = sha256_obj({"y": "z", "x": [1, 2, 3]})
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_nested_floats_survive(self):
        h = sha256_obj({"v": 0.1 + 0.2})
        self.assertEqual(len(h), 64)


class TestAtomicWrite(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            write_json(p, {"a": 1, "b": [1, 2]})
            self.assertEqual(read_json(p), {"a": 1, "b": [1, 2]})

    def test_no_tmp_leftover(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "y.json"
            write_json(p, {"ok": True})
            leftovers = [q.name for q in Path(td).iterdir() if q.name != "y.json"]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()

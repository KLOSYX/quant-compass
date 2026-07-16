import importlib.util
import json
from pathlib import Path
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "analyze_candidates", ROOT / "scripts" / "analyze_candidates.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class AnalyzeCandidatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = ROOT / "evals" / "fixtures"
        cls.profile = json.loads(
            (fixtures / "profile.json").read_text(encoding="utf-8")
        )
        cls.products = pd.read_csv(fixtures / "products.csv", dtype={"code": str})
        cls.nav = pd.read_csv(fixtures / "nav.csv", dtype={"code": str})

    def test_filters_short_history_and_closed_product(self):
        result = MODULE.analyze(self.profile, self.products, self.nav)
        by_code = {item["code"]: item for item in result["candidates"]}
        self.assertTrue(by_code["000001"]["eligible"])
        self.assertIn("history_too_short", by_code["000002"]["hard_filter_reasons"])
        self.assertIn("not_currently_open", by_code["000003"]["hard_filter_reasons"])
        self.assertEqual(result["coverage"]["eligible"], 1)

    def test_channel_claim_is_not_promoted(self):
        result = MODULE.analyze(self.profile, self.products, self.nav)
        by_code = {item["code"]: item for item in result["candidates"]}
        self.assertEqual(by_code["000001"]["channel_status"], "public_open")
        self.assertEqual(
            result["channel_note"], "public_open does not mean ant_verified"
        )

    def test_uses_cumulative_nav_for_return_and_drawdown(self):
        result = MODULE.analyze(self.profile, self.products, self.nav)
        item = next(row for row in result["candidates"] if row["code"] == "000001")
        self.assertAlmostEqual(item["total_return"], 0.087, places=6)
        self.assertEqual(item["max_drawdown"], 0.0)
        self.assertGreater(item["annual_return"], 0.03)


if __name__ == "__main__":
    unittest.main()

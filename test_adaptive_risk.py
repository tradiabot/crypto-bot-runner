import os
import unittest
from unittest.mock import patch

import adaptive_risk


def snapshot(value, epoch):
    return {"event": "portfolio_snapshot", "epoch": epoch, "data": {"total_usd": value}}


class AdaptiveRiskUsdHaltTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "ADAPTIVE_RISK_ENABLED": "YES",
            "ADAPTIVE_BASELINE_MODE": "RECENT_HIGH",
            "ADAPTIVE_HALT_LOSS_USD": "5",
            "ADAPTIVE_RESUME_LOSS_USD": "0.25",
            "ADAPTIVE_BASELINE_USD": "0",
        }, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    @patch.object(adaptive_risk, "_save_state")
    @patch.object(adaptive_risk, "_load_state", return_value={})
    def test_halts_at_exactly_five_dollars(self, _load, _save):
        result = adaptive_risk.status([snapshot(60, 1), snapshot(55, 2)])
        self.assertTrue(result["hard_loss_halt"])
        self.assertTrue(result["halted"])
        self.assertEqual(result["max_trade_usdc"], 0)
        self.assertFalse(result["allow_risk_reducing_sells"])

    @patch.object(adaptive_risk, "_save_state")
    @patch.object(adaptive_risk, "_load_state", return_value={"baseline_equity": 60, "hard_loss_halt": True})
    def test_halt_latches_until_loss_is_recovered(self, _load, _save):
        held = adaptive_risk.status([snapshot(60, 1), snapshot(58, 3)])
        self.assertTrue(held["hard_loss_halt"])
        recovered = adaptive_risk.status([snapshot(60, 1), snapshot(59.80, 3)])
        self.assertFalse(recovered["hard_loss_halt"])


if __name__ == "__main__":
    unittest.main()

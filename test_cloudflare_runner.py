import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import cloudflare_runner


class CloudflareRunnerLogTests(unittest.TestCase):
    def test_seeded_d1_history_is_not_reuploaded(self):
        now = datetime.now(timezone.utc)
        old = datetime.fromtimestamp(now.timestamp() - 60, timezone.utc)
        rows = [
            {"ts": old.strftime("%Y-%m-%d %H:%M:%S"), "event": "trade_executed", "data": {"result": {"id": 1}}},
            {"ts": now.isoformat(), "event": "decision_hold", "data": {}},
            {"ts": "invalid", "event": "trade_executed", "data": {"result": {"id": 2}}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "crypto-bot.log"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            with patch.object(cloudflare_runner, "LOG_PATH", path):
                events = cloudflare_runner.read_log_events(time.time() - 2)
        self.assertEqual([event["event"] for event in events], ["decision_hold"])

    def test_seed_feedback_deduplicates_trade_ids(self):
        remote_rows = {
            "logs": [
                {"ts": "2026-09-12T23:00:00Z", "event": "trade_executed", "data": {"result": {"id": 7}}},
                {"ts": "2026-09-12T22:00:00Z", "event": "trade_executed", "data": {"result": {"id": 7}}},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "crypto-bot.log"
            with patch.object(cloudflare_runner, "LOG_PATH", path), patch.object(cloudflare_runner, "request_json", return_value=remote_rows):
                cloudflare_runner.seed_local_feedback()
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()

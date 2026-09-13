import json
import os
import unittest
from unittest.mock import patch

import crypto_scan


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        content = json.dumps({"opportunities": [{"symbol": "BTC", "action": "HOLD", "confidence": 0, "reason": "mixed"}]})
        return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


class GroqRequestTests(unittest.TestCase):
    def test_gpt_oss_uses_strict_schema(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data)
            captured["timeout"] = timeout
            return _Response()

        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only", "GROQ_REASONING_EFFORT": "low"}, clear=False), patch(
            "crypto_scan.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            result = crypto_scan.ask_groq_model("openai/gpt-oss-120b", "Market=BTC")

        response_format = captured["payload"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(captured["payload"]["reasoning_effort"], "low")
        self.assertEqual(result["opportunities"][0]["symbol"], "BTC")


if __name__ == "__main__":
    unittest.main()

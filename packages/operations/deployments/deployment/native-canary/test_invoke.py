import json
import unittest

from invoke import send_params, validate


class PublicCanaryInvokeTests(unittest.TestCase):
    def test_only_canary_agents_on_an_existing_loopback_forward_are_admitted(self):
        validate("http://127.0.0.1:18093", "public-native-canary-a")
        for origin, agent in [("http://other.example:80", "public-native-canary-a"),
                              ("http://127.0.0.1:18093", "factory-coder"),
                              ("http://127.0.0.1:18093/private", "public-native-canary-b")]:
            with self.subTest(origin=origin, agent=agent), self.assertRaises(ValueError):
                validate(origin, agent)

    def test_native_message_contains_only_fixed_public_code_and_explicit_no_edits(self):
        session = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        message = "11111111-2222-4333-8444-555555555555"
        parameters = send_params(session, message, "seed")
        self.assertEqual(parameters["message"]["contextId"], session)
        self.assertEqual(json.loads(parameters["message"]["parts"][0]["text"]), {"prompt": "seed", "allowEdits": False})
        with self.assertRaises(ValueError):
            send_params(session, message, "model")
        self.assertEqual(json.loads(send_params(session, message, "model", True)["message"]["parts"][0]["text"])["prompt"], "model")
        with self.assertRaises(ValueError):
            send_params(session, message, "private arbitrary text", True)


if __name__ == "__main__":
    unittest.main()

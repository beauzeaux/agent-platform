import json
import unittest
from unittest.mock import patch
from common import PINS
from verify import FACTS, validate_embeddings, verify_native_client_request


class EmbeddingContractTests(unittest.TestCase):
    def response(self):
        return {"object": "list", "model": PINS["model"], "usage": {"prompt_tokens": 4},
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1] * 768}]}

    def test_accepts_native_embedding_contract(self):
        self.assertEqual(len(validate_embeddings(self.response(), 1)[0]), 768)

    def test_native_client_probe_sends_exact_body_and_inert_header_without_encoding_format(self):
        deployment = {"status": {"readyReplicas": 1},
                      "spec": {"template": {"spec": {"containers": [{"image": PINS["teiImage"]}]}}}}
        output = json.dumps(self.response()).encode() + b"\n200"
        with patch("verify.prepare_state"), patch("verify.require_owned", return_value=deployment), \
                patch("verify.STATE"), patch("verify.run", return_value=output) as request:
            verify_native_client_request()
        command = request.call_args.args[0]
        self.assertIn("Authorization: Bearer kagent-credential-injected", command)
        self.assertEqual(json.loads(request.call_args.kwargs["data"]),
                         {"model": PINS["model"], "input": FACTS[0], "dimensions": 768})

    def test_rejects_bad_dimensions_nonfinite_values_and_reordered_results(self):
        for bad in ["dimension", "nan", "bool", "zero", "index"]:
            with self.subTest(bad=bad):
                response = self.response()
                row = response["data"][0]
                if bad == "dimension":
                    row["embedding"].pop()
                elif bad == "nan":
                    row["embedding"][7] = float("nan")
                elif bad == "bool":
                    row["embedding"][7] = True
                elif bad == "zero":
                    row["embedding"] = [0.0] * 768
                else:
                    row["index"] = 1
                with self.assertRaises(ValueError):
                    validate_embeddings(response, 1)

    def test_rejects_missing_api_shape_and_count(self):
        for key in ["data", "model", "object", "usage"]:
            response = self.response()
            del response[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_embeddings(response, 1)


if __name__ == "__main__":
    unittest.main()

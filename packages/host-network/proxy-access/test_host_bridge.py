import http.client
import http.server
import gzip
import io
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

from host_bridge import ModelHandler
import egress_overlay
from build_image import copy_layer


class Origin(http.server.BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        Origin.requests.append({
            "authorization": self.headers.get("Authorization"),
            "cookie": self.headers.get("Cookie"),
            "body": self.rfile.read(int(self.headers["Content-Length"])),
        })
        body = b"data: first\n\ndata: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        Origin.requests = []
        self.origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        self.bridge = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        self.bridge.settings = {"llm_host": "127.0.0.1", "llm_port": self.origin.server_port}
        self.bridge.gateway_key = "host-only-fixture"
        for server in (self.origin, self.bridge):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.connection = http.client.HTTPConnection("127.0.0.1", self.bridge.server_port, timeout=5)

    def tearDown(self):
        self.connection.close()
        for server in (self.bridge, self.origin):
            server.shutdown()
            server.server_close()

    def test_stream_preserved_and_host_authorization_replaces_client(self):
        self.connection.request("POST", "/v1/responses", b'{"model":"fixture"}', {
            "Authorization": "Bearer never-forward",
            "Cookie": "never-forward",
            "Content-Type": "application/json",
        })
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), b"data: first\n\ndata: [DONE]\n\n")
        self.assertEqual(Origin.requests, [{
            "authorization": "Bearer host-only-fixture", "cookie": None,
            "body": b'{"model":"fixture"}',
        }])

    def test_unknown_route_never_contacts_origin(self):
        self.connection.request("POST", "/metadata/identity/oauth2/token", b"")
        self.assertEqual(self.connection.getresponse().status, 403)
        self.assertEqual(Origin.requests, [])

    def test_chunked_model_upload_preserves_body_and_stream(self):
        self.connection.request("POST", "/v1/responses",
                                iter([b'{"model":', b'"fixture"}']),
                                {"Content-Type": "application/json"}, encode_chunked=True)
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), b"data: first\n\ndata: [DONE]\n\n")
        self.assertEqual(Origin.requests[0]["body"], b'{"model":"fixture"}')
        self.assertEqual(Origin.requests[0]["authorization"],
                         "Bearer " + self.bridge.gateway_key)

    def test_invalid_and_oversized_chunks_never_contact_origin(self):
        self.bridge.settings["max_request_bytes"] = 16
        for body, expected in [
            (b"invalid\r\n", 400),
            (b"2\r\nabXX", 400),
            (b"0\r\nInjected: value\r\n\r\n", 400),
            (b"11\r\n", 413),
        ]:
            with self.subTest(body=body):
                self.connection.putrequest("POST", "/v1/responses")
                self.connection.putheader("Transfer-Encoding", "chunked")
                self.connection.endheaders()
                self.connection.send(body)
                self.assertEqual(self.connection.getresponse().status, expected)
                self.connection.close()
        self.assertEqual(Origin.requests, [])

    def test_ambiguous_framing_and_oversize_rejected_before_origin(self):
        for headers, expected in [
            ({"Transfer-Encoding": "chunked", "Content-Length": "0"}, 400),
            ({"Transfer-Encoding": "gzip, chunked"}, 400),
            ({"Content-Length": "30000001"}, 413),
        ]:
            with self.subTest(headers=headers):
                self.connection.request("POST", "/v1/responses", headers=headers)
                self.assertEqual(self.connection.getresponse().status, expected)
                self.connection.close()
        self.assertEqual(Origin.requests, [])


class OverlayTests(unittest.TestCase):
    def test_root_overlay_preserves_unrelated_text_and_aliases(self):
        original = "policy: &tls\n  root: " + egress_overlay.OLD_ROOT + "\nother: *tls\n"
        resource = {"data": {"config.yaml": original}}
        with patch.object(egress_overlay, "get", return_value=resource), \
                patch.object(egress_overlay, "run"), \
                patch.object(egress_overlay, "STATE") as state:
            state.__truediv__.return_value.exists.return_value = True
            egress_overlay.replace_config(egress_overlay.OLD_ROOT, egress_overlay.NEW_ROOT)
        self.assertEqual(resource["data"]["config.yaml"],
                         original.replace(egress_overlay.OLD_ROOT, egress_overlay.NEW_ROOT))

    def test_unexpected_shared_root_fails_without_writing(self):
        with patch.object(egress_overlay, "get", return_value={"data": {"config.yaml": "changed"}}), \
                patch.object(egress_overlay, "run") as run:
            with self.assertRaises(RuntimeError):
                egress_overlay.replace_config(egress_overlay.NEW_ROOT, egress_overlay.OLD_ROOT)
            run.assert_not_called()


class ImageLayerTests(unittest.TestCase):
    def test_raw_and_gzip_docker_layers_remain_single_compressed_tar(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            entry = tarfile.TarInfo("fixture")
            entry.size = 4
            archive.addfile(entry, io.BytesIO(b"data"))
        raw = buffer.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            for data in [raw, gzip.compress(raw)]:
                with self.subTest(compressed=data[:2] == b"\x1f\x8b"):
                    target = Path(directory) / "layer.gz"
                    copy_layer(io.BytesIO(data), target)
                    self.assertEqual(gzip.decompress(target.read_bytes()), raw)


if __name__ == "__main__":
    unittest.main()

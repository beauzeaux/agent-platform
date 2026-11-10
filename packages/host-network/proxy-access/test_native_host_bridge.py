import http.client
import http.server
import json
import socket
import ssl
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import host_bridge


BEARER = "Bearer fixture.header.signature"
STREAM = b'data: {"text":"first"}\r\n\r\ndata: \xe2\x98\x83\n\ndata: [DONE]\n\n'
TRACE = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"


class Origin(http.server.BaseHTTPRequestHandler):
    requests = []
    truncate = False
    chunked = False

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        Origin.requests.append({"method": self.command, "path": self.path,
                                "headers": dict(self.headers),
                                "body": self.rfile.read(int(self.headers.get("Content-Length", "0")))})
        if Origin.chunked:
            self.protocol_version = "HTTP/1.1"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Set-Cookie", "upstream-only=fixture")
        if Origin.chunked:
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
        else:
            self.send_header("Content-Length", str(len(STREAM) + (10 if Origin.truncate else 0)))
        self.end_headers()
        try:
            if Origin.chunked:
                for chunk in [STREAM[:11], STREAM[11:38], STREAM[38:]]:
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            else:
                self.wfile.write(STREAM)
        except OSError:
            pass


class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        Origin.requests = []
        Origin.truncate = False
        Origin.chunked = False
        self.origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        self.bridge = http.server.ThreadingHTTPServer(("127.0.0.1", 0), host_bridge.NativeHandler)
        self.bridge.settings = {"llm_host": "127.0.0.1", "llm_port": self.origin.server_port,
                                "windows_python": "fixed-python", "windows_agency_relay": "fixed-relay"}
        self.bridge.gateway_key = "must-not-replace-native-bearer"
        for server in (self.origin, self.bridge):
            threading.Thread(target=lambda target=server: target.serve_forever(poll_interval=0.01),
                             daemon=True).start()
        self.connection = http.client.HTTPConnection("127.0.0.1", self.bridge.server_port, timeout=5)

    def tearDown(self):
        self.connection.close()
        for server in (self.bridge, self.origin):
            server.shutdown()
            server.server_close()

    def request(self, method, route, body=None, headers=None):
        self.connection.request(method, route, body, {"Authorization": BEARER, **(headers or {})})
        response = self.connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        self.connection.close()
        return result

    def test_native_model_bearer_body_stream_and_safe_trace_headers_are_unchanged(self):
        body = b'{"model":"configured-native-model","input":"fixture"}'
        status, response, headers = self.request("POST", "/v1/responses", body, {
            "Content-Type": "application/json; charset=utf-8", "traceparent": TRACE, "tracestate": "lab=canary",
            "Cookie": "client-only=fixture", "X-Agency-Actor": "spoof", "X-Agency-Actor-Uid": "spoof-uid",
            "X-Agency-Bridge-Key": "spoof-key", "Proxy-Authorization": "Bearer spoof-proxy",
            "X-Forwarded-Host": "arbitrary-origin.invalid",
        })
        self.assertEqual((status, response), (200, STREAM))
        self.assertNotIn("Set-Cookie", headers)
        observed = Origin.requests[0]
        self.assertEqual(observed["body"], body)
        self.assertEqual(observed["headers"]["Authorization"], BEARER)
        self.assertEqual(observed["headers"]["traceparent"], TRACE)
        self.assertEqual(observed["headers"]["tracestate"], "lab=canary")
        self.assertEqual(set(map(str.lower, observed["headers"])),
                         {"host", "accept-encoding", "authorization", "content-length",
                          "content-type", "traceparent", "tracestate"})

    def test_model_discovery_and_chunked_upload_use_only_fixed_routes(self):
        self.assertEqual(self.request("GET", "/v1/models")[0], 200)
        self.connection.request("POST", "/v1/responses", iter([b'{"input":', b'"fixture"}']),
                                {"Authorization": BEARER, "Content-Type": "application/json"},
                                encode_chunked=True)
        response = self.connection.getresponse()
        self.assertEqual(response.read(), STREAM)
        self.assertEqual(Origin.requests[-1]["body"], b'{"input":"fixture"}')
        self.assertEqual(Origin.requests[-1]["headers"]["Authorization"], BEARER)

    def test_native_chat_preserves_tool_payload_stream_and_identity(self):
        body = b'{"model":"gemini-4-argon","messages":[],"tools":[{"type":"function"}],"stream":true}'
        status, response, _ = self.request("POST", "/v1/chat/completions", body,
                                           {"Content-Type": "application/json"})
        self.assertEqual((status, response), (200, STREAM))
        self.assertEqual(Origin.requests[0]["path"], "/v1/chat/completions")
        self.assertEqual(Origin.requests[0]["body"], body)
        self.assertEqual(Origin.requests[0]["headers"]["Authorization"], BEARER)

    def test_chunked_upstream_sse_is_byte_preserving_and_stream_limit_never_replays(self):
        Origin.chunked = True
        self.assertEqual(self.request("POST", "/v1/responses", b"{}")[:2], (200, STREAM))
        self.bridge.settings["max_response_bytes"] = 10
        self.connection.request("POST", "/v1/responses", b"{}", {"Authorization": BEARER})
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        with self.assertRaises(http.client.IncompleteRead):
            response.read()
        self.assertEqual(len(Origin.requests), 2)

    def test_unknown_routes_methods_queries_and_absolute_origins_never_reach_any_upstream(self):
        with patch.object(host_bridge.subprocess, "run") as process:
            for method, route in [
                ("GET", "/health"), ("POST", "/v1/models"), ("GET", "/v1/responses"),
                ("GET", "/v1/chat/completions"), ("POST", "/v1/chat/completions?other=1"),
                ("POST", "/v1/completions"),
                ("GET", "//v1/models"),
                ("POST", "/v1/responses?target=other"), ("POST", "http://other.invalid/v1/responses"),
                ("GET", "/agency/mcp"), ("DELETE", "/agency/plugins"), ("POST", "/agency/../v1/responses"),
                ("CONNECT", "other.invalid:443"), ("PUT", "/v1/responses"), ("TRACE", "/v1/models"),
            ]:
                with self.subTest(method=method, route=route):
                    self.assertEqual(self.request(method, route)[0], 403)
            process.assert_not_called()
        self.assertEqual(Origin.requests, [])

    def test_invalid_framing_body_limits_and_header_ambiguity_never_reach_origin(self):
        self.bridge.settings["max_request_bytes"] = 16
        cases = [
            ([("Content-Length", "0"), ("Content-Length", "0")], b"", 400),
            ([("Content-Length", "0"), ("Transfer-Encoding", "chunked")], b"", 400),
            ([("Transfer-Encoding", "gzip, chunked")], b"", 400),
            ([("Content-Length", "+1")], b"", 400),
            ([("Content-Length", "-1")], b"", 400),
            ([("Content-Length", "17")], b"", 413),
            ([("Content-Length", "9" * 5000)], b"", 413),
            ([("Transfer-Encoding", "chunked")], b"11\r\n", 413),
            ([("Transfer-Encoding", "chunked")], b"2\r\nabXX", 400),
            ([("Transfer-Encoding", "chunked")], b"0\r\nInjected: value\r\n\r\n", 400),
            ([("Content-Length", "0"), ("Authorization", BEARER)], b"", 400),
            ([("Content-Length", "0"), ("Content-Type", "text/plain")], b"", 415),
            ([("Content-Length", "0"), ("Content-Encoding", "gzip")], b"", 400),
            ([("Content-Length", "0"), ("Mcp-Session-Id", "a"), ("Mcp-Session-Id", "b")], b"", 400),
        ]
        for headers, body, expected in cases:
            with self.subTest(headers=headers):
                self.connection.putrequest("POST", "/v1/responses")
                self.connection.putheader("Authorization", BEARER)
                for name, value in headers:
                    self.connection.putheader(name, value)
                self.connection.endheaders(body)
                response = self.connection.getresponse()
                self.assertEqual(response.status, expected)
                response.read()
                self.connection.close()
        self.assertEqual(Origin.requests, [])

    def test_missing_authentication_and_unframed_or_get_bodies_are_rejected(self):
        self.connection.request("POST", "/v1/responses", b"{}", {"X-Agency-Actor": "spoof"})
        response = self.connection.getresponse()
        self.assertEqual(response.status, 401)
        response.read()
        self.connection.close()
        self.assertEqual(self.request("GET", "/v1/models", b"body")[0], 400)
        self.connection.putrequest("POST", "/v1/responses")
        self.connection.putheader("Authorization", BEARER)
        self.connection.endheaders()
        response = self.connection.getresponse()
        self.assertEqual(response.status, 411)
        response.read()
        self.assertEqual(Origin.requests, [])

    def test_truncated_body_is_not_forwarded(self):
        with socket.create_connection(("127.0.0.1", self.bridge.server_port), timeout=5) as connection:
            connection.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: fixture\r\nAuthorization: " +
                               BEARER.encode() + b"\r\nContent-Length: 4\r\n\r\nab")
            connection.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(connection)
            response.begin()
            self.assertEqual(response.status, 400)
            response.read()
        self.assertEqual(Origin.requests, [])

    def test_request_deadline_closes_stalled_body_without_contacting_origin(self):
        self.bridge.settings["request_timeout_seconds"] = 0.05
        with socket.create_connection(("127.0.0.1", self.bridge.server_port), timeout=5) as connection:
            connection.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: fixture\r\nAuthorization: " +
                               BEARER.encode() + b"\r\nContent-Length: 4\r\n\r\na")
            self.assertEqual(connection.recv(1024), b"")
        self.assertEqual(Origin.requests, [])

    def test_agency_envelope_contains_native_bearer_and_session_but_no_spoofed_identity(self):
        reply = SimpleNamespace(returncode=0, stdout=json.dumps({
            "status": 200, "body": '{"jsonrpc":"2.0","result":{}}', "session": "server-session",
        }).encode())
        with patch.object(host_bridge.subprocess, "run", return_value=reply) as process:
            for route in ["/agency/mcp", "/agency/policy", "/agency/plugins"]:
                status, _, headers = self.request("POST", route, b'{"id":1}', {
                    "Content-Type": "application/json", "Mcp-Session-Id": "client-session",
                    "X-Agency-Actor": "spoof", "X-Agency-Actor-Uid": "spoof", "X-Agency-Bridge-Key": "spoof",
                })
                self.assertEqual(status, 200)
                self.assertEqual(headers["Mcp-Session-Id"], "server-session")
                call = process.call_args
                self.assertEqual(call.args[0], ["fixed-python", "-u", "fixed-relay"])
                self.assertEqual(json.loads(call.kwargs["input"]), {
                    "method": "POST", "path": route, "body": {"id": 1},
                    "authorization": BEARER, "session": "client-session",
                })
            self.assertEqual(self.request("DELETE", "/agency/mcp")[0], 200)
            self.assertEqual(process.call_count, 4)
        self.assertEqual(Origin.requests, [])

    def test_managed_identity_envelope_preserves_only_injected_actor_header_and_query(self):
        reply = SimpleNamespace(returncode=0, stdout=json.dumps({
            "status": 200, "body": '{"access_token":"test-only"}', "session": None,
        }).encode())
        route = ("/metadata/identity/oauth2/token?api-version=2019-08-01"
                 "&resource=499b84ac-1321-427f-aa17-267ca6975798")
        with patch.object(host_bridge.subprocess, "run", return_value=reply) as process:
            self.connection.request("GET", route, headers={"X-IDENTITY-HEADER": BEARER})
            response = self.connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            self.assertEqual(json.loads(process.call_args.kwargs["input"]), {
                "method": "GET", "path": route, "body": {}, "identityHeader": BEARER, "session": None,
            })
        self.assertEqual(Origin.requests, [])

    def test_managed_identity_allows_exact_azure_management_resource(self):
        reply = SimpleNamespace(returncode=0, stdout=json.dumps({
            "status": 200, "body": '{"access_token":"test-only"}', "session": None,
        }).encode())
        route = ("/metadata/identity/oauth2/token?api-version=2019-08-01"
                 "&resource=https%3A%2F%2Fmanagement.azure.com%2F")
        with patch.object(host_bridge.subprocess, "run", return_value=reply) as process:
            self.connection.request("GET", route, headers={"X-IDENTITY-HEADER": BEARER})
            response = self.connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            self.assertEqual(json.loads(process.call_args.kwargs["input"])["path"], route)

    def test_managed_identity_requires_its_injected_header_and_fixed_route(self):
        with patch.object(host_bridge.subprocess, "run") as process:
            route = ("/metadata/identity/oauth2/token?api-version=2019-08-01"
                     "&resource=499b84ac-1321-427f-aa17-267ca6975798")
            self.assertEqual(self.request("GET", route)[0], 401)
            self.connection.request("GET", route, headers={"X-IDENTITY-HEADER": "not-a-bearer"})
            response = self.connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            self.connection.close()
            self.connection.request("GET", "/metadata/identity/oauth2/token?api-version=2019-08-01"
                                    "&resource=other", headers={"X-IDENTITY-HEADER": BEARER})
            response = self.connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            self.connection.close()
            process.assert_not_called()

    def test_upstream_errors_are_not_replayed(self):
        with patch.object(host_bridge.subprocess, "run", side_effect=host_bridge.subprocess.TimeoutExpired("fixed", 200)) as process:
            self.assertEqual(self.request("POST", "/agency/mcp", b"{}")[0], 502)
            self.assertEqual(process.call_count, 1)
        self.bridge.settings["max_response_bytes"] = 1
        self.assertEqual(self.request("POST", "/v1/responses", b"{}")[0], 502)
        self.assertEqual(len(Origin.requests), 1)

    def test_invalid_or_nonfinite_agency_json_never_launches_windows_interop(self):
        with patch.object(host_bridge.subprocess, "run") as process:
            for body in [b"not-json", b'{"value":NaN}']:
                with self.subTest(length=len(body)):
                    self.assertEqual(self.request("POST", "/agency/mcp", body)[0], 400)
            process.assert_not_called()

    def test_expanded_stdio_envelope_limit_is_enforced_before_windows_interop(self):
        body = ('{"value":"' + "\u2603" * 1_400_000 + '"}').encode()
        with patch.object(host_bridge.subprocess, "run") as process:
            self.assertEqual(self.request("POST", "/agency/mcp", body)[0], 413)
            process.assert_not_called()

    def test_truncated_accepted_model_response_closes_stream_without_retry_or_fake_terminal(self):
        Origin.truncate = True
        self.connection.request("POST", "/v1/responses", b"{}", {"Authorization": BEARER})
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        with self.assertRaises(http.client.IncompleteRead):
            response.read()
        self.assertEqual(len(Origin.requests), 1)


class NativeConfigurationTests(unittest.TestCase):
    def test_native_is_disabled_by_default_and_cannot_bind_public_or_other_ports(self):
        self.assertIsNone(host_bridge.native_server({}))
        self.assertIsNone(host_bridge.native_server({"native_frontend": {"enabled": False}}))
        for values in [{"listen_address": "0.0.0.0"}, {"listen_port": 8444},
                       {"max_connections": 33}, {"max_request_bytes": 30_000_001},
                       {"tls_skip_verify": True}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                host_bridge.native_server({"windows_agency_relay": "fixed",
                                           "native_frontend": {"enabled": True, **values}})

    def test_native_server_tls_has_no_client_ca_but_legacy_remains_mutual_tls(self):
        with patch.object(host_bridge.ssl, "SSLContext") as constructor:
            native = Mock()
            legacy = Mock()
            constructor.side_effect = [native, legacy]
            self.assertIs(host_bridge.tls_context("new-native.crt", "new-native.key"), native)
            native.load_cert_chain.assert_called_once_with("new-native.crt", "new-native.key")
            native.load_verify_locations.assert_not_called()
            self.assertEqual(native.minimum_version, ssl.TLSVersion.TLSv1_2)
            host_bridge.tls_context("legacy.crt", "legacy.key", "legacy-client-ca.crt")
            legacy.load_verify_locations.assert_called_once_with(cafile="legacy-client-ca.crt")
            self.assertEqual(legacy.verify_mode, ssl.CERT_REQUIRED)

    def test_legacy_ado_listener_can_be_explicitly_disconnected(self):
        self.assertIsNone(host_bridge.legacy_ado_server(
            {"legacy_ado_frontend_enabled": False}, Mock()))
        with self.assertRaises(ValueError):
            host_bridge.legacy_ado_server(
                {"legacy_ado_frontend_enabled": "false"}, Mock())


class LegacyAgencyTests(unittest.TestCase):
    def test_legacy_mtls_agency_path_keeps_actor_envelope_and_does_not_opt_into_native_bearer(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), host_bridge.AgencyHandler)
        server.settings = {"windows_python": "fixed-python", "windows_agency_relay": "fixed-relay"}
        threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True).start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        reply = SimpleNamespace(returncode=0, stdout=json.dumps({"status": 200, "body": "{}", "session": None}).encode())
        try:
            with patch.object(host_bridge.subprocess, "run", return_value=reply) as process:
                connection.request("POST", "/agency/mcp", b"{}", {
                    "Authorization": BEARER, "X-Agency-Actor": "legacy/actor",
                    "X-Agency-Actor-Uid": "legacy-uid", "Mcp-Session-Id": "legacy-session",
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                self.assertEqual(json.loads(process.call_args.kwargs["input"]), {
                    "method": "POST", "path": "/agency/mcp", "body": {}, "actor": "legacy/actor",
                    "actorUID": "legacy-uid", "session": "legacy-session",
                })
        finally:
            connection.close()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

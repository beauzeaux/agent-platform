import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import windows_relay


class WindowsRelayTests(unittest.TestCase):
    def connection(self):
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"jsonrpc":"2.0","result":{"ok":true}}'
        response.getheader.return_value = "server-session"
        return connection

    def test_native_authorization_is_unchanged_without_reading_or_sending_legacy_identity(self):
        connection = self.connection()
        factory = Mock(return_value=connection)
        request = {"method": "POST", "path": "/agency/mcp", "body": {"id": 1},
                   "authorization": "Bearer fixture.header.signature", "session": "client-session",
                   "actor": "spoof", "actorUID": "spoof-uid"}
        with patch.object(Path, "read_text", side_effect=AssertionError("native must not read bridge key")):
            reply = windows_relay.relay(request, factory)
        factory.assert_called_once_with("127.0.0.1", 15442, timeout=195)
        self.assertEqual(connection.request.call_args.args[3], {
            "Authorization": request["authorization"], "Content-Type": "application/json",
            "Mcp-Session-Id": "client-session",
        })
        self.assertEqual(reply["session"], "server-session")
        connection.close.assert_called_once()

    def test_legacy_envelope_still_uses_bridge_key_and_actor_identity_without_authorization(self):
        connection = self.connection()
        with patch.object(Path, "read_text", return_value=json.dumps({"bridgeKey": "legacy-fixture"})):
            windows_relay.relay({"method": "POST", "path": "/agency/policy", "body": {},
                                 "actor": "space/actor", "actorUID": "uid", "session": "legacy-session"},
                                Mock(return_value=connection))
        self.assertEqual(connection.request.call_args.args[3], {
            "X-Agency-Bridge-Key": "legacy-fixture", "X-Agency-Actor": "space/actor",
            "X-Agency-Actor-Uid": "uid", "Content-Type": "application/json", "Mcp-Session-Id": "legacy-session",
        })

    def test_invalid_native_authorization_cannot_downgrade_to_legacy_or_contact_an_origin(self):
        factory = Mock()
        with patch.object(Path, "read_text", side_effect=AssertionError("no legacy fallback")):
            for authorization in [None, "", "Basic fixture", "Bearer value\r\nInjected: header"]:
                with self.subTest(authorization=authorization), self.assertRaises(ValueError):
                    windows_relay.relay({"method": "POST", "path": "/agency/mcp", "body": {},
                                         "authorization": authorization}, factory)
            for method, route in [("POST", "http://other.invalid/agency/mcp"), ("GET", "/agency/mcp"),
                                  ("DELETE", "/agency/plugins")]:
                with self.subTest(route=route), self.assertRaises(ValueError):
                    windows_relay.relay({"method": method, "path": route, "body": {},
                                         "authorization": "Bearer fixture.header.signature"}, factory)
        factory.assert_not_called()

    def test_response_body_limit_closes_connection_without_retry(self):
        connection = self.connection()
        connection.getresponse.return_value.read.return_value = b"x" * 8_000_001
        with self.assertRaises(ValueError):
            windows_relay.relay({"method": "POST", "path": "/agency/mcp", "body": {},
                                 "authorization": "Bearer fixture.header.signature"}, Mock(return_value=connection))
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    def test_managed_identity_request_forwards_only_injected_actor_header(self):
        connection = self.connection()
        factory = Mock(return_value=connection)
        route = ("/metadata/identity/oauth2/token?api-version=2019-08-01"
                 "&resource=499b84ac-1321-427f-aa17-267ca6975798")

        windows_relay.relay({
            "method": "GET", "path": route, "body": {},
            "identityHeader": "Bearer fixture.header.signature", "session": None,
        }, factory)

        self.assertEqual(connection.request.call_args.args,
                         ("GET", route, None, {"X-IDENTITY-HEADER": "Bearer fixture.header.signature"}))

    def test_managed_identity_allows_exact_azure_management_resource(self):
        connection = self.connection()
        route = ("/metadata/identity/oauth2/token?api-version=2019-08-01"
                 "&resource=https%3A%2F%2Fmanagement.azure.com%2F")
        windows_relay.relay({
            "method": "GET", "path": route, "body": {},
            "identityHeader": "Bearer fixture.header.signature", "session": None,
        }, Mock(return_value=connection))
        self.assertEqual(connection.request.call_args.args[1], route)


if __name__ == "__main__":
    unittest.main()

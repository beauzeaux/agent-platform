import base64
import json
import pathlib
import sys
import unittest


SCRIPTS = pathlib.Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from wireguard_config import parse_sites_document, render_nftables_rules, render_wireguard_config, validate_peer_name


PUBLIC_KEY_1 = base64.b64encode(bytes(range(32))).decode("ascii")
PUBLIC_KEY_2 = base64.b64encode(bytes(range(1, 33))).decode("ascii")


class WireGuardConfigTests(unittest.TestCase):
    def test_peer_and_gateway_routes_render_in_stable_order(self):
        document = json.dumps(
            [
                {
                    "name": "worker-b",
                    "publicKey": PUBLIC_KEY_2,
                    "overlayAddress": "10.82.0.3/32",
                    "subnetPrefixes": ["10.90.0.0/16"],
                },
                {
                    "name": "worker-a",
                    "publicKey": PUBLIC_KEY_1,
                    "overlayAddress": "10.82.0.2/32",
                    "enabled": True,
                },
            ]
        )

        peers = parse_sites_document(document, ["10.82.0.1/24"])
        config = render_wireguard_config("private-key", 51820, peers)

        self.assertEqual(["worker-a", "worker-b"], [peer.name for peer in peers])
        self.assertIn("AllowedIPs = 10.82.0.3/32, 10.90.0.0/16", config)

    def test_disabled_peer_is_omitted(self):
        document = json.dumps(
            [
                {
                    "name": "worker",
                    "publicKey": PUBLIC_KEY_1,
                    "overlayAddress": "10.82.0.2/32",
                    "enabled": False,
                }
            ]
        )

        self.assertEqual((), parse_sites_document(document, ["10.82.0.1/24"]))

    def test_duplicate_or_overlapping_routes_are_rejected(self):
        document = json.dumps(
            [
                {
                    "name": "gateway-a",
                    "publicKey": PUBLIC_KEY_1,
                    "overlayAddress": "10.82.0.2/32",
                    "subnetPrefixes": ["10.90.0.0/16"],
                },
                {
                    "name": "gateway-b",
                    "publicKey": PUBLIC_KEY_2,
                    "overlayAddress": "10.82.0.3/32",
                    "subnetPrefixes": ["10.90.1.0/24"],
                },
            ]
        )

        with self.assertRaisesRegex(ValueError, "overlaps"):
            parse_sites_document(document, ["10.82.0.1/24"])

    def test_hub_address_claim_is_rejected(self):
        document = json.dumps(
            [{"name": "worker", "publicKey": PUBLIC_KEY_1, "overlayAddress": "10.82.0.1/32"}]
        )

        with self.assertRaisesRegex(ValueError, "claims a hub address"):
            parse_sites_document(document, ["10.82.0.1/24"])

    def test_invalid_public_key_is_rejected(self):
        document = json.dumps(
            [{"name": "worker", "publicKey": "not-a-wireguard-key", "overlayAddress": "10.82.0.2/32"}]
        )

        with self.assertRaisesRegex(ValueError, "valid base64"):
            parse_sites_document(document, ["10.82.0.1/24"])

    def test_loopback_route_is_rejected(self):
        document = json.dumps(
            [
                {
                    "name": "worker",
                    "publicKey": PUBLIC_KEY_1,
                    "overlayAddress": "10.82.0.2/32",
                    "subnetPrefixes": ["127.0.0.1/32"],
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "not routable"):
            parse_sites_document(document, ["10.82.0.1/24"])

    def test_public_route_is_rejected(self):
        document = json.dumps(
            [
                {
                    "name": "worker",
                    "publicKey": PUBLIC_KEY_1,
                    "overlayAddress": "10.82.0.2/32",
                    "subnetPrefixes": ["8.8.8.0/24"],
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "not routable"):
            parse_sites_document(document, ["10.82.0.1/24"])

    def test_unsafe_peer_name_is_rejected_by_hub(self):
        document = json.dumps(
            [
                {
                    "name": "worker\n[Peer]",
                    "publicKey": PUBLIC_KEY_1,
                    "overlayAddress": "10.82.0.2/32",
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "Peer name"):
            parse_sites_document(document, ["10.82.0.1/24"])

    def test_unknown_fields_are_rejected(self):
        document = json.dumps(
            [
                {
                    "name": "worker",
                    "publicKey": PUBLIC_KEY_1,
                    "overlayAddress": "10.82.0.2/32",
                    "endpoint": "example:51820",
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "unknown fields"):
            parse_sites_document(document, ["10.82.0.1/24"])

    def test_firewall_allows_only_peer_and_attached_network_forwarding(self):
        peers = parse_sites_document(
            json.dumps(
                [{"name": "worker", "publicKey": PUBLIC_KEY_1, "overlayAddress": "10.82.0.2/32"}]
            ),
            ["10.82.0.1/24"],
        )

        rules = render_nftables_rules("wg0", "eth0", ["10.81.0.0/24"], peers)

        self.assertIn("policy drop", rules)
        self.assertIn("ip daddr { 10.81.0.0/24 } accept", rules)
        self.assertNotIn("masquerade", rules)
        self.assertNotIn("0.0.0.0/0", rules)

    def test_peer_name_is_safe(self):
        self.assertEqual("worker-1", validate_peer_name("worker-1"))
        with self.assertRaises(ValueError):
            validate_peer_name("../worker")

    def test_overlay_address_must_be_single_host(self):
        document = json.dumps(
            [{"name": "worker", "publicKey": PUBLIC_KEY_1, "overlayAddress": "10.82.0.0/24"}]
        )

        with self.assertRaisesRegex(ValueError, "single-host"):
            parse_sites_document(document, ["10.82.0.1/24"])


if __name__ == "__main__":
    unittest.main()

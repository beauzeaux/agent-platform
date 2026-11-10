#!/usr/bin/env python3

import base64
import binascii
import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Peer:
    name: str
    public_key: str
    allowed_ips: tuple[str, ...]


PEER_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def validate_peer_name(value: object) -> str:
    if not isinstance(value, str) or not PEER_NAME_PATTERN.fullmatch(value):
        raise ValueError("Peer name must contain 1-63 lowercase letters, digits, or interior hyphens")
    return value


def validate_public_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("publicKey must be a string")

    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("publicKey must be valid base64") from error

    if len(decoded) != 32:
        raise ValueError("publicKey must encode exactly 32 bytes")

    return value


def validate_network(value: object) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    if not isinstance(value, str):
        raise ValueError("allowedIps entries must be strings")

    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise ValueError(f"Invalid network '{value}'") from error

    if (
        not network.is_private
        or network.is_link_local
        or network.is_loopback
        or network.is_multicast
        or network.is_unspecified
    ):
        raise ValueError(f"Network '{value}' is not routable through the hub")

    return network


def parse_sites_document(value: str, hub_addresses: Iterable[str]) -> tuple[Peer, ...]:
    hub_ips = {ipaddress.ip_interface(address).ip for address in hub_addresses}
    peers: list[Peer] = []
    public_keys: set[str] = set()
    claimed_networks: list[tuple[str, ipaddress.IPv4Network | ipaddress.IPv6Network]] = []

    try:
        sites = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("Site configuration contains invalid JSON") from error
    if not isinstance(sites, list):
        raise ValueError("Site configuration must be a JSON array")

    site_documents: list[tuple[str, dict[str, object]]] = []
    for document in sites:
        if not isinstance(document, dict):
            raise ValueError("Each site must be a JSON object")
        peer_name = validate_peer_name(document.get("name"))
        site_documents.append((peer_name, document))

    for peer_name, document in sorted(site_documents):
        unknown_fields = set(document) - {"enabled", "name", "overlayAddress", "publicKey", "subnetPrefixes"}
        if unknown_fields:
            raise ValueError(f"Site '{peer_name}' has unknown fields: {', '.join(sorted(unknown_fields))}")

        enabled = document.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"Site '{peer_name}' enabled must be a boolean")
        if not enabled:
            continue

        public_key = validate_public_key(document.get("publicKey"))
        if public_key in public_keys:
            raise ValueError(f"Site '{peer_name}' reuses another site's public key")
        public_keys.add(public_key)

        overlay_address = validate_network(document.get("overlayAddress"))
        if overlay_address.num_addresses != 1:
            raise ValueError(f"Site '{peer_name}' overlayAddress must be a single-host prefix")

        subnet_prefixes = document.get("subnetPrefixes", [])
        if not isinstance(subnet_prefixes, list):
            raise ValueError(f"Site '{peer_name}' subnetPrefixes must be an array")

        networks = sorted(
            [overlay_address, *(validate_network(value) for value in subnet_prefixes)],
            key=lambda item: (item.version, item),
        )
        for network in networks:
            if any(hub_ip in network for hub_ip in hub_ips):
                raise ValueError(f"Site '{peer_name}' claims a hub address through '{network}'")

            for claimed_peer, claimed_network in claimed_networks:
                if network.version == claimed_network.version and network.overlaps(claimed_network):
                    raise ValueError(
                        f"Site '{peer_name}' network '{network}' overlaps '{claimed_network}' owned by '{claimed_peer}'"
                    )
            claimed_networks.append((peer_name, network))

        peers.append(Peer(peer_name, public_key, tuple(str(network) for network in networks)))

    return tuple(peers)


def render_wireguard_config(private_key: str, listen_port: int, peers: Iterable[Peer]) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"ListenPort = {listen_port}",
    ]

    for peer in peers:
        lines.extend(
            [
                "",
                f"# {peer.name}",
                "[Peer]",
                f"PublicKey = {peer.public_key}",
                f"AllowedIPs = {', '.join(peer.allowed_ips)}",
            ]
        )

    return "\n".join(lines) + "\n"


def render_nftables_rules(
    wireguard_interface: str,
    host_interface: str,
    attached_networks: Iterable[str],
    peers: Iterable[Peer],
) -> str:
    attached = [validate_network(value) for value in attached_networks]
    peer_networks = [validate_network(value) for peer in peers for value in peer.allowed_ips]

    lines = [
        "table inet wireguard_hub {",
        "  chain forward {",
        "    type filter hook forward priority filter; policy drop;",
        "    ct state established,related accept",
        f'    iifname "{wireguard_interface}" oifname "{wireguard_interface}" accept',
    ]

    for version, family in ((4, "ip"), (6, "ip6")):
        attached_values = sorted(str(network) for network in attached if network.version == version)
        peer_values = sorted(str(network) for network in peer_networks if network.version == version)
        if attached_values:
            lines.append(
                f'    iifname "{wireguard_interface}" oifname "{host_interface}" '
                f"{family} daddr {{ {', '.join(attached_values)} }} accept"
            )
        if attached_values and peer_values:
            lines.append(
                f'    iifname "{host_interface}" oifname "{wireguard_interface}" '
                f"{family} saddr {{ {', '.join(attached_values)} }} "
                f"{family} daddr {{ {', '.join(peer_values)} }} accept"
            )

    lines.extend(["  }", "}", ""])
    return "\n".join(lines)

#!/usr/bin/env python3

import os
import pathlib
import subprocess
import sys
import tempfile

from wireguard_config import parse_sites_document, render_nftables_rules, render_wireguard_config


WIREGUARD_DIRECTORY = pathlib.Path("/etc/wireguard")


def require_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Required environment variable {name} is missing")
    return value


def run(command: list[str], *, input_text: str | None = None) -> None:
    subprocess.run(command, input=input_text, check=True, text=True)


def read_private_key(path: pathlib.Path) -> str:
    private_key = path.read_text(encoding="utf-8").strip()
    if not private_key:
        raise ValueError(f"{path} is empty")
    return private_key


def configure_interface(interface: str, address: str, peers_config: str) -> None:
    address_interface = address.split("/", maxsplit=1)[0]
    if not interface_exists(interface):
        run(["ip", "link", "add", interface, "type", "wireguard"])
    run(["ip", "address", "replace", address, "dev", interface])

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=WIREGUARD_DIRECTORY) as config_file:
        config_file.write(peers_config)
        config_path = pathlib.Path(config_file.name)

    try:
        config_path.chmod(0o600)
        run(["wg", "syncconf", interface, str(config_path)])
    finally:
        config_path.unlink(missing_ok=True)

    run(["ip", "link", "set", interface, "up"])
    if ":" in address_interface:
        run(["ip", "-6", "route", "replace", address_interface + "/128", "dev", interface])


def interface_exists(interface: str) -> bool:
    return subprocess.run(
        ["ip", "link", "show", interface],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def configure_routes(interface: str, peers) -> None:
    run(["ip", "-4", "route", "flush", "dev", interface, "proto", "186"])
    run(["ip", "-6", "route", "flush", "dev", interface, "proto", "186"])
    for peer in peers:
        for network in peer.allowed_ips:
            family = "-6" if ":" in network else "-4"
            run(["ip", family, "route", "replace", network, "dev", interface, "proto", "186"])


def configure_firewall(rules: str) -> None:
    table_exists = subprocess.run(
        ["nft", "list", "table", "inet", "wireguard_hub"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    transaction = f"delete table inet wireguard_hub\n{rules}" if table_exists else rules
    run(["nft", "--check", "-f", "-"], input_text=transaction)
    run(["nft", "-f", "-"], input_text=transaction)


def main() -> int:
    address = require_environment("WIREGUARD_ADDRESS")
    attached_networks = tuple(filter(None, require_environment("WIREGUARD_ATTACHED_NETWORKS").split(",")))
    host_interface = require_environment("WIREGUARD_HOST_INTERFACE")
    interface = require_environment("WIREGUARD_INTERFACE")
    listen_port = int(require_environment("WIREGUARD_LISTEN_PORT"))
    sites_path = pathlib.Path(require_environment("WIREGUARD_SITES_PATH"))

    sites_document = sites_path.read_text(encoding="utf-8")
    peers = parse_sites_document(sites_document, [address])
    private_key = read_private_key(WIREGUARD_DIRECTORY / "private.key")
    wireguard_config = render_wireguard_config(private_key, listen_port, peers)
    nftables_rules = render_nftables_rules(interface, host_interface, attached_networks, peers)

    configure_interface(interface, address, wireguard_config)
    configure_routes(interface, peers)
    configure_firewall(nftables_rules)
    print(f"Applied {len(peers)} WireGuard peer(s)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"WireGuard synchronization failed: {error}", file=sys.stderr)
        sys.exit(1)

"""Extend only the protected existing WSL bridge configuration; never restart or deploy."""

import argparse
import copy
import json
import os
from pathlib import Path
import ssl
import stat
import subprocess
import uuid


STATE = Path("/home/nibeauli/kagent-lab/proxy-access")
OWNER = "host-capabilities-native-host"
DNS = "host-capabilities.proxy-access.svc.cluster.local"


def extended_settings(settings, certificate, key, enabled=False):
    if not isinstance(settings, dict):
        raise ValueError("Invalid owned bridge configuration.")
    if (settings.get("listen_address") != "10.82.0.2" or settings.get("llm_host") != "172.24.160.1"
            or settings.get("llm_port", 4141) != 4141
            or settings.get("windows_agency_relay") != r"Q:\tools\kagent-lab\agency-broker\windows_relay.py"
            or Path(settings.get("server_certificate", "")) == certificate
            or Path(settings.get("server_key", "")) == key):
        raise ValueError("Existing bridge ownership/configuration does not match.")
    previous = settings.get("native_frontend")
    if previous is not None and (not isinstance(previous, dict) or previous.get("managed_by") != OWNER):
        raise ValueError("Refusing to replace an unowned native frontend configuration.")
    result = copy.deepcopy(settings)
    result["native_frontend"] = {
        "managed_by": OWNER, "enabled": enabled, "listen_address": "10.82.0.2", "listen_port": 15445,
        "server_certificate": str(certificate), "server_key": str(key),
        "max_connections": 8, "max_request_bytes": 30_000_000, "max_response_bytes": 128_000_000,
        "request_timeout_seconds": 60, "response_timeout_seconds": 1800,
    }
    return result


def protected_file(path):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("Expected a root-owned private regular file.")


def configure(certificate, key, enabled=False, dry_run=False):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ValueError("Run in the existing primary WSL host as root.")
    state = STATE.resolve(strict=True)
    if state != STATE or STATE.is_symlink():
        raise ValueError("Unexpected bridge state path.")
    directory = STATE.stat()
    if directory.st_uid != 0 or stat.S_IMODE(directory.st_mode) & 0o077:
        raise ValueError("Bridge state directory is not protected.")
    config = STATE / "bridge.json"
    protected_file(config)
    for path in (certificate, key):
        if path.is_symlink() or path.resolve(strict=True).parent != STATE / "certificates":
            raise ValueError("Native certificate files must be in the existing protected certificate directory.")
        protected_file(path)
    original = config.read_bytes()
    settings = json.loads(original)
    updated = extended_settings(settings, certificate, key, enabled)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    ca = STATE / "certificates" / "ca.crt"
    protected_file(ca)
    subprocess.run(["openssl", "verify", "-CAfile", str(ca), "-purpose", "sslserver",
                    "-verify_hostname", DNS, str(certificate)],
                   check=True, capture_output=True, timeout=15)
    subprocess.run(["openssl", "x509", "-noout", "-checkend", "86400", "-in", str(certificate)],
                   check=True, capture_output=True, timeout=15)
    if dry_run:
        return
    stage = STATE / (".native-config-" + uuid.uuid4().hex)
    try:
        descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(json.dumps(updated, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        protected_file(config)
        if config.read_bytes() != original:
            raise ValueError("Bridge configuration changed during native frontend validation.")
        os.replace(stage, config)
        descriptor = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        stage.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    configure(args.certificate, args.key, args.enable, args.dry_run)
    print("Validated native frontend configuration." if args.dry_run
          else "Updated only native_frontend in the protected bridge configuration; no service restart performed.")


if __name__ == "__main__":
    main()

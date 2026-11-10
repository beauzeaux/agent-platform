"""Install the pinned private Collector without changing existing telemetry."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import shutil
import socket
import subprocess
import time
import urllib.request
import yaml

SOURCE = Path(__file__).resolve().parent
STATE = Path("/home/nibeauli/kagent-lab/host-observability")
WINDOWS = Path("/mnt/c/Users/nibeauli/AppData/Local/HostCapabilities/telemetry")
KUBE = ["/snap/bin/microk8s", "kubectl"]
HELM = ["/snap/bin/microk8s", "helm3"]
CHART_VERSION = "0.178.2"
CHART_SHA256 = "4c9732611ab301e1d5532f62fa0c715c2c89765ee74f88ec1825601b3f172324"
CHART_URL = (
    "https://github.com/open-telemetry/opentelemetry-helm-charts/releases/download/"
    f"opentelemetry-collector-{CHART_VERSION}/opentelemetry-collector-{CHART_VERSION}.tgz"
)


def run(arguments, **kwargs):
    return subprocess.run(arguments, check=True, **kwargs)


def prepare_certificates(renew_leaves=False):
    if os.geteuid() != 0:
        raise RuntimeError("Run as root inside the existing WSL distribution.")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    for certificate in ("ca", "server", "client"):
        if (STATE / f"{certificate}.crt").exists() != (STATE / f"{certificate}.key").exists():
            raise RuntimeError("Incomplete protected TLS state; refusing implicit rotation.")
    if not (STATE / "ca.crt").exists():
        run(["openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "365",
             "-keyout", str(STATE / "ca.key"), "-out", str(STATE / "ca.crt"),
             "-subj", "/CN=HostCapabilities telemetry CA",
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign"], capture_output=True)
    for name, extensions in (
        ("server", "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost,DNS:host-otel-collector.observability.svc.cluster.local,IP:127.0.0.1\n"),
        ("client", "extendedKeyUsage=clientAuth\n"),
    ):
        if renew_leaves or not (STATE / f"{name}.crt").exists():
            extension = STATE / f"{name}.ext"
            extension.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n" + extensions)
            run(["openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes",
                 "-keyout", str(STATE / f"{name}.key"), "-out", str(STATE / f"{name}.csr"),
                 "-subj", f"/CN=HostCapabilities telemetry {name}"], capture_output=True)
            run(["openssl", "x509", "-req", "-in", str(STATE / f"{name}.csr"),
                 "-CA", str(STATE / "ca.crt"), "-CAkey", str(STATE / "ca.key"), "-CAcreateserial",
                 "-days", "2", "-extfile", str(extension), "-out", str(STATE / f"{name}.crt")],
                capture_output=True)
            extension.unlink()
            (STATE / f"{name}.csr").unlink()
        run(["openssl", "x509", "-checkend", "3600", "-noout", "-in", str(STATE / f"{name}.crt")],
            capture_output=True)
    for path in STATE.iterdir():
        if path.is_file():
            path.chmod(0o600)
    password = STATE / "client-password"
    if not password.exists():
        import secrets
        password.write_text(secrets.token_hex(32))
        password.chmod(0o600)
    run(["openssl", "pkcs12", "-export", "-out", str(STATE / "client.pfx"),
         "-inkey", str(STATE / "client.key"), "-in", str(STATE / "client.crt"),
         "-certfile", str(STATE / "ca.crt"), "-passout", f"file:{password}"], capture_output=True)
    (STATE / "client.pfx").chmod(0o600)
    WINDOWS.mkdir(parents=True, exist_ok=True)
    acl_script = (
        "$ErrorActionPreference='Stop';"
        "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
        "$path=Join-Path $env:LOCALAPPDATA 'HostCapabilities\\telemetry';"
        "& icacls.exe $path /inheritance:r /grant:r "
        "'*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "
        '("*${sid}:(OI)(CI)F") | Out-Null;'
        "if($LASTEXITCODE -ne 0){throw 'Telemetry ACL protection failed.'}"
    )
    run(["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
         "-NoProfile", "-NonInteractive", "-Command", acl_script], capture_output=True)
    for name in ("ca.crt", "server.crt", "client.pfx", "client-password"):
        shutil.copyfile(STATE / name, WINDOWS / name)


def install(renew_leaves=False):
    prepare_certificates(renew_leaves)
    chart = STATE / f"opentelemetry-collector-{CHART_VERSION}.tgz"
    if not chart.exists():
        with urllib.request.urlopen(CHART_URL, timeout=60) as response:
            chart.write_bytes(response.read())
    if hashlib.sha256(chart.read_bytes()).hexdigest() != CHART_SHA256:
        raise RuntimeError("Collector chart checksum mismatch.")
    result = run([*KUBE, "-n", "observability", "create", "secret", "generic", "host-otel-tls",
                  "--from-file=tls.crt=" + str(STATE / "server.crt"),
                  "--from-file=tls.key=" + str(STATE / "server.key"),
                  "--from-file=ca.crt=" + str(STATE / "ca.crt"),
                  "--dry-run=client", "-o", "json"], capture_output=True)
    run([*KUBE, "apply", "-f", "-"], input=result.stdout, capture_output=True)
    generated_values = STATE / "collector-values.yaml"
    generated_values.write_text(yaml.safe_dump({"podAnnotations": {
        "host-capabilities/tls-sha256": hashlib.sha256((STATE / "server.crt").read_bytes()).hexdigest()}}))
    generated_values.chmod(0o600)
    run([*HELM, "lint", str(chart), "-f", str(SOURCE / "values.yaml"), "-f", str(generated_values)])
    run([*HELM, "upgrade", "--install", "host-otel", str(chart), "-n", "observability",
         "-f", str(SOURCE / "values.yaml"), "-f", str(generated_values), "--wait", "--timeout", "240s"])
    addresses = json.loads(run(["ip", "-j", "address"], capture_output=True, text=True).stdout)
    sources = []
    for interface in addresses:
        if interface["ifname"] in ("eth0", "wg0", "vxlan.calico"):
            for address in interface.get("addr_info", []):
                if address["family"] == "inet" and ipaddress.ip_address(address["local"]).is_private:
                    sources.append({"ipBlock": {"cidr": address["local"] + "/32"}})
    documents = list(yaml.safe_load_all((SOURCE / "resources.yaml").read_text()))
    for document in documents:
        if document["kind"] == "NetworkPolicy":
            for rule in document["spec"]["ingress"]:
                if any(port["port"] == 4318 for port in rule["ports"]):
                    rule["from"] = sources
    run([*KUBE, "apply", "-f", "-"], input=yaml.safe_dump_all(documents).encode(), capture_output=True)
    service = json.loads(run([*KUBE, "-n", "observability", "get", "service", "host-otel-collector",
                              "-o", "json"], capture_output=True).stdout)
    endpoint = str(ipaddress.ip_address(service["spec"]["clusterIP"])) + ":4318"
    unit = Path("/etc/systemd/system/host-otel-forward.service")
    desired = (SOURCE / "host-otel-forward.service").read_text().replace("@OTEL_COLLECTOR_ENDPOINT@", endpoint)
    if unit.exists() and "Description=HostCapabilities private OTLP access" not in unit.read_text():
        raise RuntimeError("Refusing to replace an unowned access unit.")
    socket_unit = Path("/etc/systemd/system/host-otel-forward.socket")
    if socket_unit.exists() and "Description=HostCapabilities private OTLP listener" not in socket_unit.read_text():
        raise RuntimeError("Refusing to replace an unowned access socket.")
    if socket_unit.exists():
        run(["systemctl", "stop", socket_unit.name])
    if unit.exists():
        run(["systemctl", "stop", unit.name])
        run(["systemctl", "disable", unit.name], capture_output=True)
    listeners = run(["ss", "-H", "-lntp", "sport = :14318"], capture_output=True, text=True).stdout
    for pid in set(re.findall(r"pid=(\d+)", listeners)):
        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if b"service/host-otel-collector" not in command or b"14318:4318" not in command:
            raise RuntimeError("The OTLP access port belongs to another process.")
        os.kill(int(pid), signal.SIGTERM)
    if listeners:
        time.sleep(1)
    unit.write_text(desired)
    unit.chmod(0o644)
    socket_unit.write_text((SOURCE / "host-otel-forward.socket").read_text())
    socket_unit.chmod(0o644)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", socket_unit.name])
    for attempt in range(60):
        try:
            with socket.create_connection(("127.0.0.1", 14318), timeout=1):
                break
        except OSError:
            if attempt == 59:
                raise RuntimeError("The owned OTLP access forward did not become ready.")
            time.sleep(0.5)
    print("Collector installed; Windows client material requires current-user ACL protection.")


def status():
    run([*KUBE, "-n", "observability", "get", "deployment,service,servicemonitor,networkpolicy",
         "-l", "app.kubernetes.io/instance=host-otel"])
    run(["systemctl", "is-active", "host-otel-forward.socket"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "status"])
    parser.add_argument("--renew-leaves", action="store_true")
    arguments = parser.parse_args()
    install(arguments.renew_leaves) if arguments.action == "install" else status()

"""Install a standard loopback-only Caddy front door; never change Windows DNS."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request

VERSION = "2.11.8"
DIGEST = "f032f8805e4df2ce5e1a8a624495c91e9ff763d8ff3ceddadd4784a07c472f40"
URL = f"https://github.com/caddyserver/caddy/releases/download/v{VERSION}/caddy_{VERSION}_linux_amd64.tar.gz"
STATE = Path("/home/nibeauli/kagent-lab/host-service-access")
INSTALL = Path("/usr/local/lib/host-service-access")
CONFIG = Path("/etc/host-service-access/caddy.json")
UNIT = Path("/etc/systemd/system/host-service-access.service")
CLUSTER_HOST = "cluster.internal"
ROUTES = {
    "kagent.internal": ("factory-executions", "factory-ui", 8080),
    "grafana.internal": ("observability", "kube-prom-stack-grafana", 80),
    "prometheus.internal": ("observability", "kube-prom-stack-kube-prome-prometheus", 9090),
    "tempo.internal": ("observability", "tempo", 3200),
    "loki.internal": ("observability", "loki", 3100),
    "swarm.internal": (None, "127.0.0.1", 18452),
}
UNIT_TEXT = """[Unit]
Description=HostCapabilities stable loopback service URLs (Caddy)
After=network-online.target snap.microk8s.daemon-kubelite.service
Wants=network-online.target

[Service]
Type=notify
TimeoutStartSec=30
ExecStart=/usr/local/lib/host-service-access/caddy run --config /etc/host-service-access/caddy.json
Restart=on-failure
RestartSec=5
DynamicUser=yes
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077

[Install]
WantedBy=multi-user.target
"""


def configuration(targets, headlamp=None):
    routes = []
    portal_hosts = [CLUSTER_HOST, "localhost", "127.0.0.1"]
    links = []
    def mount(prefix, target, hosts, strip=True):
        handlers = ([{"handler": "rewrite", "strip_path_prefix": prefix}] if strip else [])
        handlers.append({"handler": "reverse_proxy", "upstreams": [{"dial": target}]})
        routes.append({"match": [{"host": hosts, "path": [prefix, prefix + "/*"]}],
                       "handle": handlers, "terminal": True})
    for prefix, host in [("/kagent", "kagent.internal")]:
        if host in targets:
            mount(prefix, targets[host], [*portal_hosts, host])
            links.append(prefix + "/")
    if headlamp:
        mount("/headlamp", headlamp, [*portal_hosts, "kagent.internal"], strip=False)
        links.append("/headlamp/")
    for prefix, host in [("/tempo", "tempo.internal")]:
        if host in targets:
            mount(prefix, targets[host], portal_hosts)
            links.append(prefix + "/ready")
    if "loki.internal" in targets:
        for endpoint in ["ready", "metrics"]:
            routes.append({"match": [{"host": portal_hosts, "path": ["/loki/" + endpoint]}],
                           "handle": [{"handler": "rewrite", "strip_path_prefix": "/loki"},
                                      {"handler": "reverse_proxy",
                                       "upstreams": [{"dial": targets["loki.internal"]}]}], "terminal": True})
        mount("/loki", targets["loki.internal"], portal_hosts, strip=False)
        links.append("/loki/ready")
    for prefix, host in [("/grafana", "grafana.internal"), ("/prometheus", "prometheus.internal")]:
        if host in targets:
            routes.append({"match": [{"host": portal_hosts, "path": [prefix + "/api/*"]}],
                           "handle": [{"handler": "rewrite", "strip_path_prefix": prefix},
                                      {"handler": "reverse_proxy", "upstreams": [{"dial": targets[host]}]}],
                           "terminal": True})
            routes.append({"match": [{"host": portal_hosts, "path": [prefix, prefix + "/*"]}],
                           "handle": [{"handler": "static_response", "status_code": 302,
                                       "headers": {"Location": ["http://" + host + "/"]}}],
                           "terminal": True})
            links.append(prefix + "/ (UI migration pending; API already proxied)")
    routes.append({"match": [{"host": portal_hosts, "path": ["/"]}],
                   "handle": [{"handler": "static_response", "status_code": 200,
                               "headers": {"Content-Type": ["text/plain; charset=utf-8"]},
                               "body": "Cluster services:\n" + "\n".join(
                                   "http://" + CLUSTER_HOST + link for link in links) + "\n"}],
                   "terminal": True})
    routes.extend([{"match": [{"host": [host]}], "handle": [{"handler": "reverse_proxy",
               "upstreams": [{"dial": target}]}], "terminal": True}
              for host, target in targets.items()])
    routes.append({"handle": [{"handler": "static_response", "status_code": 404}]})
    return {"admin": {"disabled": True}, "logging": {"logs": {"default": {"level": "WARN"}}},
            "apps": {"http": {"servers": {"loopback": {"listen": ["127.0.0.1:80"],
                     "automatic_https": {"disable": True}, "routes": routes}}}}}


def main(apply=False):
    targets = {}
    for host, (namespace, service, port) in ROUTES.items():
        if namespace:
            data = subprocess.check_output(["/snap/bin/microk8s", "kubectl", "--request-timeout=8s",
                                           "get", "service", service, "-n", namespace, "-o", "json"])
            address = json.loads(data)["spec"]["clusterIP"]
            if not address.startswith("10.152.183."):
                raise RuntimeError("Unexpected private Service network; no listener was changed.")
        else:
            address = service
        targets[host] = f"{address}:{port}"
    optional = subprocess.check_output(["/snap/bin/microk8s", "kubectl", "--request-timeout=8s",
                                       "get", "service", "headlamp", "-n", "headlamp",
                                       "--ignore-not-found", "-o", "json"])
    headlamp = None
    if optional:
        service = json.loads(optional)
        if (service["metadata"].get("annotations", {}).get("meta.helm.sh/release-name") != "headlamp"
                or not service["spec"]["clusterIP"].startswith("10.152.183.")):
            raise RuntimeError("Headlamp Service ownership/network mismatch.")
        headlamp = service["spec"]["clusterIP"] + ":80"
    value = configuration(targets, headlamp)
    if not apply:
        print(json.dumps({"dryRun": True, "listen": "127.0.0.1:80", "routes": targets}))
        return
    if os.geteuid() != 0:
        raise RuntimeError("Apply inside primary WSL as root.")
    if UNIT.exists() and "HostCapabilities stable loopback service URLs" not in UNIT.read_text():
        raise RuntimeError("Refusing to overwrite an unowned systemd unit.")
    if CONFIG.exists() and not UNIT.exists():
        raise RuntimeError("Refusing to replace unowned service access configuration.")
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    INSTALL.mkdir(parents=True, mode=0o755, exist_ok=True)
    archive = STATE / f"caddy-{VERSION}.tar.gz"
    if not archive.exists():
        with urllib.request.urlopen(URL, timeout=120) as response:
            archive.write_bytes(response.read(100 * 1024 * 1024))
        archive.chmod(0o600)
    raw = archive.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DIGEST:
        raise RuntimeError("Official Caddy archive checksum mismatch.")
    with tarfile.open(fileobj=io.BytesIO(raw)) as package:
        for name in ("caddy", "LICENSE"):
            member = package.getmember(name)
            if not member.isfile() or member.size > 100 * 1024 * 1024:
                raise RuntimeError("Unexpected Caddy release layout.")
            destination = INSTALL / name
            stage = INSTALL / (name + ".next")
            stage.write_bytes(package.extractfile(member).read())
            stage.chmod(0o755 if name == "caddy" else 0o644)
            stage.replace(destination)
    (INSTALL / "caddy").chmod(0o755)
    (INSTALL / "LICENSE").chmod(0o644)
    CONFIG.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    staged = STATE / "caddy.next.json"
    staged.write_text(json.dumps(value, indent=2))
    subprocess.run([str(INSTALL / "caddy"), "validate", "--config", str(staged)], check=True)
    if CONFIG.exists():
        (STATE / "caddy.before.json").write_bytes(CONFIG.read_bytes())
    CONFIG.write_bytes(staged.read_bytes())
    CONFIG.chmod(0o644)
    UNIT.write_text(UNIT_TEXT)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "host-service-access.service"], check=True, capture_output=True)
    subprocess.run(["systemctl", "restart", "host-service-access.service"], check=True)
    subprocess.run(["systemctl", "is-active", "--quiet", "host-service-access.service"], check=True)
    (STATE / "deployment.json").write_text(json.dumps({"caddyVersion": VERSION, "sha256": DIGEST,
                                                     "listen": "127.0.0.1:80", "routes": targets}, indent=2))
    print(json.dumps({"installed": True, "listen": "127.0.0.1:80", "routes": targets,
                      "windowsHostsChanged": False, "globalTrustChanged": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)

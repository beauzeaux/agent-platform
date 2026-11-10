"""Root-only, idempotent host bridge preparation and Helm lifecycle."""

import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile


SOURCE = Path("/mnt/q/tools/kagent-lab/proxy-access")
STATE = Path("/home/nibeauli/kagent-lab/proxy-access")
UNIT = Path("/etc/systemd/system/kagent-proxy-access.service")
KUBE = ["/snap/bin/microk8s", "kubectl"]
HELM = ["/snap/bin/microk8s", "helm3"]
NAMESPACE = "proxy-access"
RELEASE = "agent-proxies"
SECRET = "agent-proxies-tls"


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def write(path, content, mode=0o600):
    path.write_text(content)
    path.chmod(mode)


def prepare():
    if os.geteuid() != 0:
        raise RuntimeError("Run in primary WSL as root.")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)
    certificates = STATE / "certificates"
    certificates.mkdir(mode=0o700, exist_ok=True)
    ca = certificates / "ca.crt"
    ca_key = certificates / "ca.key"
    if ca.exists() != ca_key.exists():
        raise RuntimeError("Incomplete CA; inspect protected state rather than silently replacing trust.")
    if not ca.exists():
        run(["openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes",
             "-keyout", str(ca_key), "-out", str(ca), "-days", "365",
             "-subj", "/CN=Kagent private proxy access",
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign"], capture_output=True)
    for name, subject, extensions in [
        ("bridge", "bridge.proxy-access.internal",
         "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:bridge.proxy-access.internal,IP:10.82.0.2\n"),
        ("gateway", "agent-proxies.proxy-access.svc",
         "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:agent-proxies,DNS:agent-proxies.proxy-access,"
         "DNS:agent-proxies.proxy-access.svc,DNS:agent-proxies.proxy-access.svc.cluster.local\n"),
        ("bridge-client", "agent-proxies", "extendedKeyUsage=clientAuth\n"),
    ]:
        certificate = certificates / f"{name}.crt"
        key = certificates / f"{name}.key"
        if certificate.exists() != key.exists():
            raise RuntimeError(f"Incomplete {name} certificate state.")
        if not certificate.exists():
            csr = certificates / f"{name}.csr"
            ext = certificates / f"{name}.ext"
            write(ext, "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n" + extensions)
            run(["openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes",
                 "-keyout", str(key), "-out", str(csr), "-subj", "/CN=" + subject], capture_output=True)
            run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca),
                 "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(certificate),
                 "-days", "90", "-extfile", str(ext)], capture_output=True)
            csr.unlink()
            ext.unlink()
        run(["openssl", "x509", "-checkend", "86400", "-noout", "-in", str(certificate)], capture_output=True)
    for path in certificates.iterdir():
        path.chmod(0o600)
    public = SOURCE / "public"
    public.mkdir(exist_ok=True)
    shutil.copyfile(ca, public / "gateway-ca.crt")
    shutil.copyfile("/mnt/c/Users/nibeauli/AppData/Local/AdoDevProxy/root.crt", public / "ado-ca.crt")
    shutil.copyfile(SOURCE / "host_bridge.py", STATE / "host_bridge.py")
    config = {
        "listen_address": "10.82.0.2",
        "llm_host": "172.24.160.1",
        "llm_port": 4141,
        "server_certificate": str(certificates / "bridge.crt"),
        "server_key": str(certificates / "bridge.key"),
        "client_ca": str(ca),
        "windows_python": "/mnt/c/ProgramData/Tools/Python/3.12.10/x64/python.exe",
        "windows_relay": r"Q:\tools\kagent-lab\proxy-access\windows-relay.py",
        "windows_agency_relay": r"Q:\tools\kagent-lab\agency-broker\windows_relay.py",
        "gateway_key_command": [
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "-NoProfile", "-NonInteractive", "-Command",
            "[IO.File]::ReadAllText((Join-Path $env:APPDATA 'local-llm-gateway\\gateway-key')).Trim()",
        ],
    }
    write(STATE / "bridge.json", json.dumps(config, indent=2) + "\n")
    unit = f"""[Unit]
Description=Private authenticated Kagent model and ADO host bridge
After=network.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
Environment=WSL_INTEROP=/run/WSL/1_interop
Environment=WSL_DISTRO_NAME=Ubuntu-26.04
ExecStart=/usr/bin/python3 {STATE}/host_bridge.py --config {STATE}/bridge.json
Restart=on-failure
RestartSec=10
UMask=0077
NoNewPrivileges=true
KillMode=control-group

[Install]
WantedBy=multi-user.target
"""
    if UNIT.exists() and "Private authenticated Kagent model and ADO host bridge" not in UNIT.read_text():
        raise RuntimeError("Refusing to replace an unowned service unit.")
    write(UNIT, unit, 0o644)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", UNIT.name])
    run(["systemctl", "restart", UNIT.name])
    run(["systemctl", "is-active", "--quiet", UNIT.name])
    print("Host bridge enabled; TLS keys remain in root-only WSL state.")


def deploy(values):
    namespace = run([*KUBE, "create", "namespace", NAMESPACE, "--dry-run=client", "-o", "json"],
                    capture_output=True).stdout
    run([*KUBE, "apply", "-f", "-"], input=namespace, capture_output=True)
    existing = run([*KUBE, "get", "secret", SECRET, "-n", NAMESPACE,
                    "--ignore-not-found", "-o", "json"], capture_output=True).stdout
    if existing.strip():
        labels = json.loads(existing)["metadata"].get("labels", {})
        if labels.get("app.kubernetes.io/managed-by") != "kagent-proxy-access":
            raise RuntimeError("Refusing to overwrite an unowned TLS Secret.")
    certificates = STATE / "certificates"
    secret = run([
        *KUBE, "create", "secret", "generic", SECRET, "-n", NAMESPACE,
        "--from-file=tls.crt=" + str(certificates / "gateway.crt"),
        "--from-file=tls.key=" + str(certificates / "gateway.key"),
        "--from-file=bridge-client.crt=" + str(certificates / "bridge-client.crt"),
        "--from-file=bridge-client.key=" + str(certificates / "bridge-client.key"),
        "--from-file=ca.crt=" + str(certificates / "ca.crt"),
        "--dry-run=client", "-o", "json",
    ], capture_output=True).stdout
    manifest = json.loads(secret)
    manifest["metadata"]["labels"] = {"app.kubernetes.io/managed-by": "kagent-proxy-access"}
    run([*KUBE, "apply", "-f", "-"], input=json.dumps(manifest).encode(), capture_output=True)
    run([*HELM, "upgrade", "--install", RELEASE, str(SOURCE / "chart"), "-n", NAMESPACE,
         "-f", str(values), "--wait", "--timeout", "180s"])
    run([*KUBE, "rollout", "status", "deployment/" + RELEASE, "-n", NAMESPACE, "--timeout=180s"])


def configure():
    image = json.loads((STATE / "image.json").read_text())
    jwks = json.loads((STATE / "public-jwks.json").read_text())
    registry = json.loads(run([*KUBE, "get", "service", "registry", "-n", "kagent", "-o", "json"],
                             capture_output=True).stdout)["spec"]["clusterIP"]
    repository = registry + ":5000/kagent-lab/proxy-runtime"
    values = {
        "fullnameOverride": RELEASE, "replicaCount": 1,
        "image": {"repository": repository, "digest": image["digest"],
                  "pullPolicy": "Never"},
        "existingTlsSecret": SECRET,
        "tlsNames": {"serverCert": "tls.crt", "serverKey": "tls.key",
                     "clientCert": "bridge-client.crt", "clientKey": "bridge-client.key",
                     "ca": "ca.crt", "tokenSigningKey": ""},
        "bridge": {"host": "10.82.0.2", "servername": "bridge.proxy-access.internal",
                   "modelPort": 15441, "connectPort": 15480},
        "auth": {"issuer": "https://idp.ate-system.svc",
                 "audience": "https://agent-proxies.proxy-access.svc/session",
                 "jwks": jwks, "allowedAtespaces": ["proxy-access"], "actorClaim": "actor.ate.dev"},
        "limits": {"maxConnections": 64, "maxConcurrent": 8, "maxBodyBytes": 30000000,
                   "headersTimeoutMs": 15000, "requestTimeoutMs": 60000,
                   "idleTimeoutMs": 1800000, "maxDurationMs": 1800000},
        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"},
                      "limits": {"cpu": "1", "memory": "512Mi"}},
    }
    broker_ownership = Path("/home/nibeauli/kagent-lab/engsys-agency-coder/owned.json")
    broker_actors = Path("/mnt/q/tools/kagent-lab/agency-broker/public/actors.json")
    if broker_actors.exists():
        values["broker"] = json.loads(broker_actors.read_text())
        values["auth"]["allowedAtespaces"] = sorted({"proxy-access", *(
            actor["subject"].split("/")[1] for actor in values["broker"]["actors"].values())})
    elif broker_ownership.exists():
        values["broker"] = {"actorUID": json.loads(broker_ownership.read_text())["uid"]}
    factory_settings = Path("/home/nibeauli/kagent-lab/factory-coding/proxy-values.json")
    if factory_settings.exists():
        prior = json.loads(factory_settings.read_text())
        for field in ["dynamicActorsFile", "dynamicActorsConfigMap"]:
            values.setdefault("broker", {})[field] = prior["broker"][field]
        values["auth"]["allowedAtespaces"] = sorted(set(values["auth"]["allowedAtespaces"]) | {"factory-executions"})
    write(STATE / "values.json", json.dumps(values, indent=2) + "\n")
    print("Generated non-secret values with pinned public signing keys and immutable image.")


def refresh_keys():
    ca = json.loads(run([
        *KUBE, "get", "clustertrustbundle", "servicedns.podcert.ate.dev:identity:primary-bundle",
        "-o", "json",
    ], capture_output=True).stdout)["spec"]["trustBundle"]
    write(STATE / "public-idp-ca.crt", ca)
    address = json.loads(run([*KUBE, "get", "service", "idp", "-n", "ate-system", "-o", "json"],
                            capture_output=True).stdout)["spec"]["clusterIP"]
    data = run([
        "curl", "--silent", "--show-error", "--fail", "--max-time", "15", "--noproxy", "*",
        "--cacert", str(STATE / "public-idp-ca.crt"),
        "--resolve", "idp.ate-system.svc:443:" + address,
        "https://idp.ate-system.svc/openid/v1/jwks",
    ], capture_output=True).stdout
    jwks = json.loads(data)
    if not jwks.get("keys") or any("d" in key for key in jwks["keys"]):
        raise RuntimeError("Invalid public JWKS response.")
    write(STATE / "public-jwks.json", json.dumps(jwks))
    print("Refreshed public identity keys over verified private TLS.")


def cache_images(hosts=("10.82.0.3", "10.82.0.4")):
    values = json.loads((STATE / "values.json").read_text())
    repository = values["image"]["repository"]
    digest = values["image"]["digest"]
    layout = STATE / "oci"
    manifest = json.loads((layout / "blobs/sha256" / digest.split(":")[1]).read_text())
    index = {"schemaVersion": 2, "manifests": [{
        "mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": digest,
        "size": (layout / "blobs/sha256" / digest.split(":")[1]).stat().st_size,
        "annotations": {"org.opencontainers.image.ref.name": "0.1.0"},
    }]}
    archive = STATE / "runtime.oci.tar"
    with tarfile.open(archive, "w") as output:
        output.add(layout / "oci-layout", arcname="oci-layout")
        data = json.dumps(index).encode()
        info = tarfile.TarInfo("index.json")
        info.size = len(data)
        output.addfile(info, io.BytesIO(data))
        references = [digest, manifest["config"]["digest"]] + [layer["digest"] for layer in manifest["layers"]]
        for reference in references:
            name = "blobs/sha256/" + reference.split(":")[1]
            output.add(layout / name, arcname=name)
    arguments = ["/snap/bin/microk8s", "ctr", "--namespace", "k8s.io", "images", "import",
                 "--base-name", repository, "--digests", "--label", "io.cri-containerd.image=managed", "-"]
    tag_arguments = ["/snap/bin/microk8s", "ctr", "--namespace", "k8s.io", "images", "tag", "--force",
                     repository + ":0.1.0", repository + "@" + digest]
    try:
        with archive.open("rb") as stream:
            run(arguments, stdin=stream)
        run(tag_arguments)
        for host in hosts:
            remote = ["runuser", "-u", "nibeauli", "--", "ssh", "-o", "BatchMode=yes",
                      "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", host, "sudo", "-n"]
            with archive.open("rb") as stream:
                run([*remote, *arguments], stdin=stream)
            run([*remote, *tag_arguments])
    finally:
        archive.unlink()
    print("Pinned OCI image preloaded locally and on selected remote nodes; registry ingress remains unchanged.")


def remove():
    from egress_overlay import remove as remove_overlay
    remove_overlay()
    releases = json.loads(run([*HELM, "list", "-n", NAMESPACE, "-o", "json"], capture_output=True).stdout)
    if any(release["name"] == RELEASE for release in releases):
        run([*HELM, "uninstall", RELEASE, "-n", NAMESPACE, "--wait", "--timeout", "120s"])
    result = run([*KUBE, "get", "secret", SECRET, "-n", NAMESPACE, "--ignore-not-found", "-o", "json"],
                 capture_output=True).stdout
    if result.strip():
        secret = json.loads(result)
        if secret["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") != "kagent-proxy-access":
            raise RuntimeError("Refusing to remove an unowned TLS Secret.")
        run([*KUBE, "delete", "secret", SECRET, "-n", NAMESPACE])
    if UNIT.exists():
        if "Private authenticated Kagent model and ADO host bridge" not in UNIT.read_text():
            raise RuntimeError("Refusing to remove an unowned service unit.")
        run(["systemctl", "disable", "--now", UNIT.name])
        UNIT.unlink()
        run(["systemctl", "daemon-reload"])
    print("Owned Helm release, TLS Secret and host service removed; protected certificate state retained.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare-host", "refresh-keys", "configure", "cache-images", "deploy", "remove"])
    parser.add_argument("--values", type=Path, default=STATE / "values.json")
    args = parser.parse_args()
    if args.action == "prepare-host":
        prepare()
    elif args.action == "deploy":
        deploy(args.values)
    elif args.action == "configure":
        configure()
    elif args.action == "cache-images":
        cache_images()
    elif args.action == "refresh-keys":
        refresh_keys()
    else:
        remove()

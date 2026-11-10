"""Real gVisor worker acceptance; all created runtime resources are owned fixtures."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import argparse
import http.client
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from manage import STATE


ATE = "/home/nibeauli/kagent-lab/artifacts/kubectl-ate"
ENV = dict(os.environ, KUBECONFIG="/var/snap/microk8s/current/credentials/client.config")
SPACE = "proxy-access"
AUDIENCE = "https://agent-proxies.proxy-access.svc/session"
HOST = "agent-proxies.proxy-access.svc.cluster.local"


def ate(*args, json_result=False):
    result = subprocess.run([ATE, "--preview", *args], env=ENV, capture_output=True, text=True, timeout=240)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout) if json_result else result.stdout


@contextmanager
def forward(actor, port):
    path = STATE / f"actor-forward-{port}.log"
    with path.open("w") as log:
        process = subprocess.Popen([ATE, "proxy", "actor", actor, f"{port}:80", "-a", SPACE],
                                   env=ENV, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 180
            for _ in range(150):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Actor readiness timeout.")
                if process.poll() is not None:
                    raise RuntimeError("Actor proxy exited; inspect protected fixture logs.")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=30) as response:
                        if 200 <= response.status < 300:
                            break
                except (OSError, http.client.HTTPException):
                    time.sleep(1)
            else:
                raise RuntimeError("Actor readiness timeout.")
            yield f"http://127.0.0.1:{port}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def request(origin, route):
    try:
        with urllib.request.urlopen(origin + route, timeout=360) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            report = json.load(error)
        except (ValueError, OSError):
            raise RuntimeError(f"Actor request returned HTTP {error.code}") from None
        raise RuntimeError(f"Actor fixture failed: {report}") from None


def template(name, cpu):
    descriptor = json.loads((STATE / "image.json").read_text())
    env = {
        "SESSION_URL": f"https://{HOST}:8443/session",
        "DATA_URL": f"https://{HOST}:8444",
    }
    env.update({
        "HTTP_PROXY": "http://127.0.0.1:15480",
        "HTTPS_PROXY": "http://127.0.0.1:15480",
        "http_proxy": "http://127.0.0.1:15480",
        "https_proxy": "http://127.0.0.1:15480",
        "NO_PROXY": "localhost,127.0.0.1," + HOST,
        "no_proxy": "localhost,127.0.0.1," + HOST,
        "NODE_EXTRA_CA_CERTS": "/opt/proxy/ado-ca.crt",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "DOTNET_CLI_HOME": "/tmp/dotnet-home",
        "HOME": "/tmp/proxy-home",
        "XDG_DATA_HOME": "/tmp/xdg-data",
        "NUGET_HTTP_CACHE_PATH": "/tmp/nuget-http-cache",
        "NUGET_SCRATCH": "/tmp/nuget-scratch",
        "GATEWAY_SESSION_URL": f"https://{HOST}:8443/session",
        "GATEWAY_CA_FILE": "/opt/proxy/gateway-ca.crt",
        "EGRESS_CA_FILE": "/run/ate/trust/trust-bundle.pem",
    })
    return {
        "metadata": {"atespace": SPACE, "name": name},
        "workerSelector": {"matchLabels": {
            "kagent.dev/worker-pool-name": "kagent-default",
            "kagent.dev/worker-pool-namespace": "kagent",
        }},
        "containers": [{
            "name": "probe",
            "image": "localhost:15000/kagent-lab/proxy-runtime@" + descriptor["digest"],
            "command": [
                "/usr/local/bin/node", "-e",
                "require('node:fs').mkdirSync(process.env.HOME,{recursive:true,mode:448});"
                "require('/opt/proxy/actor_probe.js');",
            ],
            "env": [{"name": key, "value": value} for key, value in env.items()],
            "wakeupProbe": {"httpGet": {"path": "/readyz", "port": 80}},
            "volumeMounts": [{"name": "trust", "mountPath": "/run/ate/trust"},
                             {"name": "tmp", "mountPath": "/tmp"}],
        }],
        "volumes": [{"name": "trust", "systemInfo": {"dataSources": [{
            "trustBundle": {"names": ["egress-mitm.ate.dev", "system-roots.ate.dev"],
                            "path": "trust-bundle.pem"},
        }]}}, {"name": "tmp", "durableDir": {}}],
        "resources": {"limits": {"cpuMillicores": cpu, "memoryBytes": 1073741824}},
        "snapshotConfig": {"preferredFidelity": "SNAPSHOT_FIDELITY_VOLUMES",
                           "storageLocation": "gs://ate-snapshots/proxy-access/",
                           "goldenSnapshotConfig": {"mode": "GOLDEN_SNAPSHOT_MODE_DISABLED"}},
    }


def policy(actor, audience):
    document = {"rules": [
        {"https": {"hostnames": [HOST], "ports": {"numbers": [8443]},
                   "effects": {"replaceHeaders": [{
                       "header": "Authorization", "prefix": "Bearer ",
                       "actorJwt": {"audiences": [audience], "expirationSeconds": 300},
                   }]}}},
        {"tlsPassthrough": {"hostnames": [HOST], "ports": {"numbers": [8444]}}},
    ]}
    path = STATE / f"{actor}-egress.json"
    path.write_text(json.dumps(document))
    ate("create", "egress-policy", actor, "-a", SPACE, "-f", str(path))


def main(live=False):
    suffix = uuid.uuid4().hex[:10]
    names = [f"proxy-proof-{suffix}-{i}" for i in range(3)]
    normal = f"proxy-proof-{suffix}"
    denied = f"proxy-denied-{suffix}"
    templates = []
    actors = []
    try:
        try:
            ate("get", "atespace", SPACE)
        except RuntimeError as error:
            if "notfound" not in str(error).lower() and "not found" not in str(error).lower():
                raise
            ate("create", "atespace", SPACE)
        for name, cpu in [(normal, 4000), (denied, 1000)]:
            path = STATE / f"{name}.json"
            path.write_text(json.dumps(template(name, cpu)))
            ate("create", "actor-template", "-f", str(path))
            templates.append(name)
        for name in names:
            ate("create", "actor", name, "-a", SPACE, "--template", normal)
            actors.append(name)
            policy(name, AUDIENCE)

        def check(index):
            path = STATE / f"{names[index]}-result.json"
            try:
                with forward(names[index], 18141 + index) as origin:
                    result = request(origin, "/run?live=1" if index == 0 and live else "/run")
            except RuntimeError as error:
                path.write_text(json.dumps({"result": "FAIL", "error": str(error)}, indent=2))
                raise
            assert result["result"] == "PASS", result
            result["node"] = ate("get", "actor", names[index], "-a", SPACE, "-o", "json",
                                 json_result=True)["status"]["assignedNode"]
            path.write_text(json.dumps(result, indent=2))
            return result

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(check, range(3)))
        assert len({result["node"] for result in results}) == 3, results
        ate("suspend", "actor", names[0], "-a", SPACE, "--mode", "sync")
        with forward(names[0], 18144) as origin:
            restored = request(origin, "/model-check")
        assert restored["boot_id"] != results[0]["boot_id"], restored
        assert restored["models"] == results[0]["models"], restored
        for name in list(actors):
            ate("delete", "actor", name, "-a", SPACE)
            actors.remove(name)
        reject = f"proxy-wrong-audience-{suffix}"
        ate("create", "actor", reject, "-a", SPACE, "--template", denied)
        actors.append(reject)
        policy(reject, "https://not-authorized.invalid")
        with forward(reject, 18145) as origin:
            rejection = request(origin, "/identity-check")
        assert rejection["session_status"] == 401, rejection
        report = {"result": "PASS", "workers": results, "cold_restore": restored,
                  "wrong_audience": rejection}
        (STATE / "actor-acceptance.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    finally:
        failures = []
        for name in actors:
            try:
                ate("delete", "actor", name, "-a", SPACE)
            except RuntimeError as error:
                failures.append(str(error))
        for name in templates:
            try:
                ate("delete", "actor-template", name, "-a", SPACE)
            except RuntimeError as error:
                failures.append(str(error))
        if failures:
            raise RuntimeError("Owned fixture cleanup failed: " + "; ".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-inference", action="store_true")
    main(parser.parse_args().live_inference)

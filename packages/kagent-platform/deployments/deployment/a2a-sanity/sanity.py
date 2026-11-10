"""Finite native A2A acceptance: public SDK coding, then Sol/Argon MCP checks."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

from build import STATE as BUILD, build, fingerprint
from resources import MODELS, OWNER, SPACE, documents, prefix, resource

STATE = Path("/home/nibeauli/kagent-lab/a2a-sanity")
LAB = Path("/mnt/q/tools/kagent-lab")
KUBE = ["/snap/bin/microk8s", "kubectl", "--request-timeout=30s"]
CLIENT = Path("/home/nibeauli/kagent-lab/factory-coding/client")
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}


class SessionError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__("Native session operation failed: " + code)


def save(state):
    path = STATE / state["runId"] / "state.json"
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(state, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def current(run_id=None):
    if run_id is None:
        run_id = json.loads((STATE / "current.json").read_text())["runId"]
    run_id = str(uuid.UUID(run_id))
    return json.loads((STATE / run_id / "state.json").read_text())


def kube(*args, body=None):
    result = subprocess.run([*KUBE, *args], input=None if body is None else json.dumps(body).encode(),
                            capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError("Kubernetes operation failed; no inference was replayed.")
    return json.loads(result.stdout) if result.stdout.strip() else None


def existing(document, state, required=False):
    value = kube("get", document["kind"], document["metadata"]["name"], "-n", document["metadata"]["namespace"],
                 "--ignore-not-found", "-o", "json")
    if value:
        metadata = value["metadata"]
        if (metadata.get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER
                or metadata.get("labels", {}).get("sanity.host-capabilities/run") != state["runId"]):
            raise RuntimeError("Refusing to touch an unowned fixture resource.")
        expected = state.get("uids", {}).get(document["kind"] + "/" + metadata["name"])
        if expected and metadata["uid"] != expected:
            raise RuntimeError("Fixture resource was replaced; refusing to touch it.")
    elif required:
        raise RuntimeError("Fixture resource is absent.")
    return value


def catalog(namespace=SPACE):
    bridge = json.loads(Path("/home/nibeauli/kagent-lab/proxy-access/bridge.json").read_text())
    secret_name = "baseline-llm" if namespace == "baseline" else "cluster-management-llm"
    secret = kube("get", "secret", secret_name, "-n", namespace, "-o", "json")
    key = base64.b64decode(secret["data"]["gateway-key"]).decode()
    request = urllib.request.Request(f"http://{bridge['llm_host']}:{bridge.get('llm_port', 4141)}/v1/models",
                                     headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(request, timeout=30) as response:
        values = {item["id"]: item for item in json.load(response)["data"]}
    for model, wire in MODELS.values():
        endpoint = "/responses" if wire == "responses" else "/chat/completions"
        if model not in values or endpoint not in values[model].get("supported_endpoints", []):
            raise RuntimeError("Selected fixture model/wire format is not advertised: " + model)


def setup(apply=False, live=False, run_id=None, namespace="baseline"):
    if not apply:
        return {"dryRun": True, "models": MODELS, "namespace": namespace,
                "actions": ["build/cache private fixture", "create owned native agents", "no SendMessage"]}
    if os.geteuid() != 0:
        raise RuntimeError("Run as primary WSL root.")
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    pointer = STATE / "current.json"
    if pointer.exists():
        prior = current()
        if prior["phase"] != "cleaned":
            if run_id and prior["runId"] != str(uuid.UUID(run_id)):
                raise RuntimeError("Remove the current fixture before creating another.")
            if prior.get("namespace", SPACE) != namespace:
                raise RuntimeError("The current fixture belongs to another namespace.")
            if prior["live"] != live:
                raise RuntimeError("Existing fixture live gate differs; remove and set up a fresh fixture.")
            return prior
    catalog(namespace)
    previous = json.loads((BUILD / "image.json").read_text()) if (BUILD / "image.json").exists() else {}
    if previous.get("sourceDigest") != fingerprint() or not (BUILD / "values.json").exists():
        build(publish=True)
    values = json.loads((BUILD / "values.json").read_text())["image"]
    image = values["repository"] + "@" + values["digest"]
    state = {"runId": str(uuid.UUID(run_id)) if run_id else str(uuid.uuid4()),
             "phase": "setting-up", "namespace": namespace, "live": live, "image": image, "sourceDigest": fingerprint(),
             "models": {role: {"id": model, "apiFormat": wire} for role, (model, wire) in MODELS.items()},
             "tasks": {}, "sessions": {}, "uids": {}, "results": {}}
    docs = documents(state["runId"], image, live, namespace)
    for document in docs:
        existing(document, state)
    save(state)
    pointer.write_text(json.dumps({"runId": state["runId"]}))
    try:
        payload = {"apiVersion": "v1", "kind": "List", "items": docs}
        kube("apply", "--dry-run=server", "-f", "-", "-o", "json", body=payload)
        kube("apply", "-f", "-", "-o", "json", body=payload)
    finally:
        for document in docs:
            value = existing(document, state)
            if value:
                state["uids"][document["kind"] + "/" + document["metadata"]["name"]] = value["metadata"]["uid"]
        save(state)
    for role in MODELS:
        wait_ready(prefix(state["runId"], namespace) + "-" + role, namespace)
    state["phase"] = "ready"
    save(state)
    return state


def wait_ready(name, namespace=SPACE):
    result = subprocess.run([*KUBE, "wait", "agents.api.kagent.dev/" + name, "-n", namespace,
                             "--for=condition=Ready", "--timeout=240s"], capture_output=True, timeout=260)
    if result.returncode:
        raise RuntimeError("Fixture Agent is not Ready: " + name)


def session_rpc(origin, token_file, method, identity, agent, namespace=SPACE):
    result = subprocess.run([str(CLIENT), origin.removeprefix("http://"), str(token_file),
                             method, identity, agent, namespace], capture_output=True, timeout=210)
    if result.returncode:
        match = re.search(rb"code = ([A-Za-z]+)", result.stderr)
        code = match.group(1).decode() if match else "Unknown"
        if method == "get" and code == "NotFound":
            return None
        raise SessionError(code)
    if method == "delete":
        return None
    session = json.loads(result.stdout)["session"]
    owner = session.get("agent", {}).get("ref", {})
    if owner.get("name") != agent or owner.get("namespace") != namespace:
        raise RuntimeError("Native session provenance mismatch.")
    return session["id"]


class A2A:
    def __init__(self, origin, token_file, namespace=SPACE):
        self.origin, self.token_file, self.namespace = origin, token_file, namespace

    def rpc(self, agent, method, params):
        request = urllib.request.Request(self.origin + "/agents/" + self.namespace + "/" + agent,
            data=json.dumps({"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.token_file.read_text().strip()})
        with urllib.request.urlopen(request, timeout=240) as response:
            result = json.load(response)
        if "error" in result:
            raise RuntimeError("Native A2A operation failed: " + method)
        return result["result"].get("task", result["result"])


def texts(task):
    result = [part["text"] for artifact in task.get("artifacts", [])
              for part in artifact.get("parts", []) if isinstance(part.get("text"), str)]
    result.extend(part["text"] for part in task.get("status", {}).get("message", {}).get("parts", [])
                  if isinstance(part.get("text"), str))
    for message in reversed(task.get("history", [])):
        if message.get("role") in {"ROLE_AGENT", "agent"}:
            result.extend(part["text"] for part in message.get("parts", []) if isinstance(part.get("text"), str))
    return result


def decoded(task):
    for text in texts(task):
        text = text.strip()
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        try:
            value = json.loads(text)
            if isinstance(value, dict) and isinstance(value.get("detail"), str):
                value = json.loads(value["detail"])
            if isinstance(value, dict):
                yield value
        except ValueError:
            continue


def check_coder(task):
    for value in decoded(task):
        result = value.get("result", {})
        if (value.get("publicDataOnly") and value.get("status") == "completed"
                and result.get("testsPassed") == 7 and isinstance(result.get("source"), str)
                and hashlib.sha256(result["source"].encode()).hexdigest() == result.get("sourceSha256")
                and "repo_write" in value.get("tools", []) and "fixture_test" in value.get("tools", [])):
            return {**result, "observedTools": value["tools"], "sdkSessionId": value.get("sdkSessionId")}
    raise RuntimeError("The A2A coding artifact did not prove edits and seven fixture tests.")


def check_review(task, expected_nonce, source_sha):
    for value in decoded(task):
        if (value.get("verdict") == "pass" and value.get("nonce") == expected_nonce
                and value.get("sourceSha256") == source_sha):
            return {"verdict": "pass", "sourceSha256": source_sha, "mcpChallengeVerified": True}
    raise RuntimeError("Reviewer did not prove the fresh MCP challenge and same coding artifact.")


def wait_actor_absent(session, namespace=SPACE):
    name = "session-" + str(uuid.UUID(session))
    environment = dict(os.environ, KUBECONFIG="/var/snap/microk8s/current/credentials/client.config")
    deadline = time.monotonic() + 90
    while True:
        result = subprocess.run(["/home/nibeauli/kagent-lab/artifacts/kubectl-ate", "--preview", "get",
                                 "actor", name, "-a", namespace, "-o", "json"],
                                env=environment, capture_output=True, timeout=60)
        if result.returncode:
            error = result.stderr.decode(errors="replace").lower()
            if "notfound" in error or "not found" in error:
                return
            raise RuntimeError("Native actor absence could not be verified.")
        if time.monotonic() > deadline:
            raise RuntimeError("Owned native actor is still finalizing; cleanup retained for retry.")
        time.sleep(2)


def role_task(state, client, role, prompt):
    namespace = state.get("namespace", SPACE)
    agent = prefix(state["runId"], namespace) + "-" + role
    if role not in state["sessions"]:
        request_id = str(uuid.uuid5(uuid.UUID(state["runId"]), "session:" + role))
        state["sessions"][role] = session_rpc(client.origin, client.token_file, "create", request_id, agent, namespace)
        save(state)
    session = state["sessions"][role]
    if session_rpc(client.origin, client.token_file, "get", session, agent, namespace) != session:
        raise RuntimeError("Session changed.")
    entry = state["tasks"].setdefault(role, {"messageId": str(uuid.uuid5(uuid.UUID(state["runId"]), "message:" + role)),
                                          "attempted": False})
    if entry["attempted"] and not entry.get("taskId"):
        listing = client.rpc(agent, "ListTasks", {"contextId": session, "pageSize": 100})
        matches = [task for task in listing.get("tasks", [])
                   if any(message.get("messageId") == entry["messageId"] for message in task.get("history", []))]
        if len(matches) != 1:
            raise RuntimeError("Uncertain A2A delivery; retained without automatic replay.")
        entry["taskId"] = matches[0]["id"]
        save(state)
    if not entry["attempted"]:
        entry["attempted"] = True
        save(state)
        message = {"messageId": entry["messageId"], "contextId": session, "role": "ROLE_USER",
                   "parts": [{"text": json.dumps({"prompt": prompt, "allowEdits": role == "coder"})}
                             if role == "coder" else {"text": prompt}]}
        task = client.rpc(agent, "SendMessage", {"message": message,
                                               "configuration": {"returnImmediately": True}})
        entry["taskId"] = task["id"]
        save(state)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        task = client.rpc(agent, "GetTask", {"id": entry["taskId"]})
        status = task.get("status", {}).get("state")
        entry["state"] = status
        save(state)
        if status in TERMINAL:
            if status != "TASK_STATE_COMPLETED":
                raise RuntimeError("Fixture role ended without success: " + role)
            return task
        time.sleep(2)
    raise RuntimeError("Fixture task deadline; observe or remove, never replay.")


def run(state, client, live=False):
    if not live or not state["live"]:
        raise RuntimeError("Explicit --live and a prospectively live setup are required.")
    if state["phase"] == "completed":
        return state
    if state["phase"] == "cleaned":
        raise RuntimeError("Set up a fresh run after cleanup.")
    if state["sourceDigest"] != fingerprint():
        raise RuntimeError("Fixture source changed; do not repoint existing sessions.")
    namespace = state.get("namespace", SPACE)
    for document in documents(state["runId"], state["image"], state["live"], namespace):
        value = existing(document, state, required=True)
        if document["kind"] == "ModelConfig":
            expected, actual = document["spec"], value["spec"]
            if (any(actual.get(key) != expected[key]
                    for key in ["model", "provider", "apiKeySecret", "apiKeySecretKey"])
                    or any(actual["openAI"].get(key) != expected["openAI"][key]
                           for key in ["baseUrl", "apiFormat"])):
                raise RuntimeError("Configured fixture model, credential binding or wire format changed.")
    state["phase"] = "running"
    save(state)
    try:
        if "coder" not in state["results"]:
            state["results"]["coder"] = check_coder(role_task(state, client, "coder", "sanity-code"))
            save(state)
        code = state["results"]["coder"]
        for role in ["sol", "argon"]:
            if role in state["results"]:
                continue
            name = prefix(state["runId"], namespace) + "-" + role + "-challenge"
            entry = state["tasks"].get(role, {})
            nonce = entry.get("nonce") or secrets.token_hex(16)
            state["tasks"].setdefault(role, {})["nonce"] = nonce
            # Keep nonce creation independent of the inference delivery checkpoint.
            if "messageId" not in state["tasks"][role]:
                state["tasks"][role].update(messageId=str(uuid.uuid5(uuid.UUID(state["runId"]), "message:" + role)),
                                           attempted=False)
            challenge = resource("ConfigMap", name, state["runId"],
                                 data={"nonce": nonce, "sourceSha256": code["sourceSha256"]}, namespace=namespace)
            existing(challenge, state, required=True)
            kube("apply", "-f", "-", "-o", "json", body=challenge)
            save(state)
            prompt = ("Review this public sum fixture: empty arrays yield zero; all elements must be finite numbers, "
                      "otherwise TypeError. Fetch ConfigMap " + name + " in namespace " + namespace +
                      " using k8s_get_resource_yaml and return ONLY JSON verdict, nonce and sourceSha256. "
                      "The nonce is intentionally not in this message. Public artifact received from the coding "
                      "agent through A2A: " + json.dumps(code))
            state["results"][role] = check_review(role_task(state, client, role, prompt), nonce, code["sourceSha256"])
            state["results"][role].update(model=MODELS[role][0], apiFormat=MODELS[role][1])
            save(state)
        state["phase"], state["passed"] = "completed", True
        save(state)
        return state
    except Exception:
        state["phase"] = "failed"
        state["passed"] = False
        save(state)
        raise


def remove(state, client, apply=False):
    if not apply:
        return {"dryRun": True, "runId": state["runId"], "sessions": len(state["sessions"]),
                "resources": len(state["uids"])}
    if state["phase"] == "cleaned":
        return state
    namespace = state.get("namespace", SPACE)
    deleted = state.setdefault("deletedSessions", [])
    for role, session in state["sessions"].items():
        if session in deleted:
            continue
        agent = prefix(state["runId"], namespace) + "-" + role
        if session_rpc(client.origin, client.token_file, "get", session, agent, namespace) is not None:
            for attempt in range(12):
                try:
                    session_rpc(client.origin, client.token_file, "delete", session, agent, namespace)
                    break
                except SessionError as error:
                    if error.code == "NotFound":
                        break
                    if error.code != "FailedPrecondition" or attempt == 11:
                        raise
                    time.sleep(2)
            deadline = time.monotonic() + 60
            while session_rpc(client.origin, client.token_file, "get", session, agent, namespace) is not None:
                if time.monotonic() > deadline:
                    raise RuntimeError("Session cleanup is still pending.")
                time.sleep(2)
        deleted.append(session)
        save(state)
    for session in deleted:
        wait_actor_absent(session, namespace)
    docs = documents(state["runId"], state["image"], state["live"], namespace)
    order = {"Agent": 0, "AgentTemplate": 1, "Harness": 2, "ModelConfig": 3, "RemoteMCPServer": 4, "ConfigMap": 5}
    for document in sorted(docs, key=lambda value: order[value["kind"]]):
        value = existing(document, state)
        if not value:
            continue
        plural = {"Agent": "agents", "AgentTemplate": "agenttemplates", "Harness": "harnesses",
                  "ModelConfig": "modelconfigs", "RemoteMCPServer": "remotemcpservers", "ConfigMap": "configmaps"}[value["kind"]]
        base = "/api/v1" if value["kind"] == "ConfigMap" else "/apis/api.kagent.dev/v1alpha3"
        kube("delete", "--raw", f"{base}/namespaces/{namespace}/{plural}/{value['metadata']['name']}", "-f", "-",
             body={"apiVersion": "v1", "kind": "DeleteOptions",
                   "preconditions": {"uid": value["metadata"]["uid"]}})
        if document["kind"] == "Agent":
            deadline = time.monotonic() + 180
            while existing(document, state):
                if time.monotonic() > deadline:
                    raise RuntimeError("Agent is still finalizing; dependencies retained for cleanup retry.")
                time.sleep(2)
    deadline = time.monotonic() + 180
    while any(existing(document, state) for document in docs):
        if time.monotonic() > deadline:
            raise RuntimeError("Owned resources are still finalizing; cleanup retained for retry.")
        time.sleep(2)
    state["phase"] = "cleaned"
    state["cleanup"] = {"resourcesRemoved": len(state["uids"]), "sessionsAbsent": len(deleted),
                        "nativeSessionActorsAbsent": len(deleted),
                        "retained": "private receipt/image caches; native snapshot and telemetry retention"}
    save(state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "run", "status", "remove", "check"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--namespace", choices=["baseline", SPACE], default="baseline")
    args = parser.parse_args()
    if args.action in {"status", "remove"} and not args.run_id and not (STATE / "current.json").exists():
        print(json.dumps({"phase": "absent", "dryRun": not args.apply}))
        return
    operation_lock = None
    if args.action != "status" and (args.apply or args.action == "run"):
        import fcntl
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        operation_lock = (STATE / "operation.lock").open("a")
        try:
            fcntl.flock(operation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another fixture operation is running; use Status to observe it.") from None
    if args.action == "setup":
        state = setup(args.apply, args.live, args.run_id, args.namespace)
    elif args.action == "status":
        state = current(args.run_id)
    elif args.action == "check" and not args.apply:
        state = setup(False, args.live, namespace=args.namespace)
    else:
        if args.action == "check" and not args.live:
            raise RuntimeError("Check requires --apply --live.")
        setup_error = None
        if args.action == "check":
            wanted = str(uuid.UUID(args.run_id)) if args.run_id else str(uuid.uuid4())
            try:
                state = setup(True, True, wanted, args.namespace)
            except Exception as error:
                if not (STATE / wanted / "state.json").exists():
                    raise
                state = current(wanted)
                state["passed"] = False
                save(state)
                setup_error = error
        else:
            state = current(args.run_id)
        token = Path(json.loads(Path("/home/nibeauli/kagent-lab/factory-coding/service.json").read_text())["controllerToken"])
        sys.path.insert(0, str(LAB))
        from lab_transport import port_forward
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with port_forward(SPACE, "factory-controller", port, 8083) as origin:
            client = A2A(origin, token, state.get("namespace", SPACE))
            if args.action == "remove":
                state = remove(state, client, args.apply)
            elif args.action == "check":
                try:
                    if setup_error:
                        raise setup_error
                    run(state, client, True)
                finally:
                    remove(state, client, True)
            else:
                state = run(state, client, args.live)
    summary = {key: state[key] for key in ["dryRun", "runId", "namespace", "phase", "passed", "models", "actions", "cleanup"]
               if key in state}
    if "runId" in state and not state.get("dryRun"):
        summary["report"] = str(STATE / state["runId"] / "state.json")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

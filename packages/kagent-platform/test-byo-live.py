import json
import subprocess
import time
import urllib.request
import uuid
import yaml

from lab_transport import BASE, ENV, port_forward

REPORT = BASE / "protected/byo-acceptance.json"
ATE = str(BASE / "artifacts/kubectl-ate")


class Lab:
    def __init__(self, origin, report=REPORT):
        self.origin = origin
        self.report = report
        self.sessions = []
        self.checkpoints = []
        self.results = []

    def rpc(self, service, method, body):
        result = subprocess.run([
            str(BASE / "bin/grpcurl"), "-plaintext", "-d", json.dumps(body),
            self.origin.removeprefix("http://"),
            f"kagent.api.v1.{service}/{method}",
        ], text=True, capture_output=True, timeout=240)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return json.loads(result.stdout)

    def create(self):
        session = self.rpc("SessionService", "CreateSession", {
            "agent": {"namespace": "kagent", "name": "deterministic-byo"},
            "requestId": str(uuid.uuid4()),
        })["session"]
        self.sessions.append(session["id"])
        return session["id"]

    def a2a(self, method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()),
                "method": method, "params": params}
        for attempt in range(20):
            request = urllib.request.Request(
                self.origin + "/agents/kagent/deterministic-byo",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=240) as response:
                result = json.load(response)
            if "error" not in result:
                return result["result"]
            error = result["error"]
            retryable = any(
                info.get("metadata", {}).get("reason") == "KAGENT_SEND_NOT_ACCEPTED"
                for info in error.get("data", []) if isinstance(info, dict)
            )
            if not retryable:
                raise RuntimeError(json.dumps(result))
            time.sleep(1)
        raise RuntimeError(json.dumps(result))

    def send(self, session, command, task=None, immediate=False):
        message = {"messageId": str(uuid.uuid4()), "contextId": session,
                   "role": "ROLE_USER", "parts": [{"text": command}]}
        if task:
            message["taskId"] = task
        params = {"message": message}
        if immediate:
            params["configuration"] = {"returnImmediately": True}
        value = self.a2a("SendMessage", params)
        value = value.get("task", value)
        self.results.append({"session": session, "command": command, "task": value})
        self.save()
        return value

    def idle(self, session):
        for attempt in range(120):
            actor = json.loads(subprocess.check_output([
                ATE, "get", "actor", f"session-{session}", "-a", "kagent", "-o", "json",
            ], text=True, stderr=subprocess.DEVNULL, env=ENV, timeout=30))
            status = actor["status"]
            if status["state"] == "ACTOR_STATE_SUSPENDED":
                assert any(
                    s.get("durableSnapshot", {}).get("state") == "SNAPSHOT_STORAGE_STATE_COMPLETED"
                    and s.get("fidelity") == "SNAPSHOT_FIDELITY_VOLUMES"
                    for s in status.get("snapshots", [])
                ), status
                return status
            time.sleep(1)
        raise RuntimeError(f"Session {session} did not reach a durable idle snapshot")

    def save(self, **extra):
        if self.report is None:
            return
        self.report.write_text(json.dumps({
            "sessions": self.sessions, "checkpoints": self.checkpoints,
            "results": self.results, **extra,
        }, indent=2))
        self.report.chmod(0o600)


def native(task):
    values = []
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "text" and isinstance(item, str):
                    try:
                        values.append(json.loads(item))
                    except json.JSONDecodeError:
                        pass
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(task.get("artifacts", []))
    if not values:
        visit(task)
    return next(value for value in reversed(values) if isinstance(value, dict) and "counter" in value)


def complete(task):
    assert task["status"]["state"] == "TASK_STATE_COMPLETED", task
    return native(task)


if __name__ == "__main__":
    expected_image = next(
        document["spec"]["workload"]["image"]
        for document in yaml.safe_load_all(open("/mnt/q/tools/kagent-lab/byo-agent.yaml"))
        if document["kind"] == "Harness"
    )
    for attempt in range(90):
        agent = json.loads(subprocess.check_output([
            "/snap/bin/microk8s", "kubectl", "get", "agent", "deterministic-byo",
            "-n", "kagent", "-o", "json",
        ], env=ENV))
        status = agent.get("status", {})
        if status.get("workloadImage") == expected_image and \
                status.get("desiredRevision") == status.get("latestSuccessfulRevision"):
            break
        time.sleep(1)
    else:
        raise RuntimeError("The intended immutable agent revision was not prepared")
    with port_forward("kagent", "kagent-controller", 18083, 8083) as origin:
        lab = Lab(origin)
        try:
            first, second = lab.create(), lab.create()
            a = complete(lab.send(first, "increment"))
            b = complete(lab.send(second, "status"))
            assert a["counter"] == 1 and b["counter"] == 0, (a, b)
            complete(lab.send(first, "write:proof.txt:source-workspace"))
            before = complete(lab.send(first, "status"))
            idle = lab.idle(first)
            after = complete(lab.send(first, "read:proof.txt"))
            assert after["instance_id"] != before["instance_id"], (before, after)
            assert after["instance_reason"] == "cold-boot" and after["counter"] == 1, after
            assert after["session_id"] == first and after["context_id"] == first, after
            assert "source-workspace" in json.dumps(after), after
            assert "source-workspace" not in json.dumps(complete(lab.send(second, "status")))
            for command, continuation, state in [
                ("request-input", "answer:restored-input", "TASK_STATE_INPUT_REQUIRED"),
                ("request-approval", "approve", "TASK_STATE_INPUT_REQUIRED"),
            ]:
                parked = lab.send(first, command)
                assert parked["status"]["state"] == state, parked
                assert native(parked)["parked"]["kind"] == (
                    "approval" if command == "request-approval" else "input"
                )
                lab.idle(first)
                resumed = lab.send(first, continuation, parked["id"])
                complete(resumed)
                assert resumed["id"] == parked["id"], (parked, resumed)
            head = lab.send(first, "status")
            complete(head)
            lab.idle(first)
            checkpoint = lab.rpc("CheckpointService", "CreateCheckpoint", {
                "sessionId": first, "requestId": str(uuid.uuid4()),
                "expectedHeadTaskId": head["id"],
            })["checkpoint"]
            lab.checkpoints.append(checkpoint["id"])
            for attempt in range(120):
                checkpoint = lab.rpc("CheckpointService", "GetCheckpoint", {
                    "checkpointId": checkpoint["id"],
                })["checkpoint"]
                if checkpoint["state"] == "CHECKPOINT_STATE_READY":
                    break
                assert checkpoint["state"] != "CHECKPOINT_STATE_FAILED", checkpoint
                time.sleep(1)
            assert checkpoint["state"] == "CHECKPOINT_STATE_READY", checkpoint
            fork = lab.rpc("CheckpointService", "ForkSession", {
                "checkpointId": checkpoint["id"], "requestId": str(uuid.uuid4()),
            })["session"]["id"]
            lab.sessions.append(fork)
            assert fork != first
            copied = complete(lab.send(fork, "read:proof.txt"))
            assert "source-workspace" in json.dumps(copied), copied
            complete(lab.send(fork, "write:proof.txt:fork-only"))
            original = complete(lab.send(first, "read:proof.txt"))
            assert "source-workspace" in json.dumps(original) and "fork-only" not in json.dumps(original)
            stored = lab.a2a("GetTask", {"id": head["id"]})
            assert stored.get("task", stored)["id"] == head["id"]
            child = lab.send(second, "slow-child", immediate=True)
            canceled = lab.a2a("CancelTask", {"id": child["id"]})
            canceled = canceled.get("task", canceled)
            assert canceled["status"]["state"] == "TASK_STATE_CANCELED", canceled
            cleaned = complete(lab.send(second, "status"))
            assert "reaped" in cleaned.get("marker", ""), cleaned
            abandoned = lab.send(second, "uncertain-effect")
            assert abandoned["status"]["state"] == "TASK_STATE_FAILED", abandoned
            assert native(abandoned)["marker"] == "uncertain-effect-abandoned: never replayed"
            for session in lab.sessions:
                lab.idle(session)
            lab.save(result="PASS", cold_restore_owner=idle["assignedNode"])
            print(json.dumps({"result": "PASS", "report": str(REPORT)}))
        except BaseException:
            lab.save(result="FAIL")
            raise

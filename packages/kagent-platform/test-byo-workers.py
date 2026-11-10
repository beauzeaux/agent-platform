from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import subprocess
import threading
import time

from lab_transport import BASE, ENV, port_forward

spec = importlib.util.spec_from_file_location("live", "/mnt/q/tools/kagent-lab/test-byo-live.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


if __name__ == "__main__":
    path = BASE / "protected/byo-workers-report.json"
    with port_forward("kagent", "kagent-controller", 18083, 8083) as origin:
        lab = live.Lab(origin, report=None)
        sessions, tasks, owners = [], {}, {}
        lock = threading.Lock()

        def start(session):
            client = live.Lab(origin, report=None)
            task = client.send(session, "slow-child", immediate=True)
            with lock:
                tasks[session] = task
            for attempt in range(30):
                current = client.a2a("GetTask", {"id": task["id"]})
                current = current.get("task", current)
                if current["status"]["state"] == "TASK_STATE_WORKING":
                    return task
                assert current["status"]["state"] == "TASK_STATE_SUBMITTED", current
                time.sleep(1)
            raise RuntimeError(f"Task {task['id']} never executed")

        def cancel(session):
            client = live.Lab(origin, report=None)
            canceled = client.a2a("CancelTask", {"id": tasks[session]["id"]})
            canceled = canceled.get("task", canceled)
            assert canceled["status"]["state"] == "TASK_STATE_CANCELED", canceled

        def verify(session):
            client = live.Lab(origin, report=None)
            status = live.complete(client.send(session, "status"))
            assert "reaped" in status.get("marker", ""), status
            client.idle(session)

        try:
            for index in range(9):
                sessions.append(lab.create())
            with ThreadPoolExecutor(max_workers=9) as pool:
                list(pool.map(start, sessions))
            for session in sessions:
                actor = json.loads(subprocess.check_output([
                    str(BASE / "artifacts/kubectl-ate"), "get", "actor",
                    f"session-{session}", "-a", "kagent", "-o", "json",
                ], env=ENV, text=True, stderr=subprocess.DEVNULL, timeout=30))
                owners[session] = actor["status"]["assignedNode"]
            assert set(owners.values()) == {
                "cpc-nibea-8t76e", "cpc-nibea-2l35i", "cpc-nibea-4memo",
            }, owners
            with ThreadPoolExecutor(max_workers=9) as pool:
                list(pool.map(cancel, sessions))
                list(pool.map(verify, sessions))
            path.write_text(json.dumps({"result": "PASS", "owners": owners}, indent=2))
            path.chmod(0o600)
            print(json.dumps({"result": "PASS", "execution_hosts": sorted(set(owners.values()))}))
        finally:
            # Only these explicitly created test contexts are removed.
            for session in sessions:
                if session in tasks:
                    try:
                        lab.a2a("CancelTask", {"id": tasks[session]["id"]})
                    except Exception:
                        pass
                lab.rpc("SessionService", "DeleteSession", {"sessionId": session})

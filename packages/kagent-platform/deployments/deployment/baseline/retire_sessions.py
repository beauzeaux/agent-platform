"""Bounded retirement of the exact legacy fleet; never sends or replays work.

Run under the lab's WSL operator environment. The default is a metadata-only dry
run; --apply attempts each verified session once and retains protected receipts.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest


RETIRED = {
    "factory-executions": (
        "factory-coder", "slopmop-ado-triage", "slopmop-documentation-steward",
        "slopmop-implementation-reviewer", "slopmop-opportunity-scout",
        "slopmop-plan-reviewer", "slopmop-planner", "slopmop-pr-pattern-miner",
        "public-native-canary-a", "public-native-canary-b", "cluster-manager",
        "cluster-observability",
    ),
    "kagent": ("deterministic-byo", "observability-agent"),
}
BASE = Path("/home/nibeauli/kagent-lab")


def verify(session, namespace, agent):
    ref = session.get("agent", {}).get("ref", {})
    if namespace not in RETIRED or agent not in RETIRED[namespace]:
        raise ValueError("outside retired scope")
    if ref.get("namespace") != namespace or ref.get("name") != agent:
        raise ValueError("session ownership differs from requested retired Agent")
    import uuid
    if str(uuid.UUID(session["id"])) != session["id"]:
        raise ValueError("noncanonical session ID")
    return session["id"]


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        json.dump(value, stream, indent=2)


def call(endpoint, token, operation, identity, agent, namespace):
    return subprocess.run(
        [str(BASE / "factory-coding/client"), endpoint, token, operation, identity, agent, namespace],
        capture_output=True, text=True, timeout=195,
    )


def error_code(result):
    for code in ("NotFound", "FailedPrecondition", "Aborted", "DeadlineExceeded", "Unavailable"):
        if f"code = {code}" in result.stderr:
            return code
    return "Other"


def run(apply):
    sys.path.insert(0, "/mnt/q/tools/kagent-lab")
    from lab_transport import port_forward

    os.umask(0o077)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = BASE / "fleet-retirement" / f"session-cleanup-{stamp}"
    output.mkdir(mode=0o700)
    summary = {}
    for namespace, agents in RETIRED.items():
        token = (
            json.loads((BASE / "factory-coding/service.json").read_text())["controllerToken"]
            if namespace == "factory-executions" else "-"
        )
        service = "factory-controller" if namespace == "factory-executions" else "kagent-controller"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with port_forward(namespace, service, port, 8083):
            endpoint = f"127.0.0.1:{port}"
            for agent in agents:
                listing = call(endpoint, token, "list", "retirement", agent, namespace)
                if listing.returncode:
                    raise RuntimeError("native session inventory failed")
                sessions = json.loads(listing.stdout).get("sessions", [])
                for session in sessions:
                    verify(session, namespace, agent)
                save(output / f"{namespace}-{agent}-before.json", {"sessions": sessions})
                receipts = []
                for session in sessions if apply else ():
                    identity = verify(session, namespace, agent)
                    current = call(endpoint, token, "get", identity, agent, namespace)
                    if current.returncode:
                        receipts.append({"id": identity, "result": error_code(current)})
                        continue
                    verify(json.loads(current.stdout)["session"], namespace, agent)
                    deletion = call(endpoint, token, "delete", identity, agent, namespace)
                    receipt = {"id": identity, "result": "Deleted" if deletion.returncode == 0 else error_code(deletion)}
                    if deletion.returncode == 0:
                        check = call(endpoint, token, "get", identity, agent, namespace)
                        receipt["verifiedNotFound"] = check.returncode != 0 and error_code(check) == "NotFound"
                    receipts.append(receipt)
                    save(output / f"{namespace}-{agent}-{identity}-receipt.json", receipt)
                after = call(endpoint, token, "list", "retirement", agent, namespace)
                if after.returncode:
                    raise RuntimeError("native post-cleanup inventory failed")
                remaining = json.loads(after.stdout).get("sessions", [])
                for session in remaining:
                    verify(session, namespace, agent)
                save(output / f"{namespace}-{agent}-after.json", {"sessions": remaining})
                summary[f"{namespace}/{agent}"] = {
                    "before": len(sessions), "after": len(remaining),
                    "results": dict(Counter(item["result"] for item in receipts)),
                    "verifiedNotFound": sum(item.get("verifiedNotFound", False) for item in receipts),
                }
                save(output / f"{namespace}-{agent}-summary.json", summary[f"{namespace}/{agent}"])
                print(namespace, agent, json.dumps(summary[f"{namespace}/{agent}"]), flush=True)
    save(output / "summary.json", summary)


class ScopeTests(unittest.TestCase):
    def test_new_namespaces_and_wrong_ownership_are_refused(self):
        session = {"id": "00000000-0000-0000-0000-000000000001",
                   "agent": {"ref": {"namespace": "baseline", "name": "factory-coder"}}}
        with self.assertRaises(ValueError):
            verify(session, "baseline", "factory-coder")
        with self.assertRaises(ValueError):
            verify(session, "factory-executions", "factory-coder")

    def test_retired_session_is_accepted_only_for_exact_ref(self):
        session = {"id": "00000000-0000-0000-0000-000000000001",
                   "agent": {"ref": {"namespace": "factory-executions", "name": "factory-coder"}}}
        self.assertEqual(verify(session, "factory-executions", "factory-coder"), session["id"])
        with self.assertRaises(ValueError):
            verify(session, "factory-executions", "slopmop-planner")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        unittest.main(argv=[sys.argv[0]])
    else:
        run(args.apply)

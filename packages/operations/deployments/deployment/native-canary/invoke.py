"""Parent-only fixed public probes through existing authenticated native Session/A2A APIs."""
import argparse
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit
import urllib.request
import uuid


def validate(origin, agent):
    location = urlsplit(origin)
    if (location.scheme != "http" or location.hostname != "127.0.0.1" or not location.port
            or location.path or location.query or location.fragment or location.username or location.password
            or not re.fullmatch(r"public-native-canary-[a-z0-9-]{1,32}", agent)):
        raise ValueError("An existing loopback forward and a new public-canary Agent are required.")


def send_params(session, message, code, allow_public_inference=False):
    uuid.UUID(session)
    uuid.UUID(message)
    if code not in {"metadata", "model", "seed", "isolate"} or (code == "model" and not allow_public_inference):
        raise ValueError("Fixed public action required; model also requires --allow-public-inference.")
    return {"message": {"messageId": message, "contextId": session, "role": "ROLE_USER",
            "parts": [{"text": json.dumps({"prompt": code, "allowEdits": False})}]},
            "configuration": {"returnImmediately": True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:18093")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--session-client", default="/home/nibeauli/kagent-lab/factory-coding/client")
    parser.add_argument("action", choices=["create", "send", "get"])
    parser.add_argument("--request-id")
    parser.add_argument("--session")
    parser.add_argument("--message-id")
    parser.add_argument("--task")
    parser.add_argument("--code", choices=["metadata", "model", "seed", "isolate"])
    parser.add_argument("--allow-public-inference", action="store_true")
    args = parser.parse_args()
    validate(args.origin, args.agent)

    def session_rpc(method, identity):
        result = subprocess.run([args.session_client, args.origin.removeprefix("http://"),
                                 str(args.token_file), method, identity, args.agent],
                                check=True, capture_output=True, timeout=210)
        session = json.loads(result.stdout)["session"]
        provenance = session.get("agent", {}).get("ref", {})
        if provenance.get("name") != args.agent or provenance.get("namespace") != "factory-executions":
            raise ValueError("Native session is not owned by the selected public-canary Agent.")
        return session

    if args.action == "create":
        uuid.UUID(args.request_id)
        session = session_rpc("create", args.request_id)
        print(json.dumps({"sessionId": session["id"], "agent": args.agent}))
        return
    uuid.UUID(args.session)
    session = session_rpc("get", args.session)
    if session["id"] != args.session:
        raise ValueError("Native session identity differs.")
    if args.action == "send":
        method, params = "SendMessage", send_params(args.session, args.message_id, args.code, args.allow_public_inference)
    else:
        uuid.UUID(args.task)
        method, params = "GetTask", {"id": args.task}
    token = args.token_file.read_text().strip()
    request = urllib.request.Request(args.origin + "/agents/factory-executions/" + args.agent,
        data=json.dumps({"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}).encode(),
        headers={"content-type": "application/json", "authorization": "Bearer " + token})
    # One SendMessage only. On uncertain transport, inspect with GetTask/ListTasks; never resend automatically.
    with urllib.request.urlopen(request, timeout=240) as response:
        result = json.load(response)
    if "error" in result:
        raise ValueError("Native public probe RPC failed; inspect parent-protected controller diagnostics.")
    task = result["result"].get("task", result["result"])
    if task.get("contextId") != args.session:
        raise ValueError("Native task belongs to another session.")
    report = {"taskId": task["id"], "sessionId": args.session, "state": task.get("status", {}).get("state")}
    if args.action == "get":
        for part in task.get("status", {}).get("message", {}).get("parts", []):
            try:
                outer = json.loads(part.get("text", ""))
                receipt = json.loads(outer["detail"])
                if receipt.get("publicDataOnly") is True:
                    report["receipt"] = {key: receipt[key] for key in ["id", "action", "status", "businessOutcome", "businessWorkAttempted",
                        "result", "archive", "error", "probeError", "failureStage", "sdkSessionId",
                        "sourceSha256"] if key in receipt}
            except (ValueError, KeyError, TypeError):
                continue
    print(json.dumps(report))


if __name__ == "__main__":
    main()

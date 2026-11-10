import json
import subprocess
import urllib.request
import uuid

from lab_transport import BASE, port_forward

with port_forward("kagent", "kagent-controller", 18083, 8083) as origin:
    session = json.loads(subprocess.check_output([
        str(BASE / "bin/grpcurl"), "-plaintext", "-d",
        json.dumps({"agent": {"namespace": "kagent", "name": "deterministic-byo"},
                    "requestId": str(uuid.uuid4())}),
        "127.0.0.1:18083", "kagent.api.v1.SessionService/CreateSession",
    ], text=True, timeout=180))["session"]
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "contextId": session["id"],
                "role": "ROLE_USER",
                "parts": [{"text": "increment"}],
            },
        },
    }
    request = urllib.request.Request(
        origin + "/agents/kagent/deterministic-byo",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    path = BASE / "protected/byo-smoke.json"
    path.write_text(json.dumps(result, indent=2))
    path.chmod(0o600)
    print(json.dumps(result, indent=2))
    if "error" in result:
        raise RuntimeError("Managed BYO invocation failed")

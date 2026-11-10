"""Live shared-process acceptance; no remote writes or work-data content output."""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    settings = json.loads((ROOT / "state" / "broker.json").read_text())
    uid, actor = next(iter(settings["actors"].items()))
    headers = {"X-Agency-Bridge-Key": settings["bridgeKey"], "X-Agency-Actor": actor["subject"],
               "X-Agency-Actor-Uid": uid, "Content-Type": "application/json"}
    sessions = []

    def request(method, body, session=None):
        selected = dict(headers)
        if session:
            selected["Mcp-Session-Id"] = session
        req = urllib.request.Request("http://127.0.0.1:15442/agency/mcp",
                                     data=json.dumps(body).encode(), headers=selected, method=method)
        with urllib.request.urlopen(req, timeout=200) as response:
            raw = response.read()
            return response.getheader("Mcp-Session-Id"), json.loads(raw) if raw else {}

    def health():
        return json.load(urllib.request.urlopen("http://127.0.0.1:15442/health", timeout=5))

    try:
        for number in range(2):
            session, reply = request("POST", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                              "params": {"protocolVersion": "2025-03-26",
                                                         "capabilities": {}, "clientInfo": {
                                                             "name": "shared-acceptance", "version": "1"}}})
            assert "result" in reply, reply
            sessions.append(session)
            _, reply = request("POST", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "load_toolset", "arguments": {"toolset_id": "ado",
                                                       "toolset_args": {"organization": "msasg"}}}}, session)
            assert "result" in reply, reply
            _, reply = request("POST", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "get_tool_schema", "arguments": {"toolset_id": "ado::organization=msasg",
                                                         "tool_name": "repo_pull_request_write"}}}, session)
            assert "result" in reply, reply
            if number == 0:
                pid = health()["gatewayPid"]
            assert health()["gatewayPid"] == pid
        for session in sessions:
            request("DELETE", {}, session)
        sessions.clear()
        assert health()["gatewayPid"] == pid
        print(json.dumps({"twoLogicalSessions": True, "singleGatewayPid": pid,
                          "writeSchemaAvailable": True, "disconnectKeepsGateway": True}))
    finally:
        for session in sessions:
            request("DELETE", {}, session)


if __name__ == "__main__":
    main()

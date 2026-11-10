"""Fixed loopback broker relay for legacy mTLS and host-validated native bearer requests."""
import http.client
import json
import re
from pathlib import Path
import sys
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent
NATIVE_ROUTES = {("POST", "/agency/plugins"), ("POST", "/agency/mcp"),
                 ("POST", "/agency/policy"), ("DELETE", "/agency/mcp"),
                 ("GET", "/metadata/identity/oauth2/token")}
IMDS_ROUTE = ("GET", "/metadata/identity/oauth2/token")
IMDS_API_VERSION = "2019-08-01"
IMDS_RESOURCES = {
    "499b84ac-1321-427f-aa17-267ca6975798",
    "https://management.azure.com/",
}


def relay(request, connection_factory=http.client.HTTPConnection):
    target = urlsplit(request["path"])
    route = (request["method"], target.path)
    if (target.scheme or target.netloc or target.fragment or route not in NATIVE_ROUTES
            or target.query and route != IMDS_ROUTE):
        raise ValueError("Unapproved broker route.")
    if route == IMDS_ROUTE:
        try:
            query = parse_qsl(target.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise ValueError("Invalid managed identity query.") from None
        values = dict(query)
        if (len(query) != 2 or values.get("api-version") != IMDS_API_VERSION
                or values.get("resource") not in IMDS_RESOURCES):
            raise ValueError("Invalid managed identity query.")
        identity_header = request.get("identityHeader")
        if (not isinstance(identity_header, str) or len(identity_header) > 8192
                or not re.fullmatch(r"Bearer [A-Za-z0-9._~+/-]+=*", identity_header, re.I)
                or "authorization" in request or request.get("session")):
            raise ValueError("Invalid managed identity authorization.")
        headers = {"X-IDENTITY-HEADER": identity_header}
        body = None
    elif "authorization" in request:
        authorization = request["authorization"]
        if (route not in NATIVE_ROUTES or not isinstance(authorization, str)
                or len(authorization) > 8192
                or not re.fullmatch(r"Bearer [A-Za-z0-9._~+/-]+=*", authorization, re.I)):
            raise ValueError("Invalid native broker authorization.")
        headers = {"Authorization": authorization, "Content-Type": "application/json"}
        body = json.dumps(request["body"], allow_nan=False)
    else:
        settings = json.loads((ROOT / "state" / "broker.json").read_text())
        headers = {"X-Agency-Bridge-Key": settings["bridgeKey"],
                   "X-Agency-Actor": request["actor"], "X-Agency-Actor-Uid": request["actorUID"],
                   "Content-Type": "application/json"}
        body = json.dumps(request["body"], allow_nan=False)
    if request.get("session"):
        if not isinstance(request["session"], str) or not re.fullmatch(r"[A-Za-z0-9_.~-]{1,256}", request["session"]):
            raise ValueError("Invalid broker session.")
        headers["Mcp-Session-Id"] = request["session"]
    connection = connection_factory("127.0.0.1", 15442, timeout=195)
    try:
        connection.request(request["method"], request["path"], body, headers)
        response = connection.getresponse()
        body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise ValueError("Broker response limit.")
        return {"status": response.status,
            "session": response.getheader("Mcp-Session-Id"),
            "body": body.decode()}
    finally:
        connection.close()


def main():
    raw = sys.stdin.buffer.read(8_100_001)
    if len(raw) > 8_100_000:
        raise ValueError("Broker request limit.")
    reply = json.dumps(relay(json.loads(raw))).encode()
    if len(reply) > 8_100_000:
        raise ValueError("Broker envelope limit.")
    sys.stdout.buffer.write(reply)


if __name__ == "__main__":
    main()

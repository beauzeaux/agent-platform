"""Render only new public-canary native resources; never contact Kubernetes."""
import argparse
import json
from pathlib import Path
import re
import uuid

OWNER = "host-capabilities-native-public-canary"
PREFIX = "public-native-canary"
HOST = "https://host-capabilities.proxy-access.svc.cluster.local:8445"
AUDIENCE = "https://host-capabilities.proxy-access.svc/session"
EMBEDDING = "http://local-embeddings.factory-executions.svc.cluster.local:8080"


def resources(image, run_id, model, worker_pool, snapshot_prefix, allow_public_inference=False):
    uuid.UUID(run_id)
    if (not re.fullmatch(r"[^@\s]+/kagent-lab/native-public-canary@sha256:[a-f0-9]{64}", image)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", model)
            or not re.fullmatch(r"[a-z0-9-]{1,63}", worker_pool)
            or not snapshot_prefix.startswith("gs://") or any(char.isspace() for char in snapshot_prefix)):
        raise ValueError("Pinned public image, public model, existing pool and owned snapshot prefix required.")
    def resource(kind, name, spec):
        return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": kind,
                "metadata": {"name": name, "namespace": "factory-executions",
                             "labels": {"app.kubernetes.io/managed-by": OWNER}}, "spec": spec}
    actor_jwt = {"audiences": [AUDIENCE], "expirationSeconds": 300}
    documents = [
        resource("ModelConfig", PREFIX + "-model", {"provider": "OpenAI", "model": model, "stream": True,
            "openAI": {"baseUrl": HOST + "/v1", "apiFormat": "responses"}, "actorJwt": actor_jwt}),
        resource("RemoteMCPServer", PREFIX + "-mcp", {"description": "Public metadata-only MCP canary",
            "protocol": "STREAMABLE_HTTP", "url": HOST + "/agency/mcp", "actorJwt": actor_jwt,
            "timeout": "20s", "terminateOnClose": True}),
        resource("AgentTemplate", PREFIX + "-template", {"description": "Public-only fixed operator actions",
            "systemPrompt": "Public integration canary.", "modelConfig": {"name": PREFIX + "-model"},
            "tools": [{"mcp": {"server": {"kind": "RemoteMCPServer", "name": PREFIX + "-mcp"},
                                "tools": ["call_tool"], "requireApproval": True}}]}),
        resource("EgressPolicy", PREFIX + "-embedding", {"destinations": [{"origin": EMBEDDING}]}),
    ]
    documents[1]["metadata"]["labels"]["kagent.dev/discovery"] = "disabled"
    memory = json.dumps({"enabled": True, "embedding": {"baseUrl": EMBEDDING + "/v1",
        "model": "BAAI/bge-base-en-v1.5", "dimensions": 768}, "localBridgeUrl": "http://127.0.0.1:18082",
        "privateHttpOrigins": [EMBEDDING]})
    for role in ["a", "b"]:
        name = PREFIX + "-" + role
        env = {"AGENT_NATIVE_CONFIG": "true", "NATIVE_MEMORY_ENABLED": "true",
               "SESSION_ARCHIVE_EXPORT_ENABLED": "true", "PUBLIC_CANARY_RUN_ID": run_id,
               "NATIVE_MEMORY_CONFIG_JSON": memory,
               "PUBLIC_CANARY_INFERENCE": "true" if role == "a" and allow_public_inference else "false"}
        documents.append(resource("Harness", name, {"byo": {}, "workload": {"image": image, "command": ["/app/harness"]},
            "env": [{"name": key, "value": value} for key, value in env.items()],
            "substrate": {"workerPoolRef": {"name": worker_pool},
                          "snapshotPolicy": {"location": snapshot_prefix.rstrip("/") + "/" + run_id + "/" + role + "/"}}}))
        documents.append(resource("Agent", name, {"harnessRef": {"name": name},
            "template": {"ref": {"name": PREFIX + "-template"}}, "egressPolicyRefs": [{"name": PREFIX + "-embedding"}]}))
    return {"apiVersion": "v1", "kind": "List", "items": documents}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--worker-pool", default="kagent-default")
    parser.add_argument("--snapshot-prefix", required=True)
    parser.add_argument("--allow-public-inference", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(".build/resources.json"))
    args = parser.parse_args()
    output = args.output.resolve()
    owned = (Path(__file__).resolve().parent / ".build").resolve()
    if output.parent != owned:
        raise ValueError("Rendered resources must stay in this artifact's .build directory.")
    document = resources(args.image, args.run_id, args.model, args.worker_pool, args.snapshot_prefix,
                         args.allow_public_inference)
    owned.mkdir(exist_ok=True, mode=0o700)
    output.write_text(json.dumps(document, indent=2))
    print("Rendered new public-canary resources:", output)


if __name__ == "__main__":
    main()

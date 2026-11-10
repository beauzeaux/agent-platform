"""Public-fixture API, pgvector and exact native migration acceptance."""
import argparse
import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from common import (ENV, KUBE, NAMESPACE, OWNER, PINS, STATE, UPSTREAM, apply, existing,
                    kube, prepare_state, remove, require_owned, run)

PG = "memory-canary-postgres"
EMBED = "local-embeddings"
FACTS = ["Paris is the capital of France.", "Jupiter is the largest planet in the Solar System.",
         "The Pacific Ocean is the largest ocean on Earth."]
QUERY = "Which city is the capital of France?"


def validate_embeddings(response, count):
    if response.get("object") != "list" or response.get("model") != PINS["model"]:
        raise ValueError("unexpected OpenAI embedding envelope")
    data = response.get("data")
    if not isinstance(data, list) or len(data) != count:
        raise ValueError("embedding count mismatch")
    vectors = []
    for index, row in enumerate(data):
        vector = row.get("embedding")
        if row.get("object") != "embedding" or row.get("index") != index:
            raise ValueError("invalid OpenAI embedding record")
        if not isinstance(vector, list) or len(vector) != 768:
            raise ValueError("native memory requires exactly 768 dimensions")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
            raise ValueError("nonfinite or invalid embedding values")
        if not any(v != 0 for v in vector):
            raise ValueError("zero embedding rejected")
        vectors.append(vector)
    if not isinstance(response.get("usage", {}).get("prompt_tokens"), int):
        raise ValueError("missing OpenAI usage envelope")
    return vectors


def verify_native_client_request():
    prepare_state()
    current = require_owned("deployment", EMBED)
    if not current or current.get("status", {}).get("readyReplicas") != 1:
        raise RuntimeError("owned embedding canary is not healthy")
    image = current["spec"]["template"]["spec"]["containers"][0]["image"]
    if image != PINS["teiImage"]:
        raise RuntimeError("embedding canary does not use the pinned TEI image")
    request = {"model": PINS["model"], "input": FACTS[0], "dimensions": 768}
    output = run([*KUBE, "exec", "-i", "-n", NAMESPACE, "deployment/" + EMBED, "--",
                  "curl", "--fail-with-body", "--silent", "--show-error", "--max-time", "60",
                  "http://127.0.0.1:8080/v1/embeddings", "-H", "Content-Type: application/json",
                  "-H", "Authorization: Bearer kagent-credential-injected",
                  "--data-binary", "@-", "--write-out", "\n%{http_code}"],
                 data=json.dumps(request, separators=(",", ":")).encode(),
                 timeout=80, name="native-client-request")
    body, status = output.decode().rsplit("\n", 1)
    if status != "200":
        raise AssertionError("native client request did not return HTTP 200")
    response = json.loads(body)
    vectors = validate_embeddings(response, 1)
    report = {"accepted": True, "httpStatus": 200, "verifiedAt": datetime.now(timezone.utc).isoformat(),
              "teiImage": image, "request": request, "encodingFormatOmitted": True,
              "inertBearerPlaceholderSent": True, "responseObject": response["object"],
              "responseModel": response["model"], "dimensions": len(vectors[0]),
              "finiteNonzeroFloatArray": True, "usage": response["usage"],
              "scope": "exact public-fixture wire contract only; not actor authentication or Service routing proof"}
    (STATE / "native-client-contract.json").write_text(json.dumps(report, indent=2))
    print("PASS: exact native client body and inert bearer header accepted: HTTP 200, 768 finite floats.")


def psql(sql, *, failure_state=None):
    result = subprocess.run([*KUBE, "exec", "-i", "-n", NAMESPACE, "deployment/" + PG,
                             "-c", "postgresql", "--", "psql", "-X", "-qAt",
                             "--set=ON_ERROR_STOP=1", "--set=VERBOSITY=sqlstate",
                             "-U", "postgres", "-d", "memory_canary"],
                            input=sql.encode(), capture_output=True, timeout=90, env=ENV)
    if failure_state:
        if result.returncode == 0 or failure_state.encode() not in result.stderr:
            (STATE / "verify-negative-postgres.log").write_bytes(result.stderr)
            raise AssertionError("database negative check did not produce the expected SQLSTATE")
    elif result.returncode:
        (STATE / "verify-postgres.log").write_bytes(result.stderr)
        raise RuntimeError("canary database validation failed")
    return result.stdout.decode().strip()


def verify_ingress_denial():
    name = "memory-canary-network-probe"
    labels = {"app": name, "app.kubernetes.io/managed-by": OWNER}
    pods = json.loads(kube("get", "pods", "-n", NAMESPACE, "-l", "app=" + EMBED, "-o", "json"))
    address = pods["items"][0]["status"]["podIP"]
    try:
        apply({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
               "metadata": {"name": name, "namespace": NAMESPACE},
               "spec": {"podSelector": {"matchLabels": {"app": name}}, "policyTypes": ["Egress"],
                        "egress": [{"to": [{"podSelector": {"matchLabels": {"app": EMBED}}}],
                                    "ports": [{"port": 8080, "protocol": "TCP"}]}]}})
        apply({"apiVersion": "v1", "kind": "Pod",
               "metadata": {"name": name, "namespace": NAMESPACE, "labels": labels},
               "spec": {"automountServiceAccountToken": False, "restartPolicy": "Never",
                        "nodeSelector": {"kubernetes.io/hostname": "cpc-nibea-8t76e"},
                        "securityContext": {"runAsUser": 1000, "runAsNonRoot": True,
                                            "seccompProfile": {"type": "RuntimeDefault"}},
                        "containers": [{"name": "probe", "image": PINS["teiImage"],
                                        "imagePullPolicy": "IfNotPresent", "command": ["sleep", "180"],
                                        "securityContext": {"allowPrivilegeEscalation": False,
                                                            "capabilities": {"drop": ["ALL"]}},
                                        "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                                                      "limits": {"cpu": "100m", "memory": "128Mi"}}}]}})
        kube("wait", "pod/" + name, "-n", NAMESPACE, "--for=condition=Ready", "--timeout=90s", timeout=110)
        result = subprocess.run([*KUBE, "exec", "-n", NAMESPACE, name, "--", "curl", "--silent",
                                 "--fail", "--connect-timeout", "3", "--max-time", "5",
                                 "http://" + address + ":8080/health"], env=ENV,
                                capture_output=True, timeout=15)
        if result.returncode != 28:
            raise AssertionError("unauthorized ingress must time out, not succeed or fail to execute")
    finally:
        remove("pod", name)
        remove("networkpolicy", name)


def verify():
    prepare_state()
    (STATE / "verification.json").unlink(missing_ok=True)
    for name in [PG, EMBED]:
        current = require_owned("deployment", name)
        if not current or current.get("status", {}).get("readyReplicas") != 1:
            raise RuntimeError("owned canary is not healthy")
    revision = run(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"]).decode().strip()
    if revision != PINS["kagentCommit"]:
        raise RuntimeError("native source pin changed")
    hashes = kube("exec", "-n", NAMESPACE, "deployment/" + EMBED, "--", "sha256sum",
                  *["/model/" + name for name in PINS["modelFiles"]]).decode().splitlines()
    mounted_hashes = {line.split(maxsplit=1)[1].removeprefix("/model/"): line.split()[0] for line in hashes}
    if mounted_hashes != PINS["modelFiles"]:
        raise AssertionError("mounted model cache differs from pinned public artifacts")
    request = json.dumps({"input": [*FACTS, QUERY], "model": PINS["model"],
                          "encoding_format": "float", "dimensions": 768})
    response = json.loads(kube("exec", "-i", "-n", NAMESPACE, "deployment/" + EMBED, "--",
                               "curl", "--fail-with-body", "--silent", "--show-error", "--max-time", "60",
                               "http://127.0.0.1:8080/v1/embeddings",
                               "-H", "Content-Type: application/json", "--data-binary", "@-",
                               data=request.encode(), timeout=80))
    vectors = validate_embeddings(response, len(FACTS) + 1)
    (STATE / "fixture-embeddings.json").write_text(json.dumps(response))
    verify_native_client_request()
    result = subprocess.run([*KUBE, "exec", "-n", NAMESPACE, "deployment/" + EMBED, "--",
                             "curl", "--silent", "--fail", "--connect-timeout", "3", "--max-time", "5",
                             "https://1.1.1.1/"], capture_output=True, timeout=15, env=ENV)
    if result.returncode != 28:
        raise AssertionError("public TCP egress must time out, not fail for DNS or TLS reasons")
    verify_ingress_denial()
    vector_schema = psql("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector';")
    if not vector_schema:
        psql("CREATE SCHEMA extensions; REVOKE ALL ON SCHEMA extensions FROM PUBLIC; "
             "CREATE EXTENSION vector WITH SCHEMA extensions;")
    elif vector_schema != "extensions":
        raise AssertionError("extension schema mismatch")
    psql("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='canary_owner') THEN "
         "CREATE ROLE canary_owner NOLOGIN; CREATE ROLE canary_readwrite NOLOGIN; END IF; END $$; "
         "CREATE SCHEMA IF NOT EXISTS memory_canary AUTHORIZATION canary_owner; "
         "GRANT USAGE ON SCHEMA extensions TO canary_owner,canary_readwrite; "
         "GRANT USAGE ON TYPE extensions.vector TO canary_owner,canary_readwrite; "
         "GRANT USAGE ON SCHEMA memory_canary TO canary_readwrite; "
         "ALTER DEFAULT PRIVILEGES FOR ROLE canary_owner IN SCHEMA memory_canary "
         "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO canary_readwrite;")
    binary = STATE / "kagent"
    if not binary.exists():
        binary.write_bytes(run(["cat", "/home/nibeauli/kagent-lab/artifacts/kagent"]))
        binary.chmod(0o700)
    provenance = run(["/home/nibeauli/kagent-lab/byo-build/toolchain/go/bin/go",
                      "version", "-m", str(binary)]).decode()
    if "vcs.revision=" + PINS["kagentCommit"] not in provenance or "vcs.modified=false" not in provenance:
        raise RuntimeError("native migration CLI does not match the clean pinned checkout")
    for path, target in [(binary, "/work/kagent")]:
        kube("exec", "-i", "-n", NAMESPACE, "deployment/" + PG, "--", "sh", "-c",
             "cat > " + target + " && chmod 700 " + target, data=path.read_bytes())
    config = "database:\n  schema: memory_canary\n  vectorSchema: extensions\n  vectorEnabled: true\n"
    kube("exec", "-i", "-n", NAMESPACE, "deployment/" + PG, "--", "sh", "-c",
         "cat > /work/kagent.yaml", data=config.encode())
    cli = ["/work/kagent", "--config", "/work/kagent.yaml", "db", "migrate"]
    flags = ["--db-role", "canary_owner", "--db-url",
             "postgresql://postgres@/memory_canary?host=/var/run/postgresql&sslmode=disable"]
    for operation in ["up", "status", "up"]:
        output = kube("exec", "-n", NAMESPACE, "deployment/" + PG, "--", *cli, operation, *flags, timeout=150)
        (STATE / ("migration-" + operation + ".log")).write_bytes(output)
    index = psql("SELECT indexdef FROM pg_indexes WHERE schemaname='memory_canary' "
                 "AND indexname='idx_memory_embedding_hnsw';")
    if "USING hnsw" not in index or "extensions.vector_cosine_ops" not in index:
        raise AssertionError("native HNSW cosine migration is missing")
    fixtures = json.dumps([{"id": "public-" + str(i), "content": fact, "embedding": vectors[i]}
                           for i, fact in enumerate(FACTS)], separators=(",", ":"))
    sql = "\\set fixtures '" + fixtures.replace("'", "''") + "'\n"
    sql += "\\set query '" + json.dumps(vectors[-1]) + "'\n"
    sql += """
SET ROLE canary_readwrite;
SET search_path TO memory_canary;
INSERT INTO memory(id,agent_name,user_id,content,embedding,created_at)
SELECT item->>'id','public-fixture','canary-user',item->>'content',
       (item->'embedding')::text::extensions.vector,now()
FROM jsonb_array_elements(:'fixtures'::jsonb) item
ON CONFLICT(id) DO UPDATE SET embedding=EXCLUDED.embedding;
SELECT id FROM memory
ORDER BY embedding OPERATOR(extensions.<=>)
    :'query'::extensions.vector LIMIT 1;
SELECT count(*) FROM memory WHERE extensions.vector_dims(embedding)=768;
"""
    results = psql(sql).splitlines()
    if results[-2:] != ["public-0", "3"]:
        raise AssertionError("real 768-dimensional insert/cosine search failed")
    psql("SET ROLE canary_readwrite; CREATE TABLE memory_canary.forbidden(id int);", failure_state="42501")
    psql("SET ROLE canary_readwrite; INSERT INTO memory_canary.memory(id,embedding) VALUES ('bad','[1,2]'::extensions.vector);",
         failure_state="22000")
    service = existing("service", EMBED)
    if service["spec"]["type"] != "ClusterIP" or service["spec"].get("externalIPs"):
        raise AssertionError("embedding service must remain internal")
    report = {"healthy": True, "verifiedAt": datetime.now(timezone.utc).isoformat(),
              "kagentCommit": revision, "pins": PINS,
              "pgvectorVersion": psql("SELECT extversion FROM pg_extension WHERE extname='vector';"),
              "postgresVersion": psql("SHOW server_version;"),
              "dimensions": 768, "fixtureCount": len(FACTS), "nearestNeighbor": "public-0",
              "nativeMigrationCliSha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "nativeMigrationCliCommit": PINS["kagentCommit"],
              "nativeMigrations": "up/status/up passed as canary_owner",
              "nativeVectorMigrationVersion": psql("SELECT max(version_id) FROM memory_canary.vector_schema_migrations WHERE is_applied;"),
              "negativeChecks": ["readwrite DDL denied", "wrong dimension rejected", "public egress denied",
                                 "unauthorized pod ingress denied despite explicit probe egress grant"],
              "embeddingUrl": "http://local-embeddings.factory-executions.svc.cluster.local:8080/v1",
              "scope": "isolated backend only; no live memory API authorization or business cutover"}
    (STATE / "verification.json").write_text(json.dumps(report, indent=2))
    print("PASS: offline private 768D embeddings, native migrations, pgvector insert/search and negative checks.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-client-only", action="store_true")
    args = parser.parse_args()
    verify_native_client_request() if args.native_client_only else verify()

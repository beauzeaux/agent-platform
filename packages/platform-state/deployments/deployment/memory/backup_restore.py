"""Protected business backups and an owned, socket-only disposable restore proof."""
import json
import os
import secrets
import select
import subprocess
import time
from datetime import datetime, timezone
from common import (ENV, KUBE, NAMESPACE, OWNER, PINS, STATE, apply, kube,
                    prepare_state, remove, require_owned)
from prepare import digest

BUSINESS = [*KUBE, "exec", "-i", "-n", "kagent", "deployment/kagent-postgresql",
            "-c", "postgresql", "--"]
PSQL = ["psql", "-X", "-qAt", "--set=ON_ERROR_STOP=1", "-U", "postgres", "-d", "kagent"]
PGDATA = "/var/lib/postgresql/data/pgdata"
INVENTORY_SQL = """
SELECT format(
    'SELECT json_build_object(''schema'',%L,''table'',%L,''rows'',count(*)) FROM %I.%I;',
    n.nspname,c.relname,n.nspname,c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%'
  AND n.nspname <> 'information_schema'
ORDER BY n.nspname,c.relname
\\gexec
"""


def command(args, directory, label, *, data=None, timeout=180):
    with (directory / (label + ".log")).open("wb") as error:
        result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=error,
                                timeout=timeout, env=ENV)
    if result.returncode:
        raise RuntimeError(label + " failed; inspect the protected backup directory")
    return result.stdout


def stream_backup(args, path, directory, label, timeout):
    partial = path.with_name(path.name + ".partial")
    with partial.open("xb") as output, (directory / (label + ".log")).open("wb") as error:
        result = subprocess.run(args, stdout=output, stderr=error, timeout=timeout, env=ENV)
        output.flush()
        os.fsync(output.fileno())
    if result.returncode:
        return False
    partial.replace(path)
    return True


def read_line(process, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
        raise RuntimeError("bounded exported-snapshot metadata read timed out")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("exported-snapshot session exited; inspect protected diagnostics")
    return line.decode().strip()


def parse_inventory(output):
    result = [json.loads(line) for line in output.decode().splitlines() if line.strip()]
    for row in result:
        if set(row) != {"schema", "table", "rows"} or not isinstance(row["rows"], int) or row["rows"] < 0:
            raise ValueError("invalid table-count inventory")
    return sorted(result, key=lambda row: (row["schema"], row["table"]))


def logical_backup(directory):
    log = (directory / "snapshot-session.log").open("wb")
    process = subprocess.Popen([*BUSINESS, *PSQL], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=log, env=ENV, bufsize=0)
    try:
        process.stdin.write(b"SET statement_timeout='120s'; SET idle_in_transaction_session_timeout='600s';\n"
                            b"BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
                            b"SELECT json_build_object('snapshot',pg_export_snapshot());\n")
        snapshot = json.loads(read_line(process, time.monotonic() + 30))["snapshot"]
        process.stdin.write((INVENTORY_SQL + "\n\\echo INVENTORY_DONE\n").encode())
        lines, deadline = [], time.monotonic() + 180
        while (line := read_line(process, deadline)) != "INVENTORY_DONE":
            lines.append(line)
        inventory = parse_inventory(("\n".join(lines)).encode())
        (directory / "logical-source-inventory.json").write_text(json.dumps(inventory, indent=2))
        target = directory / "kagent.no-owner-no-acl.dump"
        ok = stream_backup([*BUSINESS, "timeout", "-s", "TERM", "300", "pg_dump",
                            "--host=/var/run/postgresql", "--username=postgres", "--no-password",
                            "--dbname=kagent", "--format=custom", "--compress=gzip:1",
                            "--no-owner", "--no-acl", "--snapshot=" + snapshot],
                           target, directory, "pg-dump", 320)
        if not ok:
            raise RuntimeError("logical pg_dump failed; inspect protected pg-dump.log")
        process.stdin.write(b"COMMIT;\n\\quit\n")
        process.stdin.close()
        process.wait(timeout=15)
        if process.returncode:
            raise RuntimeError("exported-snapshot session did not finish cleanly")
        return target, inventory
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        log.close()


def restore_resources(name, image):
    labels = {"app.kubernetes.io/managed-by": OWNER, "app": name}
    policy = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
              "metadata": {"name": name, "namespace": NAMESPACE},
              "spec": {"podSelector": {"matchLabels": {"app": name}},
                       "policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []}}
    pod = {"apiVersion": "v1", "kind": "Pod",
           "metadata": {"name": name, "namespace": NAMESPACE, "labels": labels},
           "spec": {"automountServiceAccountToken": False, "restartPolicy": "Never",
                    "activeDeadlineSeconds": 1200, "terminationGracePeriodSeconds": 30,
                    "nodeSelector": {"kubernetes.io/hostname": "cpc-nibea-8t76e"},
                    "securityContext": {"fsGroup": 70, "runAsUser": 70, "runAsGroup": 70,
                                        "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{"name": "restore", "image": image, "imagePullPolicy": "IfNotPresent",
                                    "command": ["sleep", "1200"],
                                    "securityContext": {"allowPrivilegeEscalation": False,
                                                        "capabilities": {"drop": ["ALL"]}},
                                    "resources": {"requests": {"cpu": "100m", "memory": "256Mi"},
                                                  "limits": {"cpu": "1", "memory": "1Gi"}},
                                    "volumeMounts": [{"name": "data", "mountPath": "/var/lib/postgresql/data"},
                                                    {"name": "socket", "mountPath": "/var/run/postgresql"},
                                                    {"name": "work", "mountPath": "/work"}]}],
                    "volumes": [{"name": "data", "emptyDir": {"sizeLimit": "4Gi"}},
                                {"name": "socket", "emptyDir": {"sizeLimit": "16Mi"}},
                                {"name": "work", "emptyDir": {"sizeLimit": "512Mi"}}]}}
    return policy, pod


def copy_stream(args, source, directory, label, timeout=180):
    with source.open("rb") as stream, (directory / (label + ".log")).open("wb") as error:
        result = subprocess.run(args, stdin=stream, stdout=subprocess.PIPE, stderr=error,
                                timeout=timeout, env=ENV)
    if result.returncode:
        raise RuntimeError(label + " failed; inspect protected diagnostics")
    return result.stdout


def restore_proof(directory, name, physical, logical, source_inventory, image):
    policy, pod = restore_resources(name, image)
    apply(policy)
    try:
        apply(pod)
        kube("wait", "pod/" + name, "-n", NAMESPACE, "--for=condition=Ready", "--timeout=120s", timeout=140)
        remote = [*KUBE, "exec", "-i", "-n", NAMESPACE, name, "-c", "restore", "--"]
        command([*remote, "mkdir", "-p", PGDATA], directory, "restore-directory")
        if physical:
            copy_stream([*remote, "tar", "-xf", "-", "-C", PGDATA], physical, directory, "physical-extract")
            output = command([*remote, "pg_verifybackup", "--exit-on-error", PGDATA],
                             directory, "pg-verifybackup")
            (directory / "pg-verifybackup-result.txt").write_bytes(output)
        else:
            command([*remote, "initdb", "-D", PGDATA, "--auth-local=peer", "--auth-host=scram-sha-256"],
                    directory, "logical-only-initdb")
        config = ("listen_addresses=''\nssl=off\nunix_socket_directories='/var/run/postgresql'\n"
                  "hba_file='/work/pg_hba.conf'\nident_file='/work/pg_ident.conf'\n"
                  "shared_buffers='128MB'\nmax_connections=40\n")
        for path, content in [("/work/postgresql.conf", config),
                              ("/work/pg_hba.conf", "local all postgres peer\n"),
                              ("/work/pg_ident.conf", "")]:
            command([*remote, "sh", "-c", "umask 077; cat > " + path],
                    directory, "restore-local-config", data=content.encode())
        command([*remote, "pg_ctl", "-D", PGDATA, "-l", "/work/server.log",
                 "-o", "-c config_file=/work/postgresql.conf", "-w", "-t", "60", "start"],
                directory, "restore-start", timeout=80)
        admin = [*remote, "psql", "-X", "-qAt", "--set=ON_ERROR_STOP=1", "-U", "postgres"]
        runtime = json.loads(command([*admin, "-d", "postgres", "-c",
            "SELECT json_build_object('listenAddresses',current_setting('listen_addresses'),"
            "'ssl',current_setting('ssl'),'version',current_setting('server_version'));"],
            directory, "restore-runtime"))
        if runtime["listenAddresses"] != "" or runtime["ssl"] != "off":
            raise AssertionError("restore server is not Unix-socket-only")
        proof = {"runtime": runtime}
        if physical:
            output = command([*admin, "-d", "kagent"], directory, "physical-table-counts",
                             data=INVENTORY_SQL.encode())
            physical_inventory = parse_inventory(output)
            (directory / "physical-restored-inventory.json").write_text(json.dumps(physical_inventory, indent=2))
            expected = {(row["schema"], row["table"]) for row in source_inventory}
            actual = {(row["schema"], row["table"]) for row in physical_inventory}
            if not expected.issubset(actual):
                raise AssertionError("physical restore is missing source-snapshot tables")
            proof["physical"] = {"backupManifestVerified": True, "readableTables": len(physical_inventory),
                                 "rows": sum(row["rows"] for row in physical_inventory),
                                 "sourceSnapshotTablesPresent": True,
                                 "rowCountsComparable": "different consistent backup times; live writes may differ"}
        command([*remote, "createdb", "-U", "postgres", "--template=template0", "memory_logical_restore"],
                directory, "logical-createdb")
        copy_stream([*remote, "pg_restore", "-U", "postgres", "--dbname=memory_logical_restore",
                     "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction"],
                    logical, directory, "logical-restore", timeout=300)
        output = command([*admin, "-d", "memory_logical_restore"], directory, "logical-table-counts",
                         data=INVENTORY_SQL.encode())
        restored = parse_inventory(output)
        (directory / "logical-restored-inventory.json").write_text(json.dumps(restored, indent=2))
        if restored != source_inventory:
            raise AssertionError("logical restore table counts differ from the exported source snapshot")
        proof["logical"] = {"sourceSnapshotCountsExactlyMatch": True, "readableTables": len(restored),
                            "rows": sum(row["rows"] for row in restored),
                            "ownershipAndAclRestored": False}
        command([*remote, "pg_ctl", "-D", PGDATA, "-w", "-t", "60", "-m", "fast", "stop"],
                directory, "restore-stop", timeout=80)
        return proof
    finally:
        try:
            if require_owned("pod", name):
                output = subprocess.run([*KUBE, "exec", "-n", NAMESPACE, name, "--", "cat", "/work/server.log"],
                                        capture_output=True, timeout=30, env=ENV)
                (directory / "isolated-restore-server.log").write_bytes(output.stdout + output.stderr)
        finally:
            try:
                remove("pod", name)
            finally:
                remove("networkpolicy", name)


def business_metadata():
    deployment = json.loads(kube("get", "deployment", "kagent-postgresql", "-n", "kagent", "-o", "json"))
    spec = deployment["spec"]
    image = spec["template"]["spec"]["containers"][0]["image"]
    if image.split("@")[-1] != PINS["postgresBase"].split("@")[-1]:
        raise RuntimeError("business image differs from the pre-cutover exact base; stop for parent coordination")
    import hashlib
    return {"uid": deployment["metadata"]["uid"], "image": image,
            "deploymentSpecSha256": hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()}


def main():
    prepare_state()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    directory = STATE / "backups" / stamp
    directory.mkdir(parents=True, mode=0o700)
    name = "memory-restore-" + stamp.lower()
    report = {"startedAt": datetime.now(timezone.utc).isoformat(), "directory": str(directory),
              "restoreProbe": name, "healthyGate": False}
    try:
        report["businessBefore"] = business_metadata()
        physical = directory / "postgres18.base.tar"
        available = stream_backup([*BUSINESS, "timeout", "-s", "TERM", "600", "pg_basebackup",
                                   "--host=/var/run/postgresql", "--username=postgres", "--no-password",
                                   "--pgdata=-", "--format=tar", "--wal-method=fetch", "--checkpoint=spread",
                                   "--max-rate=32M", "--manifest-checksums=SHA256",
                                   "--label=host-capabilities-memory-precutover"],
                                  physical, directory, "pg-basebackup", 620)
        report["physicalBackup"] = {"status": "complete" if available else "blocked",
                                    "path": str(physical if available else physical.with_name(physical.name + ".partial"))}
        if available:
            report["physicalBackup"].update(bytes=physical.stat().st_size, sha256=digest(physical))
        logical, inventory = logical_backup(directory)
        report["logicalBackup"] = {"path": str(logical), "bytes": logical.stat().st_size, "sha256": digest(logical),
                                   "sameSnapshotTableCounts": len(inventory),
                                   "ownershipAndAclIncluded": False}
        image = json.loads((STATE / "image.json").read_text())
        reference = image["repository"] + "@" + image["digest"]
        report["restoreImage"] = reference
        report["restoreProof"] = restore_proof(directory, name, physical if available else None,
                                             logical, inventory, reference)
        report["businessAfter"] = business_metadata()
        if report["businessBefore"] != report["businessAfter"]:
            raise AssertionError("business Deployment changed during the backup gate")
        report["restoreProbeRemoved"] = require_owned("pod", name) is None
        report["healthyGate"] = available and report["restoreProbeRemoved"]
        if not available:
            report["blocker"] = "native pg_basebackup rejected; existing replication authentication was not changed"
        report["completedAt"] = datetime.now(timezone.utc).isoformat()
    except Exception as error:
        report["errorType"] = type(error).__name__
        report["error"] = str(error)
        raise
    finally:
        (directory / "report.json").write_text(json.dumps(report, indent=2))
        print("Protected backup/restore report:", directory / "report.json")
    if not report["healthyGate"]:
        raise RuntimeError("physical backup gate blocked; logical result and diagnostics preserved")
    print("PASS: physical manifest/WAL restore and exact-snapshot logical table counts; isolated probe removed.")


if __name__ == "__main__":
    main()

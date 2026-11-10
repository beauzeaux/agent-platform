"""Explicitly gated offline XL PVC backup; never perform node/disk recovery."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shlex
import signal
import subprocess
import tarfile
import time
from datetime import datetime, timezone
from backup_restore import (BUSINESS, INVENTORY_SQL, PGDATA, command, copy_stream,
                            parse_inventory, restore_resources)
from common import (ENV, KUBE, NAMESPACE, PINS, STATE, apply, prepare_state,
                    kube, remove, require_owned)
from prepare import digest

DEPLOYMENT_UID = "59c14067-000e-4b51-afdf-25490d60ad30"
PVC_UID = "de1298c7-2717-4c29-bc0e-d3bc971a9465"
PV_UID = "dbf49fd1-a708-495c-b31e-15e5e9a1e284"
PV_NAME = "pvc-" + PVC_UID
XL_NODE = "cpc-nibea-2l35i"
XL_ADDRESS = "10.82.0.3"
PVC_PATH = "/mnt/microk8s-hostpath/kagent-kagent-postgresql-" + PV_NAME
PREPARED = STATE / "offline-pvc-prepared.json"
MAX_BYTES = 2 * 1024**3
MAX_MAINTENANCE_SECONDS = 180
SSH = ["runuser", "-u", "nibeauli", "--", "ssh", "-n", "-o", "BatchMode=yes",
       "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5",
       "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2", XL_ADDRESS]

CATALOG_SQL = """
SELECT jsonb_build_object(
 'roles',(SELECT jsonb_agg(jsonb_build_object('name',rolname,'super',rolsuper,
    'inherit',rolinherit,'createRole',rolcreaterole,'createDb',rolcreatedb,
    'login',rolcanlogin,'replication',rolreplication,'bypassRls',rolbypassrls,
    'connectionLimit',rolconnlimit,'validUntil',rolvaliduntil) ORDER BY rolname) FROM pg_roles),
 'memberships',(SELECT jsonb_agg(jsonb_build_object('role',pg_get_userbyid(roleid),
    'member',pg_get_userbyid(member),'admin',admin_option,'inherit',inherit_option,
    'set',set_option) ORDER BY roleid,member) FROM pg_auth_members),
 'schemas',(SELECT jsonb_agg(jsonb_build_object('name',nspname,'owner',pg_get_userbyid(nspowner),
    'acl',nspacl::text) ORDER BY nspname) FROM pg_namespace
    WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema'),
 'relations',(SELECT jsonb_agg(jsonb_build_object('schema',n.nspname,'name',c.relname,
    'kind',c.relkind,'owner',pg_get_userbyid(c.relowner),'acl',c.relacl::text,
    'rowSecurity',c.relrowsecurity,'forceRowSecurity',c.relforcerowsecurity)
    ORDER BY n.nspname,c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'),
 'columnAcls',(SELECT jsonb_agg(jsonb_build_object('schema',n.nspname,'table',c.relname,
    'column',a.attname,'acl',a.attacl::text) ORDER BY n.nspname,c.relname,a.attnum)
    FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace WHERE a.attacl IS NOT NULL
    AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'),
 'defaultAcls',(SELECT jsonb_agg(jsonb_build_object('role',pg_get_userbyid(d.defaclrole),
    'schema',COALESCE(n.nspname,''),'type',d.defaclobjtype,'acl',d.defaclacl::text)
    ORDER BY d.defaclrole,d.defaclnamespace,d.defaclobjtype)
    FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace),
 'routineAcls',(SELECT jsonb_agg(jsonb_build_object('schema',n.nspname,'name',p.proname,
    'arguments',pg_get_function_identity_arguments(p.oid),'owner',pg_get_userbyid(p.proowner),
    'acl',p.proacl::text) ORDER BY n.nspname,p.proname,p.oid)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'),
 'typeAcls',(SELECT jsonb_agg(jsonb_build_object('schema',n.nspname,'name',t.typname,
    'owner',pg_get_userbyid(t.typowner),'acl',t.typacl::text) ORDER BY n.nspname,t.typname)
    FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
    WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'),
 'databases',(SELECT jsonb_agg(jsonb_build_object('name',datname,'owner',pg_get_userbyid(datdba),
    'acl',datacl::text) ORDER BY datname) FROM pg_database),
 'extensions',(SELECT jsonb_agg(jsonb_build_object('name',extname,'version',extversion,
    'schema',n.nspname,'owner',pg_get_userbyid(extowner)) ORDER BY extname)
    FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace)
);
"""

REMOTE_AUDIT = """
import json,os,pathlib,socket,stat,sys
def check(condition,message):
 if not condition: raise RuntimeError(message)
root=pathlib.Path(ROOT)
data=root/'pgdata'
check(socket.gethostname().lower()==NODE, 'unexpected XL hostname')
check(str(root.resolve(strict=True))==ROOT, 'unexpected PVC realpath')
if EXPECTED_DEVICE is not None: check(root.stat().st_dev==EXPECTED_DEVICE, 'unexpected PVC filesystem')
check(sorted(p.name for p in root.iterdir())==['pgdata'], 'unexpected PVC entries')
check(str(data.resolve(strict=True))==ROOT+'/pgdata', 'unexpected PGDATA realpath')
check(data.stat().st_uid==70, 'unexpected PGDATA owner')
check((data/'PG_VERSION').read_text().strip()=='18', 'unexpected PostgreSQL major')
check(not list((data/'pg_tblspc').iterdir()), 'external PostgreSQL tablespaces')
files=[]
for parent,dirs,names in os.walk(data,followlinks=False):
 for name in dirs+names:
  p=pathlib.Path(parent)/name
  try: st=p.lstat()
  except FileNotFoundError:
   if OFFLINE: raise
   continue
  check(not stat.S_ISLNK(st.st_mode), 'external/symlinked PGDATA entry')
  check(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode), 'special PGDATA entry')
  if stat.S_ISREG(st.st_mode):
   check(st.st_nlink==1, 'hardlinked PGDATA file')
   files.append({'path':str(p.relative_to(root)),'bytes':st.st_size})
check(sum(f['bytes'] for f in files)<=MAX_SIZE, 'PVC exceeds bounded restore budget')
if OFFLINE: check(not (data/'postmaster.pid').exists(), 'postmaster.pid remains after pod termination')
result={'node':NODE,'rootReal':str(root.resolve()),'dataUid':data.stat().st_uid,
        'dataGid':data.stat().st_gid,'major':'18','regularBytes':sum(f['bytes'] for f in files),
        'regularFiles':len(files),'rootDevice':root.stat().st_dev}
if OFFLINE:
 result['files']=sorted(files,key=lambda f:f['path'])
 print(json.dumps(result),file=sys.stderr,flush=True)
 os.execvp('tar',['tar','--one-file-system','--numeric-owner','--format=posix',
                  '-C',ROOT,'-cf','-','pgdata'])
else: print(json.dumps(result))
"""


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def api(*args, directory=None, timeout=8):
    result = subprocess.run([*KUBE, "--request-timeout=5s", *args], env=ENV,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        if directory:
            (directory / "maintenance-kubernetes.log").write_bytes(result.stdout + result.stderr)
        raise RuntimeError("bounded Kubernetes operation failed")
    return json.loads(result.stdout) if result.stdout.strip() else None


def node_ready(node):
    return any(c["type"] == "Ready" and c["status"] == "True" for c in node["status"]["conditions"])


def source_identity():
    d = api("get", "deployment", "kagent-postgresql", "-n", "kagent", "-o", "json")
    pvc = api("get", "pvc", "kagent-postgresql", "-n", "kagent", "-o", "json")
    pv = api("get", "pv", PV_NAME, "-o", "json")
    node = api("get", "node", XL_NODE, "-o", "json")
    meta, spec = d["metadata"], d["spec"]
    container = spec["template"]["spec"]["containers"][0]
    if meta["uid"] != DEPLOYMENT_UID or meta.get("labels", {}).get("app.kubernetes.io/managed-by") != "kagent-cli":
        raise RuntimeError("refusing an unowned/replaced business Deployment")
    if container["name"] != "postgresql" or container["image"].split("@")[-1] != PINS["postgresBase"].split("@")[-1]:
        raise RuntimeError("unexpected business PostgreSQL image/container")
    if spec.get("replicas") != 1:
        raise RuntimeError("expected exactly one original business replica")
    if pvc["metadata"]["uid"] != PVC_UID or pvc["status"]["phase"] != "Bound" or pvc["spec"]["volumeName"] != PV_NAME:
        raise RuntimeError("unexpected/unbound source PVC")
    claim = pv["spec"]["claimRef"]
    if pv["metadata"]["uid"] != PV_UID or claim["uid"] != PVC_UID or claim["namespace"] != "kagent" or claim["name"] != "kagent-postgresql":
        raise RuntimeError("refusing an unowned/rebound source PV")
    terms = pv["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"]
    expected_terms = [{"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In", "values": [XL_NODE]}]}]
    if terms != expected_terms or pv["spec"].get("hostPath", {}).get("path") != PVC_PATH:
        raise RuntimeError("unexpected PV node affinity or hostpath")
    if not node_ready(node) or node.get("spec", {}).get("unschedulable") or not any(a["type"] == "InternalIP" and a["address"] == XL_ADDRESS for a in node["status"]["addresses"]):
        raise RuntimeError("XL NotReady or unexpected node address; no recovery attempted")
    selector = ",".join(k + "=" + v for k, v in sorted(spec["selector"]["matchLabels"].items()))
    pods = api("get", "pods", "-n", "kagent", "-l", selector, "-o", "json")["items"]
    if len(pods) != 1 or pods[0]["spec"].get("nodeName") != XL_NODE:
        raise RuntimeError("unexpected business Pod placement/count")
    rs_ref = pods[0]["metadata"]["ownerReferences"][0]
    rs = api("get", "replicaset", rs_ref["name"], "-n", "kagent", "-o", "json")
    if rs_ref["uid"] != rs["metadata"]["uid"] or not any(o["uid"] == DEPLOYMENT_UID for o in rs["metadata"].get("ownerReferences", [])):
        raise RuntimeError("refusing an unowned source Pod")
    volumes = {v["name"]: v for v in pods[0]["spec"]["volumes"]}
    mount = next((v for v in container["volumeMounts"] if v["mountPath"] == "/var/lib/postgresql/data"), None)
    if not mount or volumes[mount["name"]].get("persistentVolumeClaim", {}).get("claimName") != "kagent-postgresql":
        raise RuntimeError("unexpected source data volume")
    sql = ("SELECT json_build_object('tablespaces',(SELECT count(*) FROM pg_tablespace "
           "WHERE spcname NOT IN ('pg_default','pg_global')),'systemIdentifier',"
           "(SELECT system_identifier::text FROM pg_control_system()),"
           "'checksums',current_setting('data_checksums'),'pgdata',current_setting('data_directory'));")
    result = subprocess.run([*BUSINESS, "psql", "-X", "-qAt", "-w", "-U", "postgres", "-d", "kagent", "-c", sql],
                            env=ENV, capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError("cannot validate source PostgreSQL metadata")
    database = json.loads(result.stdout)
    if database["tablespaces"] != 0 or database["pgdata"] != PGDATA:
        raise RuntimeError("external PostgreSQL tablespaces or unexpected PGDATA")
    return {"deploymentUid": DEPLOYMENT_UID, "deploymentImage": container["image"],
            "replicas": 1, "deploymentSpecSha256": canonical_hash(spec),
            "pvcUid": PVC_UID, "pvUid": PV_UID, "pvName": PV_NAME, "nodeUid": node["metadata"]["uid"],
            "node": XL_NODE, "address": XL_ADDRESS, "hostpath": PVC_PATH,
            "selector": selector, "database": database}


def remote_command(offline, expected_device=None):
    script = ("ROOT=" + repr(PVC_PATH) + "\nNODE=" + repr(XL_NODE) +
              "\nMAX_SIZE=" + str(MAX_BYTES) + "\nOFFLINE=" + repr(offline) +
              "\nEXPECTED_DEVICE=" + repr(expected_device) + "\n" + REMOTE_AUDIT)
    return [*SSH, "sudo -n python3 -c " + shlex.quote(script)]


def prepare_manifest():
    prepare_state()
    identity = source_identity()
    result = subprocess.run(remote_command(False), env=ENV, capture_output=True, timeout=25)
    if result.returncode:
        (STATE / "offline-prepare-ssh.log").write_bytes(result.stdout + result.stderr)
        raise RuntimeError("strict-known-host XL hostpath audit failed")
    audit = json.loads(result.stdout)
    manifest = {"preparedAt": datetime.now(timezone.utc).isoformat(), "identity": identity,
                "sshAudit": audit, "maintenanceExecuted": False}
    PREPARED.write_text(json.dumps(manifest, indent=2))
    print("Helper prepared without maintenance:", PREPARED)


def guarded_scale(target, current, directory):
    d = api("get", "deployment", "kagent-postgresql", "-n", "kagent", "-o", "json", directory=directory)
    if d["metadata"]["uid"] != DEPLOYMENT_UID or d["spec"]["template"]["spec"]["containers"][0]["image"].split("@")[-1] != PINS["postgresBase"].split("@")[-1]:
        raise RuntimeError("business identity/image changed; refusing scale")
    if d["spec"]["replicas"] == target:
        return
    if d["spec"]["replicas"] != current:
        raise RuntimeError("unexpected concurrent business replica change")
    api("scale", "deployment/kagent-postgresql", "-n", "kagent",
        "--replicas=" + str(target), "--current-replicas=" + str(current),
        "--resource-version=" + d["metadata"]["resourceVersion"], "-o", "json", directory=directory)


def check_xl(directory):
    node = api("get", "node", XL_NODE, "-o", "json", directory=directory)
    if not node_ready(node):
        raise RuntimeError("XL became NotReady; no node/quorum/disk recovery attempted")


def wait_stopped(identity, directory, deadline):
    while time.monotonic() < deadline:
        check_xl(directory)
        pods = api("get", "pods", "-n", "kagent", "-l", identity["selector"], "-o", "json", directory=directory)
        if not pods["items"]:
            return
        time.sleep(1)
    raise RuntimeError("business Pod did not terminate within the shutdown budget")


def stream_offline(directory, deadline, expected_device):
    partial = directory / "postgres18.offline-pvc.tar.partial"
    with partial.open("xb") as target, (directory / "offline-copy.log").open("wb") as error:
        process = subprocess.Popen(remote_command(True, expected_device), env=ENV, stdout=target, stderr=error)
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise RuntimeError("offline archive exceeded the copy budget")
                check_xl(directory)
                time.sleep(1)
            if process.returncode:
                raise RuntimeError("strict-SSH offline copy failed; inspect protected offline-copy.log")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    archive = directory / "postgres18.offline-pvc.tar"
    partial.replace(archive)
    return archive


def maintenance_copy(directory, identity, expected_device=None):
    start = time.monotonic()
    attempted = False
    archive = None
    report = {"startedAt": datetime.now(timezone.utc).isoformat(), "originalReplicas": identity["replicas"],
              "maxSeconds": MAX_MAINTENANCE_SECONDS}
    try:
        attempted = True
        guarded_scale(0, identity["replicas"], directory)
        wait_stopped(identity, directory, min(start + 45, start + MAX_MAINTENANCE_SECONDS - 90))
        check_xl(directory)
        archive = stream_offline(directory, start + MAX_MAINTENANCE_SECONDS - 65, expected_device)
    finally:
        try:
            if attempted:
                for attempt in range(2):
                    try:
                        guarded_scale(identity["replicas"], 0, directory)
                        break
                    except Exception:
                        if attempt or time.monotonic() > start + MAX_MAINTENANCE_SECONDS - 28:
                            raise
                        time.sleep(1)
                report["replicasRestoredAt"] = datetime.now(timezone.utc).isoformat()
                report["desiredReplicasRestored"] = True
        finally:
            report["copyMaintenanceSeconds"] = round(time.monotonic() - start, 3)
            (directory / "maintenance.json").write_text(json.dumps(report, indent=2))
    deadline = start + MAX_MAINTENANCE_SECONDS - 10
    while time.monotonic() < deadline:
        d = api("get", "deployment", "kagent-postgresql", "-n", "kagent", "-o", "json", directory=directory)
        if d.get("status", {}).get("readyReplicas") == identity["replicas"] and d["spec"]["replicas"] == identity["replicas"]:
            report["readyAfterSeconds"] = round(time.monotonic() - start, 3)
            (directory / "maintenance.json").write_text(json.dumps(report, indent=2))
            return archive, report
        time.sleep(1)
    raise RuntimeError("original replica target restored, but business readiness exceeded 180 seconds")


def validate_archive(archive, expected_files):
    expected = {entry["path"]: entry["bytes"] for entry in expected_files}
    seen = {}
    with tarfile.open(archive, "r:") as source:
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "pgdata":
                raise ValueError("unsafe offline archive path")
            if not (member.isdir() or member.isfile()):
                raise ValueError("links/special files are forbidden in the offline archive")
            if member.isfile():
                if member.name in seen or member.size > MAX_BYTES:
                    raise ValueError("duplicate/oversized offline archive member")
                seen[member.name] = member.size
    if seen != expected or sum(seen.values()) > MAX_BYTES:
        raise ValueError("offline archive does not match the stopped-node file inventory")
    if "pgdata/PG_VERSION" not in seen or "pgdata/global/pg_control" not in seen:
        raise ValueError("offline archive lacks native PostgreSQL control files")
    if "pgdata/postmaster.pid" in seen:
        raise ValueError("offline archive still contains a live-server PID file")
    return {"regularFiles": len(seen), "regularBytes": sum(seen.values()), "safeExtractionPaths": True}


def restore_offline(directory, archive, identity, source_catalog, image):
    name = "memory-offline-restore-" + secrets.token_hex(5)
    policy, pod = restore_resources(name, image)
    apply(policy)
    try:
        apply(pod)
        kube("wait", "pod/" + name, "-n", NAMESPACE, "--for=condition=Ready", "--timeout=120s", timeout=140)
        remote = [*KUBE, "exec", "-i", "-n", NAMESPACE, name, "-c", "restore", "--"]
        copy_stream([*remote, "tar", "-xf", "-", "-C", "/var/lib/postgresql/data"],
                    archive, directory, "offline-restore-extract", timeout=180)
        control = command([*remote, "pg_controldata", PGDATA], directory, "offline-control")
        (directory / "pg-controldata.txt").write_bytes(control)
        fields = dict(line.split(":", 1) for line in control.decode().splitlines() if ":" in line)
        fields = {k.strip(): v.strip() for k, v in fields.items()}
        if fields.get("Database cluster state") != "shut down":
            raise AssertionError("offline archive is not a cleanly shut-down cluster")
        if fields.get("Database system identifier") != identity["database"]["systemIdentifier"]:
            raise AssertionError("restored cluster system identifier does not match the business source")
        checksums = fields.get("Data page checksum version")
        if checksums not in {"0", "1"}:
            raise AssertionError("unknown data-page checksum format")
        if checksums != "0":
            command([*remote, "pg_checksums", "--check", "-D", PGDATA],
                    directory, "offline-page-checksums", timeout=180)
        config = ("listen_addresses=''\nssl=off\nunix_socket_directories='/var/run/postgresql'\n"
                  "hba_file='/work/pg_hba.conf'\nident_file='/work/pg_ident.conf'\n"
                  "shared_buffers='128MB'\nmax_connections=40\n")
        for path, content in [("/work/postgresql.conf", config),
                              ("/work/pg_hba.conf", "local all postgres peer\n"), ("/work/pg_ident.conf", "")]:
            command([*remote, "sh", "-c", "umask 077; cat > " + path],
                    directory, "offline-local-config", data=content.encode())
        command([*remote, "pg_ctl", "-D", PGDATA, "-l", "/work/server.log",
                 "-o", "-c config_file=/work/postgresql.conf", "-w", "-t", "60", "start"],
                directory, "offline-restore-start", timeout=80)
        admin = [*remote, "psql", "-X", "-qAt", "-w", "--set=ON_ERROR_STOP=1", "-U", "postgres", "-d", "kagent"]
        restored_catalog = json.loads(command(admin, directory, "restored-role-acl-metadata",
                                              data=CATALOG_SQL.encode()))
        (directory / "restored-role-acl-metadata.json").write_text(json.dumps(restored_catalog, indent=2))
        if restored_catalog != source_catalog:
            raise AssertionError("physical restore roles/ownership/ACL/table metadata differs")
        rows = parse_inventory(command(admin, directory, "offline-table-counts", data=INVENTORY_SQL.encode()))
        (directory / "restored-table-counts.json").write_text(json.dumps(rows, indent=2))
        runtime = json.loads(command(admin, directory, "offline-runtime", data=(
            "SELECT json_build_object('listenAddresses',current_setting('listen_addresses'),"
            "'version',current_setting('server_version'));").encode()))
        if runtime["listenAddresses"] != "":
            raise AssertionError("offline restore must not expose TCP listeners")
        command([*remote, "pg_amcheck", "--database=kagent", "--username=postgres", "--no-password",
                 "--install-missing", "--heapallindexed", "--on-error-stop", "--jobs=1"],
                directory, "offline-amcheck", timeout=180)
        command([*remote, "pg_ctl", "-D", PGDATA, "-w", "-t", "60", "-m", "fast", "stop"],
                directory, "offline-restore-stop", timeout=80)
        return {"probe": name, "cleanShutdown": True, "systemIdentifierMatched": True,
                "pageChecksumVersion": checksums, "pageChecksumCheck": "passed" if checksums != "0" else "disabled in source",
                "nativeAmcheck": "passed", "rolesOwnershipAndAclsMatched": True,
                "catalogSha256": canonical_hash(restored_catalog), "socketOnly": True,
                "tablesRead": len(rows), "rowsCounted": sum(row["rows"] for row in rows), "runtime": runtime}
    finally:
        try:
            if require_owned("pod", name):
                result = subprocess.run([*KUBE, "exec", "-n", NAMESPACE, name, "--", "cat", "/work/server.log"],
                                        env=ENV, capture_output=True, timeout=20)
                (directory / "offline-restore-server.log").write_bytes(result.stdout + result.stderr)
        finally:
            try:
                remove("pod", name)
            finally:
                remove("networkpolicy", name)


def execute(admission_paused):
    if not admission_paused:
        raise RuntimeError("parent admission-pause confirmation is required; no maintenance performed")
    prepare_state()
    if PREPARED.is_symlink() or PREPARED.stat().st_uid != 0 or PREPARED.stat().st_mode & 0o077:
        raise RuntimeError("prepared source manifest must be a root-private owned file")
    prepared = json.loads(PREPARED.read_text())
    identity = source_identity()
    if prepared["identity"] != identity:
        raise RuntimeError("source changed since preparation; re-prepare and obtain parent confirmation")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    directory = STATE / "backups" / ("offline-" + stamp)
    directory.mkdir(parents=True, mode=0o700)
    report = {"startedAt": datetime.now(timezone.utc).isoformat(), "admissionPauseAttested": True,
              "source": identity, "healthyGate": False}
    def interrupted(signum, frame):
        raise KeyboardInterrupt("maintenance interrupted; restoring original replicas")
    old_handlers = {s: signal.signal(s, interrupted) for s in [signal.SIGTERM, signal.SIGINT]}
    try:
        source_catalog = json.loads(command(
            [*BUSINESS, "psql", "-X", "-qAt", "-w", "--set=ON_ERROR_STOP=1", "-U", "postgres", "-d", "kagent"],
            directory, "source-role-acl-metadata", data=CATALOG_SQL.encode(), timeout=30))
        (directory / "source-role-acl-metadata.json").write_text(json.dumps(source_catalog, indent=2))
        archive, report["maintenance"] = maintenance_copy(directory, identity, prepared["sshAudit"]["rootDevice"])
        with archive.open("r+b") as durable:
            os.fsync(durable.fileno())
        audit = json.loads((directory / "offline-copy.log").read_text().splitlines()[0])
        report["archiveValidation"] = validate_archive(archive, audit["files"])
        report["backup"] = {"path": str(archive), "bytes": archive.stat().st_size, "sha256": digest(archive)}
        image = json.loads((STATE / "image.json").read_text())
        reference = image["repository"] + "@" + image["digest"]
        report["restoreImage"] = reference
        report["restoreProof"] = restore_offline(directory, archive, identity, source_catalog, reference)
        if source_identity() != identity:
            raise AssertionError("business source binding/image/replicas changed")
        report["healthyGate"] = True
        report["completedAt"] = datetime.now(timezone.utc).isoformat()
    except BaseException as error:
        report["errorType"] = type(error).__name__
        report["error"] = str(error)
        raise
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        (directory / "report.json").write_text(json.dumps(report, indent=2))
        print("Protected offline backup report:", directory / "report.json")
    print("PASS: offline PVC backup and isolated physical restore; business replicas restored, probe removed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "execute"])
    parser.add_argument("--admission-paused", action="store_true")
    args = parser.parse_args()
    prepare_manifest() if args.action == "prepare" else execute(args.admission_paused)

# Private native-memory backend canary

This directory owns only `host-capabilities-memory-canary`-labelled resources in
`factory-executions` and generated state in `/home/nibeauli/kagent-lab/memory-canary`
(root-owned, mode 0700). It does **not** change the business PostgreSQL Deployment,
PVC, factory schema, Kagent configuration, host service, harness, or upstream checkout.
It is a backend prerequisite canary, not proof of actor-authenticated MemoryService
scope isolation; the parent must complete that separate integration gate.

## Immutable inputs and licensing

`pins.json` is authoritative:

| Input | Pin | License |
|---|---|---|
| Existing PostgreSQL Alpine 18.4 base | `postgres@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15` | PostgreSQL + upstream Alpine component licenses |
| pgvector v0.8.7 | `f37c13f68b57d2c3472b2214fbcff699d6d34876` | PostgreSQL |
| Official TEI `cpu-1.9` (resolved `cpu-1.9.4`) | `ghcr.io/huggingface/text-embeddings-inference@sha256:2538ea1c9640d3763b15af668039d24172d063b42337b0c27796fc2be180c78d` | Apache-2.0; image includes Intel MKL and OS dependencies under their licenses |
| TEI image source revision | `e80ef225ed0e6cb1717ce632a6a84b6cf211bb67` | Apache-2.0 |
| `BAAI/bge-base-en-v1.5` | `a5beb1e3e68b9ab74eb54cfd186867f64f240e1a` | MIT, per pinned model card |
| Native Kagent source | `eed27237a715fdbc7664aab9e1089c9a05097e71` | Apache-2.0 |

pgvector's official Dockerfile currently selects v0.8.7. Its source archive SHA-256
is pinned; the official build uses `make OPTFLAGS=""`. This build substitutes Alpine's
`build-base`, `clang21`, and `llvm21-dev` for Debian build packages because the exact
base's `pg_config` selects LLVM 21. Build dependencies stay in a discarded stage.
The installed extension retains `LICENSE` and `README.md` in `/usr/share/doc/pgvector`.
The final stage inherits the **same base, UID/GID 70, major version, environment,
entrypoint, command, exposed ports, and volume metadata**; `build.py` checks this.
Never replace existing Alpine data with a Debian pgvector image.

Sources: [pgvector](https://github.com/pgvector/pgvector),
[official TEI CPU/local-path and air-gap instructions](https://github.com/huggingface/text-embeddings-inference),
[pinned model card](https://huggingface.co/BAAI/bge-base-en-v1.5/blob/a5beb1e3e68b9ab74eb54cfd186867f64f240e1a/README.md).

## Build, install, verify

Run from this directory in the existing Ubuntu-26.04 WSL host, as root:

```powershell
wsl.exe -d Ubuntu-26.04 --user root --exec bash -lc 'cd /mnt/q/src/tools/HostCapabilities/deployment/memory && export PYTHONDONTWRITEBYTECODE=1 && python3 -m unittest -v && python3 prepare.py && python3 build.py && python3 install.py install'
wsl.exe -d Ubuntu-26.04 --user root --exec bash -lc 'cd /mnt/q/src/tools/HostCapabilities/deployment/memory && PYTHONDONTWRITEBYTECODE=1 python3 verify.py'
```

For the bounded exact native-client wire-contract proof only, without re-running
migrations, network probes, or changing either healthy workload:

```powershell
wsl.exe -d Ubuntu-26.04 --user root --exec bash -lc 'cd /mnt/q/src/tools/HostCapabilities/deployment/memory && PYTHONDONTWRITEBYTECODE=1 python3 verify.py --native-client-only'
```

This sends exactly `{"model":"BAAI/bge-base-en-v1.5","input":"Paris is the capital of France.","dimensions":768}`
with `Authorization: Bearer kagent-credential-injected`, **omitting** `encoding_format`.
Acceptance requires HTTP 200 and an OpenAI-shaped response containing 768 finite,
nonzero numeric values. Its separate protected evidence is `native-client-contract.json`;
an HTTP rejection retains the actual public-fixture error in `native-client-request.log`.
The bearer value is an inert caller-supplied placeholder, not a credential or proof
that TEI authenticates actor identities.
Recorded 2026-11-03 23:41 UTC: the exact body/header above returned HTTP 200,
the expected model/list envelope, 768 finite floats, and usage 9/9 prompt/total
tokens. The targeted wire-contract unit test also passed. No workload or database
change was made for this precision check.

No pip/npm dependencies or feeds are used. The Dockerfile is the explicit build
dependency manifest. Public downloads have 600-second deadlines, no credential
access, and no inference inputs. Safetensors and ONNX weight SHA-256 values come
from the pinned official model repository's LFS metadata; all downloaded file
SHA-256 values are retained in protected `downloads.json`. Model files are served
from a read-only local path with `HF_HUB_OFFLINE=1`, telemetry disabled, and default
deny **all egress**. There is no runtime download/init network exception.

The existing private OCI exporter, publisher, and local containerd cache are reused
read-only from `Q:\tools\kagent-lab`. Only repository `kagent-lab/memory-postgres`
is published. Its final immutable reference is written to protected `image.json`.
Registry transport is the existing loopback Kubernetes TLS port-forward path to
the private ClusterIP registry, not a new exposed registry or relaxed TLS setting.

The isolated PG uses `emptyDir` only, with precisely
`PGDATA=/var/lib/postgresql/data/pgdata`, mount `/var/lib/postgresql/data`,
`fsGroup: 70`. Its generated password is a dedicated ephemeral Secret; it is never
printed or written to source. No database Service, existing PVC, or business
credentials are used. The native CLI is copied from the installed prepared artifact,
its embedded clean VCS revision is checked against the pinned source, its SHA-256
is recorded, and its real migration `up/status/up` runs inside the canary
over a local Unix socket as `canary_owner`, without transmitting a password.

Installation has bounded readiness waits and captures protected diagnostics before
removing unhealthy owned workloads. `verification.json` records actual results.
`verify.py` uses **only public geography/astronomy facts** and checks OpenAI API
shape, exactly 768 finite nonzero floats, real insert/cosine nearest-neighbor search,
native migrations/idempotency, invalid dimensions, read/write-role DDL denial, and
blocked public egress. Unit tests use synthetic contract fixtures only, never live
inference; no padded or fallback embeddings exist.

## Network, model and resource boundaries

Endpoint:
`http://local-embeddings.factory-executions.svc.cluster.local:8080/v1`.
Use model `BAAI/bge-base-en-v1.5`, 768 dimensions, float encoding. Explicit CLS pooling
matches this BGE model. Inputs over the supported token budget are rejected rather
than silently truncated. OpenAI usage token counts are local metadata, not billing.
No Azure deployment, key read, or inference call is made; an existing resource
metadata-only inventory did not establish an approved owned embedding deployment.

The Service is ClusterIP only: no Ingress, NodePort, LoadBalancer, hostNetwork,
hostPort, or public host binding. All owned pods deny ingress/egress by default.
Only pods labelled `app=atenet-egress` in namespace `ate-system` can enter the TEI
pod on TCP 8080. Existing egress infrastructure is not edited. Pod probe/exec
traffic is Kubernetes administration, not an exposed inference endpoint.

HTTP here is an **explicit private-cluster exception**, not general public HTTP
permission or certificate-validation bypass. Parent harness configuration must
allow exactly this internal origin if choosing direct TEI integration, and native
actor egress policy must permit this destination. Do not send company content to
the public Hugging Face API/hub. Public network access is limited to host-side
artifact/image acquisition; every actual inference runs within the private cluster.

Resources (one replica each, pinned to existing local node `cpc-nibea-8t76e`):

| Workload | CPU request / limit | Memory request / limit |
|---|---|---|
| TEI CPU | 1 / 2 | 1Gi / 2Gi |
| Isolated PG | 100m / 1 | 256Mi / 1Gi |
| Cache loader, bounded then deleted | 50m / 500m | 64Mi / 256Mi |

Steady state total: **1.1 CPU / 1.25Gi requested, 3 CPU / 3Gi limited**; no new
inference billing. Model cache claims 2Gi on existing
`microk8s-hostpath-devdrive` (750GB host backing), about 835Mi of pinned weights
and tokenizer/configuration. It uses both original safetensors and official ONNX
for supported CPU backend loading, not model conversion or a new database product.
Host preparation and OCI build cache consume additional existing-host disk.

Remove only owned canary workloads with `python3 install.py remove`. Policies and
the owner-labelled cache PVC are intentionally retained. Delete that exact PVC
only after checking its ownership, pod references, and retained hostpath reclaim
semantics; do not delete the StorageClass, PV roots, or shared cache directories.

Validated private PostgreSQL image produced by this run:
`10.152.183.131:5000/kagent-lab/memory-postgres@sha256:4172ce00709b7cece0e6390cae8db5c00ecef4120965b43ced14e3dc7f73e273`.
The generated `image.json` is the authoritative output after a deliberate rebuild;
Alpine repository package updates can change build-stage bytes without changing
the pinned final base or pgvector source.

### Recorded acceptance

Verified 2026-11-03 23:31 UTC: both isolated Deployments are Ready with zero
restarts; PostgreSQL 18.4/pgvector 0.8.7; five offline unit tests passed. Four
public inputs produced finite 768D embeddings, and a paraphrased France-capital
query retrieved the Paris fixture. The clean pinned native CLI applied and
rechecked core/vector migrations (`vector_schema_migrations` version 1), including
the HNSW cosine index. Wrong dimensions and read/write-role DDL were rejected with
their expected SQLSTATEs. Direct public TCP egress and unauthorized pod ingress
timed out; the latter probe had an explicit destination egress grant, isolating
the ingress denial. Probe/loader pods were removed, while the healthy canary remains.
Business PostgreSQL still referenced the original stock image at that timestamp.
The parent subsequently completed protected offline backup/physical restore
acceptance and activated the verified same-base extension image; see
`Q:\src\environment-map\IMPLEMENTATION-HANDOFF.md` for current production state.

The protected `verification.json` is the latest machine-readable evidence; rerun
verification before a later cutover. These backend checks deliberately do not
claim a positive actor/egress-to-Service integration test or native API scope proof.

## Parent-only business cutover: not executed here

### Protected backup and disposable restore gate

#### Parent-gated offline alternative (prepare first; no automatic maintenance)

`offline_pvc_backup.py prepare` performs read-only identity/readiness checks and
writes protected `offline-pvc-prepared.json`. It does **not** scale, archive, or
restore anything. Preparation pins the existing CLI-managed Deployment UID,
original replicas/image, PVC/PV UIDs and binding, node UID/address, exact hostpath,
filesystem device, native cluster identifier, and PostgreSQL metadata. Its SSH
uses the existing user's known-host file with strict checking and noninteractive
sudo; it neither provisions access nor accepts a new host key.

Only after the parent confirms all nine factory admission units and autonomous
timer are paused:

```powershell
wsl.exe -d Ubuntu-26.04 --user root --exec bash -lc 'cd /mnt/q/src/tools/HostCapabilities/deployment/memory && PYTHONDONTWRITEBYTECODE=1 python3 offline_pvc_backup.py execute --admission-paused'
```

The explicit flag is the parent's pause attestation, not an instruction for this
helper to stop or restart admission services. Missing confirmation is refused before
any maintenance work. Source changes after preparation require fresh preparation
and parent coordination.

The **only** business mutation is guarded scaling of `kagent/kagent-postgresql`
from its recorded replica count to zero and back in `finally`. It waits at most
45 seconds for ordinary pod termination, refuses NotReady XL, and makes a read-only
strict-SSH tar stream from:

```text
XL cpc-nibea-2l35i / 10.82.0.3
/mnt/microk8s-hostpath/kagent-kagent-postgresql-pvc-de1298c7-2717-4c29-bc0e-d3bc971a9465
```

That PVC must contain only `pgdata`, with PostgreSQL major 18 and owner UID 70.
Unexpected/rebound/unowned resources, placement, realpaths, filesystems, external
tablespaces, links/special files, live PID files after termination, or payloads
over the 2GiB disposable-restore budget are rejected. No PVC data is changed,
deleted, formatted, remounted, or recovered. No business image/schema/`pg_hba`/
Service change or authentication relaxation is made.

The copy deadline reserves at least 65 seconds of the **180-second maintenance
budget** for restoring the original replica target/readiness. Desired downtime is
under two minutes. Replica restoration is attempted immediately after copy/failure,
with bounded safe retry on a transient scale precondition/API failure; signal
interruption runs the same `finally`. Node loss causes abort/restoration of the
desired count, **not** quorum or disk work. Actual readiness cannot be guaranteed
under node/control-plane loss; failures remain explicit protected blockers, not
permission to recover a node. Do not forcibly SIGKILL the maintenance helper.

Only **after business replicas are restored** does it hash/validate the archive and
launch a unique network-denied emptyDir restore Pod using the verified pgvector
image. It checks safe extraction paths and stopped-node file sizes, native
`pg_controldata` clean shutdown/cluster identity, native page checksums when enabled,
Unix-socket-only startup, exact role/membership/ownership/ACL/catalog metadata, table
readability/counts, and native `pg_amcheck`. The clone may install `amcheck` locally
for verification; nothing is installed into the business DB. It then removes only
its unique probe and NetworkPolicy. It never mounts the business PVC into the probe.

Protected `backups/offline-<timestamp>/report.json`, `maintenance.json`, archive,
SHA-256, native diagnostics, and catalog/count inventories remain root-private.
Role metadata stays in protected files and is never printed. This offline archive
has no `pg_basebackup` backup manifest: it is instead a clean-shutdown directory copy
with SHA-256, archive-member checks and native data checks. As with the online backup,
external Kubernetes/TLS/configuration artifacts and original network authentication
are separate full-DR requirements.

Run `python3 backup_restore.py` from this directory as root in the existing WSL
environment (`PYTHONDONTWRITEBYTECODE=1`). It creates a unique timestamped directory
under `/home/nibeauli/kagent-lab/memory-canary/backups`, with directory mode 0700,
backup/report/log files mode 0600, and no data or credentials in terminal output.
The source Deployment is read/hashed before and after the operation, never patched.
Do not treat any `.partial` file as a usable backup.

- Physical: native business-Pod `pg_basebackup` over its existing Unix socket,
  tar format, WAL method `fetch`, spread checkpoint, 32MiB/s rate bound, SHA-256
  backup manifest, and bounded execution. It does not create a replication slot.
  Replication authentication failures remain blockers; no `pg_hba`, role, service,
  image, or business schema permission is relaxed.
- Logical: native `pg_dump` custom format with `--no-owner --no-acl`, using the
  same exported repeatable-read snapshot as the protected per-table row-count
  inventory. No table rows are printed. This is a schema/data recovery artifact,
  **not** an owner/ACL/global-role disaster-recovery backup.
- Restore: a uniquely named owner-labelled Pod, **new private emptyDirs only**,
  verified same-base pgvector image, fsGroup/UID 70, no Service, and default-deny
  ingress/egress. Native `pg_verifybackup` checks physical manifest/files and WAL
  before startup. The clone uses a new local-only config (`listen_addresses=''`,
  Unix socket peer authentication); business configs are not changed. All expected
  tables must be readable in the physical copy. The logical archive restores into
  a different newly created database in this disposable clone, and every table's
  count must exactly match the exported source snapshot.
- Cleanup removes only that unique restore Pod and its NetworkPolicy; existing
  canaries, model PVC, business volumes, backups, inventories, and diagnostics remain.
  `report.json` records exact backup paths, sizes, SHA-256, restore results and
  cleanup. If physical backup is denied, the script can still prove logical restore
  in an initialized new clone, but the **physical gate stays blocked**.

Physical and logical backups are separate consistent points in time, so concurrent
business writes can legitimately change physical-versus-logical row counts. The
logical comparison uses a shared snapshot to avoid that ambiguity. Physical backup
preserves cluster data/global roles (including sensitive stored authentication
material), but external ConfigMaps, TLS keys/certificates, mounted trust bundles,
Kubernetes Secrets/PVC definitions and client connectivity are **not** included.
The socket-only restore intentionally does not prove original TLS/authentication,
owner/ACL semantics of the logical archive, application/API recovery, corruption-free
meaning of every value, or recovery of databases other than the checked `kagent` DB.
Retain backups under approved private access/retention controls; parent must separately
capture external operational configuration for full DR.

Recorded 2026-11-03 23:51 UTC:

- Physical backup **blocked**: existing socket replication authentication reports
  `no pg_hba.conf entry for replication connection from host "[local]"`.
  No authentication/configuration change was attempted; the zero-byte
  `postgres18.base.tar.partial` is **not a backup**. Consequently the physical
  extraction/manifest/WAL restore branch remains unvalidated and the physical
  cutover gate remains closed.
- Logical archive:
  `/home/nibeauli/kagent-lab/memory-canary/backups/20261103T235010Z-b30f9a/kagent.no-owner-no-acl.dump`,
  **2,231,335 bytes**, SHA-256
  `3856d8e89aecc64f76377aca11fc7ebcfac2595d760f7853646880c3d816e5c8`.
  It restored successfully into a fresh socket-only PostgreSQL 18.4 database:
  **62 tables, 2,171 rows**, with every per-table count matching the same exported
  source snapshot. Archive hash and 0700-directory/0600-file permissions were
  rechecked after restore.
- This directory's `report.json`, source/restored inventories and
  `pg-basebackup.log` retain protected evidence. The unique restore Pod and
  NetworkPolicy were removed. Business Deployment fingerprints matched before/
  after; existing healthy canaries were not changed. Two targeted restore-boundary
  unit tests passed, and the exact native embedding request proof was refreshed
  successfully (HTTP 200, 768 finite floats, inert bearer, no encoding override).

1. Stop/coordinate native memory writers. Record exact current Deployment image,
   pod security context, `PGDATA`, volume mounts, owner/readwrite roles, schema,
   Kagent image/config, and migration versions. Take protected logical **and**
   volume-level backups with a verified restore procedure before any live change.
2. Roll **only** the business PG image to the verified canary image digest in
   protected `image.json`, retaining all other pod arguments, entrypoint, UID 70,
   major 18, TLS material, `PGDATA`, and existing data mounts unchanged.
   This directory deliberately supplies no automatic live-image patch command.
3. As the database administrator, over the existing verified TLS transport, run
   the equivalent of the following reviewed transaction, using actual existing
   factory roles (examples below match the prepared factory deployment):

   ```sql
   BEGIN;
   CREATE SCHEMA IF NOT EXISTS extensions;
   REVOKE CREATE ON SCHEMA extensions FROM PUBLIC;
   CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
   SELECT n.nspname, e.extversion
     FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
     WHERE e.extname='vector';
   -- Abort if the extension is already installed in a different schema.
   GRANT USAGE ON SCHEMA extensions TO factory_owner, factory_readwrite;
   GRANT USAGE ON TYPE extensions.vector TO factory_owner, factory_readwrite;
   COMMIT;
   ```

4. Configure the native controller/CLI explicitly:

   ```yaml
   database:
     schema: factory
     vectorSchema: extensions
     vectorEnabled: true
   ```

   Retain existing `database.owner.role: factory_owner`, owner URL-file, read/write
   role/URL-file and verified TLS/channel-binding settings. Neither enabling
   vectors nor creating a table grants the expected role/schema privileges.
   Run the **matching native Kagent binary** with the protected config and existing
   owner connection URL file, never a secret on the command line:

   ```sh
   kagent --config "$PROTECTED_NATIVE_CONFIG" db migrate status \
     --db-role factory_owner --db-url-file "$PROTECTED_OWNER_URL_FILE"
   kagent --config "$PROTECTED_NATIVE_CONFIG" db migrate up \
     --db-role factory_owner --db-url-file "$PROTECTED_OWNER_URL_FILE"
   ```

   Native migrations create `factory.memory`, the 768D column and HNSW cosine
   index, and `factory.vector_schema_migrations`. Do not manually precreate the
   business memory table or substitute a different migration tracker.
5. Parent must separately prove authenticated memory store/search with creator/
   Agent isolation, BYO configuration, explicit embedding destination policy and
   actual retrieval quality. This canary does not authorize live cutover.

Rollback: first disable native/BYO memory callers and revert the controller's
vector-enabled configuration. Preserve new memory rows and migration versions.
Do **not** drop the extension, schema, tables, or indexes as an automatic rollback.
The extended image is base-compatible, so leaving it installed with vectors
disabled is the least-destructive rollback. If reverting the PG image is necessary,
parent must verify there are no vector-dependent consumers and that catalog/library
dependencies will not be queried, or restore the pre-cutover backup into a separate
validated recovery target. `kagent db migrate down --source vector` is destructive
and is not an ordinary rollback procedure.

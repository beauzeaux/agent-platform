# Private bespoke-session archive

The existing trusted factory controller hosts an additive, runtime-authenticated
archive RPC. Actors upload through their native localhost bridge, never receive
object-store credentials, and cannot choose another actor's session or creator.
This is not a host interactive-session crawler or an OTel payload pipeline.

`manage.py prepare` creates protected configuration and the separate private
`bespoke-session-archives` bucket. `apply --image <verified-private-digest>`
validates and hashes a fresh protected PostgreSQL dump, applies additive schema,
mounts an owned Secret, permits controller-to-RustFS traffic, and rolls only
`factory-controller`. Pause owned admission with the maintenance helper and
always restore its saved activity in a shell EXIT trap.

Full sanitized logical SDK events/results are compressed JSONL. A separate
canonical manifest records coverage/gaps and completeness. The PostgreSQL index
deduplicates `(session_id, checkpoint_hash)`; changed completeness for identical
event bytes produces a distinct checkpoint with the same content object. Only
an explicit `exported` acknowledgement retires the actor's durable outbox.
Failed uploads are retryable, but accepted business work is never replayed.

Current bounds: cumulative uploads over 4 MiB remain visibly pending (no chunks
or cursor API yet). Controller credentials currently reuse the trusted RustFS
infrastructure principal; bucket-scoped credentials, retention, and independent
durable backups remain production-hardening work. Do not copy protected config,
dump contents, archived messages, or signing material into source control.

`verify_objects.go` uses the already-cached standard AWS SDK in the prepared
factory controller Go module. It reads only this bucket, validates compressed
JSONL/event counts, content hashes, canonical manifest hashes and index agreement,
and prints metadata only. Its two arguments are the existing private RustFS
ClusterIP origin and a protected JSON array of selected index rows
(`session_id`, `content_hash`, `checkpoint_hash`, `object_key`, `manifest_key`,
`event_count`, `complete`). Credentials are loaded from protected host files,
never CLI values; no controller rebuild or new dependency restoration is needed.

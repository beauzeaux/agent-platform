CREATE TABLE IF NOT EXISTS factory.session_archives (
    session_id text NOT NULL,
    creator_hash text NOT NULL,
    task_id text NOT NULL,
    content_hash text NOT NULL,
    checkpoint_hash text NOT NULL,
    object_key text NOT NULL,
    manifest_key text NOT NULL,
    event_count integer NOT NULL CHECK (event_count > 0),
    complete boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, checkpoint_hash)
);
ALTER TABLE factory.session_archives OWNER TO factory_owner;
GRANT SELECT, INSERT, UPDATE ON factory.session_archives TO factory_readwrite;

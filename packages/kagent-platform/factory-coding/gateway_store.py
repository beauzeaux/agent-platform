"""Minimal PostgreSQL queue for one logical A2A agent."""
import hashlib
import json
import re
import time

import psycopg
from psycopg.rows import dict_row

TERMINAL = {"completed", "failed", "canceled"}
ACTIVE = {"dispatching", "active", "uncertain"}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Conflict(Exception):
    pass


class Store:
    def __init__(self, config, maximum=3, backlog=100, gateway="coding", task_prefix="coding"):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}",gateway):
            raise ValueError("invalid gateway name")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,20}",task_prefix):
            raise ValueError("invalid task prefix")
        self.config, self.maximum, self.backlog = config, maximum, backlog
        self.gateway, self.task_prefix = gateway, task_prefix
        self.scheduler = None

    def connection(self):
        options = dict(self.config)
        role = options.pop("role")
        if not re.fullmatch(r"[a-z_][a-z0-9_]*",role):
            raise ValueError("invalid PostgreSQL role")
        db = psycopg.connect(**options, row_factory=dict_row)
        db.execute("SET ROLE "+role)
        return db

    def acquire_scheduler(self):
        self.scheduler = self.connection()
        lock_key = int.from_bytes(
            hashlib.sha256(("factory-gateway:"+self.gateway).encode()).digest()[:8],
            "big",
            signed=True,
        )
        locked = self.scheduler.execute(
            "SELECT pg_try_advisory_lock(%s)", (lock_key,)
        ).fetchone()["pg_try_advisory_lock"]
        if not locked:
            self.scheduler.close()
            self.scheduler = None
            raise RuntimeError("another factory gateway scheduler is active")
        self.scheduler.commit()

    def close(self):
        if self.scheduler:
            self.scheduler.close()
            self.scheduler = None

    @staticmethod
    def record(row):
        if row is None:
            return None
        value = dict(row)
        value["created"] = value["created"].timestamp()
        value["updated"] = value["updated"].timestamp()
        value["available_at"] = value["available_at"].timestamp()
        return value

    def get(self, identifier, owner=None):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM factory.gateway_tasks WHERE gateway=%s AND id=%s",
                (self.gateway,identifier),
            ).fetchone()
        task = self.record(row)
        return task if task and (owner is None or task["owner"] == owner) else None

    def enqueue(self, owner, request_id, context_id, request, runtime_identity):
        digest = hashlib.sha256(encoded([context_id, request, runtime_identity]).encode()).hexdigest()
        identifier = self.task_prefix+"-"+hashlib.sha256(
            encoded([self.gateway,owner,request_id]).encode()
        ).hexdigest()[:40]
        with self.connection() as db:
            old = db.execute(
                """SELECT * FROM factory.gateway_tasks
                   WHERE gateway=%s AND owner=%s AND request_id=%s FOR UPDATE""",
                (self.gateway,owner,request_id),
            ).fetchone()
            if old:
                if old["digest"] != digest:
                    raise Conflict("request identity conflicts")
                return self.record(old)
            pending = db.execute(
                """SELECT count(*) AS count FROM factory.gateway_tasks
                   WHERE gateway=%s AND state NOT IN ('completed','failed','canceled')""",
                (self.gateway,),
            ).fetchone()["count"]
            if pending >= self.backlog:
                raise Conflict("gateway backlog full")
            db.execute(
                """INSERT INTO factory.gateway_tasks
                   (gateway,id,owner,request_id,context_id,digest,request,state)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,'queued')""",
                (self.gateway,identifier,owner,request_id,context_id or identifier,digest,encoded(request)),
            )
        return self.get(identifier)

    def update(self, identifier, **values):
        allowed = {
            "state", "session", "actor_uid", "native_task", "attempted",
            "cancel_requested", "result", "diagnostic", "available_at",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("invalid store mutation")
        assignments, arguments = [], []
        for key, value in values.items():
            if key == "result":
                assignments.append("result=%s::jsonb")
                arguments.append(encoded(value) if value is not None else None)
            elif key == "available_at":
                assignments.append("available_at=to_timestamp(%s)")
                arguments.append(value)
            else:
                assignments.append(key+"=%s")
                arguments.append(value)
        assignments.append("updated=now()")
        with self.connection() as db:
            db.execute(
                "UPDATE factory.gateway_tasks SET "+",".join(assignments)+" WHERE gateway=%s AND id=%s",
                (*arguments,self.gateway,identifier),
            )
        return self.get(identifier)

    def session_for_context(self, task):
        with self.connection() as db:
            row = db.execute(
                """SELECT session FROM factory.gateway_tasks
                   WHERE gateway=%s AND owner=%s AND context_id=%s AND id<>%s AND session IS NOT NULL
                   ORDER BY created LIMIT 1""",
                (self.gateway,task["owner"],task["context_id"],task["id"]),
            ).fetchone()
        return row["session"] if row else None

    def active(self):
        with self.connection() as db:
            rows = db.execute(
                """SELECT * FROM factory.gateway_tasks
                   WHERE gateway=%s AND state IN ('dispatching','active','uncertain')
                   ORDER BY created,id""",
                (self.gateway,),
            ).fetchall()
        return [self.record(row) for row in rows]

    def claim(self):
        with self.connection() as db:
            occupied = db.execute(
                """SELECT count(*) AS count FROM factory.gateway_tasks
                   WHERE gateway=%s AND state IN ('dispatching','active','uncertain')""",
                (self.gateway,),
            ).fetchone()["count"]
            if occupied >= self.maximum:
                return None
            row = db.execute(
                """SELECT * FROM factory.gateway_tasks candidate
                   WHERE gateway=%s AND state='queued' AND available_at<=now()
                   AND NOT EXISTS (
                     SELECT 1 FROM factory.gateway_tasks active
                     WHERE active.id<>candidate.id
                     AND active.gateway=candidate.gateway
                     AND active.owner=candidate.owner
                     AND active.context_id=candidate.context_id
                     AND active.state IN ('dispatching','active','uncertain')
                   )
                   ORDER BY created,id FOR UPDATE SKIP LOCKED LIMIT 1""",
                (self.gateway,),
            ).fetchone()
            if not row:
                return None
            db.execute(
                """UPDATE factory.gateway_tasks SET state='dispatching',updated=now()
                   WHERE gateway=%s AND id=%s""",
                (self.gateway,row["id"]),
            )
        return self.get(row["id"])

    def cancel(self, identifier, owner):
        with self.connection() as db:
            row = db.execute(
                """SELECT * FROM factory.gateway_tasks
                   WHERE gateway=%s AND id=%s AND owner=%s FOR UPDATE""",
                (self.gateway,identifier,owner),
            ).fetchone()
            if row is None:
                raise KeyError(identifier)
            if row["state"] not in TERMINAL:
                state = "canceled" if row["state"] == "queued" else row["state"]
                db.execute(
                    """UPDATE factory.gateway_tasks
                       SET cancel_requested=true,state=%s,updated=now()
                       WHERE gateway=%s AND id=%s""",
                    (state,self.gateway,identifier),
                )
        return self.get(identifier, owner)

    def list(self, owner=None):
        with self.connection() as db:
            if owner:
                rows = db.execute(
                    """SELECT * FROM factory.gateway_tasks
                       WHERE gateway=%s AND owner=%s ORDER BY created,id""",
                    (self.gateway,owner),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM factory.gateway_tasks WHERE gateway=%s ORDER BY created,id",
                    (self.gateway,),
                ).fetchall()
        return [self.record(row) for row in rows]

"""Protect host broker state without exporting credentials to the cluster."""
import argparse
import csv
from contextlib import contextmanager
import io
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@contextmanager
def configuration_lock(state):
    import msvcrt
    with (state / "configuration.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"\0")
            lock.flush()
        deadline = time.monotonic() + 60
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Protected binding update is busy.")
                time.sleep(0.1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor-uid", required=True)
    parser.add_argument("--subject")
    parser.add_argument("--profile")
    parser.add_argument("--git-author-name")
    parser.add_argument("--git-author-email")
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f-]{36}", args.actor_uid):
        raise ValueError("Invalid owned actor UID.")
    profiles = json.loads((ROOT / "profiles.json").read_text())
    state = ROOT / "state"
    state.mkdir(exist_ok=True)
    output = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
    sid = next(csv.reader(io.StringIO(output)))[1]
    if not re.fullmatch(r"S-\d+(?:-\d+)+", sid):
        raise ValueError("Cannot identify current Windows principal.")
    subprocess.run(["icacls", str(state), "/inheritance:r", "/grant:r",
                    f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"],
                   check=True, capture_output=True)
    with configuration_lock(state):
        configure(args, profiles, state, sid)


def configure(args, profiles, state, sid):
    target = state / "broker.json"
    settings = json.loads(target.read_text()) if target.exists() else {"bridgeKey": secrets.token_hex(32)}
    settings.pop("actorUid", None)
    settings.setdefault("actors", {})
    settings.setdefault("revokedActors", [])
    if args.revoke:
        settings["actors"].pop(args.actor_uid, None)
        if args.actor_uid not in settings["revokedActors"]:
            settings["revokedActors"].append(args.actor_uid)
    else:
        if args.actor_uid in settings["revokedActors"]:
            raise ValueError("Actor authorization was revoked; explicit host review is required.")
        existing = settings["actors"].get(args.actor_uid, {})
        args.subject = args.subject or existing.get("subject", "actor/proxy-access/engsys-agency-coder")
        existing = existing or next((actor for actor in settings["actors"].values() if actor["subject"] == args.subject), {})
        args.profile = args.profile or existing.get("profile", "engsys-contributor")
        if args.profile not in profiles or not re.fullmatch(r"actor/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", args.subject):
            raise ValueError("Unknown profile or invalid actor subject.")
        for uid, actor in list(settings["actors"].items()):
            if uid != args.actor_uid and actor["subject"] == args.subject:
                del settings["actors"][uid]
        identity = existing.get("gitIdentity") or next(
            (actor["gitIdentity"] for actor in settings["actors"].values()
             if actor.get("profile") == args.profile and actor.get("gitIdentity")), {})
        name = args.git_author_name or identity.get("name") or subprocess.check_output(["git", "config", "--global", "user.name"], text=True).strip()
        email = args.git_author_email or identity.get("email") or subprocess.check_output(["git", "config", "--global", "user.email"], text=True).strip()
        if not name or len(name) > 200 or "\n" in name or "\r" in name or not re.fullmatch(r"[^<>\s@]+@[^<>\s@]+", email):
            raise ValueError("A valid host-approved Git author identity is required.")
        settings["actors"][args.actor_uid] = {"subject": args.subject, "profile": args.profile,
                                            "gitIdentity": {"name": name, "email": email}}
    settings["listenAddress"] = "127.0.0.1"
    temporary = state / "broker.json.tmp"
    temporary.write_text(json.dumps(settings), encoding="utf-8")
    os.replace(temporary, target)
    subprocess.run(["icacls", str(target), "/inheritance:r", "/grant:r",
                    f"*{sid}:F", "*S-1-5-18:F", "*S-1-5-32-544:F"], check=True, capture_output=True)
    public = ROOT / "public"
    public.mkdir(exist_ok=True)
    temporary = public / "actors.json.tmp"
    temporary.write_text(json.dumps({
        "actors": {uid: {"subject": actor["subject"]} for uid, actor in settings["actors"].items()}
    }), encoding="utf-8")
    os.replace(temporary, public / "actors.json")
    print("Protected host broker binding revoked." if args.revoke else "Protected host broker state bound to the owned actor UID.")


if __name__ == "__main__":
    main()

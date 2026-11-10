"""Dry-run-first reset of one exact SlopMop demo campaign."""
import argparse
import json
from pathlib import Path
import re
import subprocess

from backend import Backend, ENV, SPACE
from gateway_store import Store, TERMINAL
from swarm_manager import SwarmManager
from swarm_roles import ROLES

STATE=Path("/home/nibeauli/kagent-lab/factory-coding")
WINDOWS_GIT="/mnt/c/Program Files/Git/cmd/git.exe"
REPOSITORY=r"Q:\src\engsys"
TARGET="users/nibeauli/slopmop-devel"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("campaign")
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--baseline")
    parser.add_argument("--confirm")
    args=parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]{3,48}",args.campaign):
        raise ValueError("invalid campaign")
    manager_config=json.loads((STATE/"service-swarm-manager.json").read_text())
    manager=Store(manager_config["postgres"],1,20,"swarm-manager","campaign")
    matches=[task for task in manager.list() if (task["result"] or {}).get("campaign")==args.campaign]
    if len(matches)!=1:
        raise RuntimeError("expected exactly one owned campaign")
    campaign=matches[0]
    manifest=campaign["result"] or {}
    children=manifest.get("children",{})
    event_children={event["task"]:event["role"] for event in manifest.get("events",[])
        if event.get("event")=="child_submitted" and event.get("task") and event.get("role")}
    tasks={}
    sessions={}
    actors={}
    for role,definition in ROLES.items():
        config_path=STATE/("service.json" if role=="coding" else "service-"+role+".json")
        config=json.loads(config_path.read_text())
        store=Store(config["postgres"],config["maximum"],config["backlog"],
            config["gatewayName"],config["taskPrefix"])
        role_ids={child["task"] for child in children.values() if child.get("role")==role}
        role_ids.update(task for task,event_role in event_children.items() if event_role==role)
        rows=[store.get(identifier) for identifier in role_ids]
        tasks[role]=[row["id"] for row in rows if row]
        sessions.update({row["session"]:config for row in rows if row and row["session"]})
        actors.update({row["actor_uid"]:(config,row) for row in rows if row and row["actor_uid"]})
        store.close()
    branches=sorted(collect_branches(manifest,args.campaign))
    prs=SwarmManager.produced_pr_urls(manifest)
    plan={"campaign":args.campaign,"campaignTask":campaign["id"],"state":campaign["state"],
        "childTasks":tasks,"sessions":sorted(sessions),
        "branches":branches,"pullRequests":prs,"target":TARGET}
    print(json.dumps(plan,indent=2))
    if not args.apply:
        return
    if args.confirm!=TARGET or not args.baseline or not re.fullmatch(r"[0-9a-f]{40}",args.baseline):
        raise RuntimeError("apply requires exact target confirmation and baseline commit")
    if campaign["state"] not in TERMINAL:
        raise RuntimeError("campaign is not terminal")
    if any(not branch.startswith("users/nibeauli/slopmop-demo-"+args.campaign+"-") for branch in branches):
        raise RuntimeError("campaign branch ownership mismatch")
    for pr_url in prs:
        result=subprocess.run([manager_config["windowsPython"],
            r"Q:\tools\kagent-lab\agency-broker\verify_pr.py",pr_url],
            capture_output=True,text=True,timeout=60,check=True)
        verified=json.loads(result.stdout)
        if (str(verified.get("status")).lower() not in {"completed","abandoned"}
                or verified.get("targetRefName")!="refs/heads/users/nibeauli/slopmop-devel"
                or not str(verified.get("sourceRefName","")).startswith(
                    "refs/heads/users/nibeauli/slopmop-demo-")):
            raise RuntimeError("campaign PR is not terminal inside the owned boundary")
    archive=STATE/("archive-"+args.campaign+".json")
    archive.write_text(json.dumps({"campaign":campaign,"resetPlan":plan}))
    archive.chmod(0o600)
    for config,row in actors.values():
        backend=Backend(config)
        try:
            backend.revoke_binding(row)
        finally:
            backend.close()
    for session,config in sessions.items():
        backend=Backend(config)
        try:
            try:
                backend.delete_session(session)
            except RuntimeError:
                actor=subprocess.run(["/home/nibeauli/kagent-lab/artifacts/kubectl-ate","--preview",
                    "get","actor","session-"+session,"-a",SPACE,"-o","json"],
                    env=ENV,capture_output=True,timeout=120)
                if actor.returncode==0 or b"NotFound" not in actor.stderr:
                    raise
        finally:
            backend.close()
    for branch in branches:
        remote=subprocess.check_output([WINDOWS_GIT,"-C",REPOSITORY,"ls-remote","--heads",
            "origin","refs/heads/"+branch],text=True,env=ENV,timeout=180)
        if remote.strip():
            subprocess.run([WINDOWS_GIT,"-C",REPOSITORY,"push","origin","--delete",branch],
                env=ENV,check=True,timeout=180)
    observed=subprocess.check_output([WINDOWS_GIT,"-C",REPOSITORY,"ls-remote","--heads","origin",
        "refs/heads/"+TARGET],text=True,env=ENV).split()[0]
    subprocess.run([WINDOWS_GIT,"-C",REPOSITORY,"push",
        "--force-with-lease=refs/heads/"+TARGET+":"+observed,
        "origin",args.baseline+":refs/heads/"+TARGET],env=ENV,check=True,timeout=180)
    for role,identifiers in tasks.items():
        config_path=STATE/("service.json" if role=="coding" else "service-"+role+".json")
        config=json.loads(config_path.read_text())
        store=Store(config["postgres"],config["maximum"],config["backlog"],
            config["gatewayName"],config["taskPrefix"])
        with store.connection() as db:
            db.execute("DELETE FROM factory.gateway_tasks WHERE gateway=%s AND id=ANY(%s)",
                (store.gateway,identifiers))
        store.close()
    with manager.connection() as db:
        db.execute("DELETE FROM factory.gateway_tasks WHERE gateway='swarm-manager' AND id=%s",
            (campaign["id"],))
    manager.close()
    print("Campaign reset completed; archive: "+str(archive))


def collect_branches(value,campaign):
    found=set()
    if isinstance(value,dict):
        for key,child in value.items():
            if (key in {"branch","workBranch"} and isinstance(child,str)
                    and child.startswith("users/nibeauli/slopmop-demo-"+campaign+"-")):
                found.add(child)
            found.update(collect_branches(child,campaign))
    elif isinstance(value,list):
        for child in value:
            found.update(collect_branches(child,campaign))
    return found
if __name__=="__main__":
    main()

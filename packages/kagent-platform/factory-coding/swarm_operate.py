"""Root-only recovery controls for retained Swarm Manager tasks."""
import argparse
import json
from pathlib import Path
import subprocess
import time

from gateway_store import Store, TERMINAL
from swarm_manager import parse_json, validate_pr_url
from swarm_roles import ROLES

STATE=Path("/home/nibeauli/kagent-lab/factory-coding")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["resume","adopt","adopt-pr"])
    parser.add_argument("campaign")
    parser.add_argument("--role",choices=sorted(ROLES))
    parser.add_argument("--key")
    parser.add_argument("--task")
    parser.add_argument("--pr-url")
    args=parser.parse_args()
    config=json.loads((STATE/"service-swarm-manager.json").read_text())
    store=Store(config["postgres"],1,20,"swarm-manager","campaign")
    matches=[task for task in store.list() if (task["result"] or {}).get("campaign")==args.campaign]
    if len(matches)!=1:
        raise RuntimeError("exactly one retained campaign is required")
    task=matches[0]
    manifest=task["result"]
    if args.action=="adopt-pr":
        if not args.key or not args.pr_url:
            raise RuntimeError("adopt-pr requires key and pr-url")
        pr_url=validate_pr_url(args.pr_url)
        child=manifest.get("children",{}).get(args.key)
        if not child or child.get("role") not in {"coding","documentation-steward"}:
            raise RuntimeError("existing finish child is required")
        existing_url=None
        if isinstance(child.get("answer"),dict):
            existing_url=child["answer"].get("pullRequestUrl")
        if existing_url and validate_pr_url(existing_url)!=pr_url:
            raise RuntimeError("finish child PR differs")
        result=subprocess.run([config["windowsPython"],
            r"Q:\tools\kagent-lab\agency-broker\verify_pr.py",pr_url],
            capture_output=True,text=True,timeout=60,check=True)
        verified=json.loads(result.stdout)
        if (str(verified.get("status")).lower()!="completed"
                or verified.get("targetRefName")!="refs/heads/users/nibeauli/slopmop-devel"
                or not str(verified.get("sourceRefName","")).startswith(
                    "refs/heads/users/nibeauli/slopmop-demo-")):
            raise RuntimeError("trusted PR verification did not prove owned completion")
        child["answer"]={"status":"merged","pullRequestUrl":pr_url,
            "commit":verified.get("lastMergeSourceCommit"),
            "evidence":["Trusted upstream verification reports completed.",
                "Target and source branches remain inside the disposable swarm boundary."],
            "verification":verified}
        manifest.setdefault("events",[]).append({"event":"operator_pr_completion_adopted",
            "key":args.key,"role":child["role"],"task":child.get("task"),
            "pullRequestUrl":pr_url,"time":time.time()})
        store.update(task["id"],result=manifest)
        store.close()
        print("Trusted completed PR evidence adopted into retained campaign: "+pr_url)
        return
    if args.action=="adopt":
        if not args.role or not args.key or not args.task:
            raise RuntimeError("adopt requires role, key, and task")
        role_config=STATE/("service.json" if args.role=="coding" else "service-"+args.role+".json")
        config=json.loads(role_config.read_text())
        children=Store(config["postgres"],config["maximum"],config["backlog"],
            config["gatewayName"],config["taskPrefix"])
        child=children.get(args.task)
        if not child or child["owner"]!="swarm-manager" or child["state"]!="completed":
            raise RuntimeError("completed swarm-manager-owned child task required")
        receipt=json.loads(child["result"]["status"]["message"]["parts"][0]["text"])
        execution=json.loads(receipt["detail"])
        answer=parse_json(execution["answer"])
        manifest.setdefault("children",{})[args.key]={"role":args.role,"task":child["id"],
            "context":child["context_id"],"answer":answer,"tools":execution.get("tools",[]),
            "toolResults":execution.get("toolResults",[])}
        store.update(task["id"],result=manifest)
        children.close()
        store.close()
        print("Completed child task adopted into retained campaign: "+child["id"])
        return
    if task["state"]!="failed":
        raise RuntimeError("failed retained campaign required")
    manifest["state"]="resuming"
    manifest.pop("error",None)
    store.update(task["id"],state="dispatching",diagnostic=None,result=manifest)
    store.close()
    print("Retained campaign resumed with stable child task identities: "+task["id"])


if __name__=="__main__":
    main()

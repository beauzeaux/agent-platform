"""Submit one idempotent, bounded autonomous SlopMop discovery campaign."""
import argparse
import datetime
import json
from pathlib import Path
import re
import ssl
import time
import urllib.request
import uuid


STATE=Path("/home/nibeauli/kagent-lab/factory-coding")


def campaign_identity(timestamp=None,window_hours=12):
    timestamp=time.time() if timestamp is None else timestamp
    window=window_hours*60*60
    start=int(timestamp//window)*window
    value=datetime.datetime.fromtimestamp(start,datetime.timezone.utc)
    campaign="auto-"+value.strftime("%Y%m%d-%H")
    return campaign,"autonomous-"+campaign


def fetch_json(url,context,headers=None):
    request=urllib.request.Request(url,headers=headers or {})
    with urllib.request.urlopen(request,context=context,timeout=30) as response:
        return json.load(response)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default=str(STATE/"service-swarm-manager.json"))
    parser.add_argument("--lookback-days",type=int,default=7)
    parser.add_argument("--source-limit",type=int,default=12)
    parser.add_argument("--candidate-limit",type=int,default=3)
    parser.add_argument("--implementation-limit",type=int,default=1)
    parser.add_argument("--window-hours",type=int,default=12)
    parser.add_argument("--campaign")
    parser.add_argument("--execute",action="store_true")
    args=parser.parse_args()
    if not 1<=args.window_hours<=24:
        raise ValueError("window hours must be between 1 and 24")
    config=json.loads(Path(args.config).read_text())
    context=ssl.create_default_context(cafile=config["ca"])
    public=fetch_json("https://10.82.0.2:18451/api/campaigns",context)
    if any(campaign.get("state") in {"queued","dispatching","active","uncertain"}
           for campaign in public.get("campaigns",[])):
        print("Skipped autonomous discovery because another campaign is active.")
        return
    if args.campaign:
        if not re.fullmatch(r"[a-z0-9-]{3,48}",args.campaign):
            raise ValueError("invalid campaign override")
        campaign,message_id=args.campaign,"autonomous-"+args.campaign
    else:
        campaign,message_id=campaign_identity(window_hours=args.window_hours)
    payload={"jsonrpc":"2.0","id":str(uuid.uuid4()),"method":"SendMessage","params":{"message":{
        "messageId":message_id,"role":"ROLE_USER","parts":[{"data":{
            "campaign":campaign,
            "goal":"Continuously mine recent engsys pull requests, review findings, code changes, and source patterns for generalized, conservative SlopMop cleanup opportunities.",
            "candidateLimit":args.candidate_limit,
            "implementationLimit":args.implementation_limit,
            "execute":args.execute,
            "autonomousDiscovery":True,
            "lookbackDays":args.lookback_days,
            "sourceLimit":args.source_limit,
            "approvedPrUrls":[],
            "triageWorkItemIds":[],
        }}]}}}
    token=(STATE/"operator-token").read_text().strip()
    request=urllib.request.Request("https://10.82.0.2:18451/a2a",
        data=json.dumps(payload).encode(),
        headers={"Content-Type":"application/json","Authorization":"Bearer "+token})
    with urllib.request.urlopen(request,context=context,timeout=60) as response:
        result=json.load(response)
    if result.get("error"):
        raise RuntimeError("autonomous campaign rejected: "+json.dumps(result["error"]))
    task=result["result"]["task"]
    print(json.dumps({"campaign":campaign,"task":task["id"],"state":task["status"]["state"],
        "execute":args.execute}))


if __name__=="__main__":
    main()

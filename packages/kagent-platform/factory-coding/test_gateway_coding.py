"""Seven real read-only coding tasks through the direct gateway."""
import json
from pathlib import Path
import subprocess
import time
import uuid

from gateway_store import ACTIVE, Store, TERMINAL
from test_gateway_live import rpc, STATE


def run():
    marker="gateway-coding-"+uuid.uuid4().hex[:12]
    path=STATE/("acceptance-"+marker+".json")
    config=json.loads((STATE/"service.json").read_text())
    store=Store(config["postgres"],config["maximum"],config["backlog"],
        config["gatewayName"],config["taskPrefix"])
    report={"run":marker,"tasks":[],"samples":[]}
    scopes=[
        "Repository build and dependency entrypoints; identify the smallest validated developer build command.",
        "Repository test entrypoints and test selection; identify one small test command and what it verifies.",
        "Repository contribution and review documentation; identify required local checks and PR conventions.",
        "Repository CI pipeline structure; identify major validation stages and their exact file references.",
        "Repository tool bootstrap scripts; identify one deterministic safe bootstrap path and prerequisites.",
        "Repository ownership boundaries; identify routing metadata and how a contributor chooses reviewers.",
        "Repository developer documentation organization; identify its index and one concrete discoverability improvement.",
    ]
    def save():
        path.write_text(json.dumps(report))
        path.chmod(0o600)
    try:
        for index,scope in enumerate(scopes):
            message={"messageId":marker+"-"+str(index),"role":"ROLE_USER","parts":[{"data":{
                "allowEdits":False,
                "prompt":"Read-only direct gateway acceptance. Do not edit, commit, push, create PRs/work items, or change remote state. "
                    "Investigate only this independent scope: "+scope+
                    " Use bounded repository tools. Return a concise useful finding with exact paths and distinguish verified from unexecuted commands."
            }}]}
            result=rpc("SendMessage",{"message":message})
            assert "error" not in result,result
            report["tasks"].append(result["result"]["task"]["id"])
            save()
        peak=0
        deadline=time.monotonic()+2400
        while time.monotonic()<deadline:
            rows=[store.get(identifier) for identifier in report["tasks"]]
            active=sum(row["state"] in ACTIVE for row in rows)
            peak=max(peak,active)
            assert active<=3
            report["samples"].append({"time":time.time(),"active":active,
                "states":[row["state"] for row in rows]})
            save()
            if all(row["state"] in TERMINAL for row in rows):
                break
            time.sleep(5)
        else:
            raise RuntimeError("coding acceptance deadline; inspect "+str(path))
        assert peak==3
        assert all(row["state"]=="completed" for row in rows),[
            {"id":row["id"],"state":row["state"],"diagnostic":row["diagnostic"]} for row in rows]
        findings={}
        for row in rows:
            receipt=json.loads(row["result"]["status"]["message"]["parts"][0]["text"])
            coding=json.loads(receipt["detail"])
            assert coding["status"]=="completed" and coding["answer"].strip() and coding["tools"]
            assert coding["allowEdits"] is False and coding["gitStatus"]==""
            findings[row["id"]]={"answer":coding["answer"],"tools":coding["tools"],
                "commit":coding["commit"],"session":row["session"]}
        script="import json,urllib.request;print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:15442/health',timeout=5))))"
        health=json.loads(subprocess.run([config["windowsPython"],"-c",script],capture_output=True,
            check=True,timeout=15).stdout)
        assert health.get("ready") is True
        report.update({"peak":peak,"findings":findings,"gatewayHealth":health,"passed":True})
        save()
        print(json.dumps({"passed":True,"peak":peak,"gatewayPid":health.get("gatewayPid"),
            "protectedReport":str(path)}))
    finally:
        save()
        store.close()


if __name__=="__main__":
    run()

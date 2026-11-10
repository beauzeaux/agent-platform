"""Live planning-loop and visualization acceptance for the SlopMop swarm."""
import json
from pathlib import Path
import ssl
import time
import urllib.request
import uuid

STATE=Path("/home/nibeauli/kagent-lab/factory-coding")
ORIGIN="https://10.82.0.2:18451"


def rpc(method,params):
    token=(STATE/"operator-token").read_text().strip()
    request=urllib.request.Request(ORIGIN+"/a2a",
        data=json.dumps({"jsonrpc":"2.0","id":str(uuid.uuid4()),"method":method,"params":params}).encode(),
        headers={"Content-Type":"application/json","Authorization":"Bearer "+token})
    context=ssl.create_default_context(cafile=str(STATE/"ca.crt"))
    for attempt in range(30):
        try:
            with urllib.request.urlopen(request,context=context,timeout=300) as response:
                return json.load(response)
        except OSError:
            if attempt==29:
                raise
            time.sleep(1)


def run():
    marker="swarm-"+uuid.uuid4().hex[:12]
    report_path=STATE/("acceptance-"+marker+".json")
    report={"run":marker}
    message={"messageId":marker,"role":"ROLE_USER","parts":[{"data":{
        "campaign":marker,
        "goal":"Find conservative, non-duplicate SlopMop analyzer opportunities grounded in the existing rule catalog.",
        "candidateLimit":3,
        "implementationLimit":0,
        "execute":False,
    }}]}
    response=rpc("SendMessage",{"message":message})
    assert "error" not in response,response
    identifier=response["result"]["task"]["id"]
    report["task"]=identifier
    deadline=time.monotonic()+2400
    while time.monotonic()<deadline:
        task=rpc("GetTask",{"id":identifier})["result"]
        report["lastTask"]=task
        report_path.write_text(json.dumps(report))
        report_path.chmod(0o600)
        if task["status"]["state"] in {"TASK_STATE_COMPLETED","TASK_STATE_FAILED","TASK_STATE_CANCELED"}:
            break
        time.sleep(5)
    else:
        raise RuntimeError("swarm planning acceptance deadline")
    assert task["status"]["state"]=="TASK_STATE_COMPLETED",task
    manifest=json.loads(task["artifacts"][0]["parts"][0]["text"])
    assert manifest["state"]=="completed"
    assert len(manifest["candidates"])==3
    assert len(manifest["plans"])==3
    assert len(manifest["planReviews"])==3
    roles={child["role"] for child in manifest["children"].values()}
    assert {"opportunity-scout","planner","plan-reviewer"}<=roles
    context=ssl.create_default_context(cafile=str(STATE/"ca.crt"))
    with urllib.request.urlopen(ORIGIN+"/dashboard",context=context,timeout=10) as response:
        assert b"SlopMop autonomous swarm" in response.read()
    with urllib.request.urlopen(ORIGIN+"/api/campaigns",context=context,timeout=10) as response:
        campaigns=json.load(response)["campaigns"]
    assert any(item["id"]==identifier for item in campaigns)
    report.update({"manifest":manifest,"passed":True})
    report_path.write_text(json.dumps(report))
    print(json.dumps({"passed":True,"task":identifier,"roles":sorted(roles),
        "protectedReport":str(report_path)}))


if __name__=="__main__":
    run()

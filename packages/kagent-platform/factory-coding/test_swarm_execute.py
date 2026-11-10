"""Full disposable SlopMop swarm campaign with real PR completion."""
import json
from pathlib import Path
import ssl
import subprocess
import time
import urllib.request
import uuid

from test_swarm_live import rpc, STATE

ORIGIN="https://10.82.0.2:18451"
PATTERN_PR="https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/7329416"
TRIAGE_ITEM=11459224


def run():
    marker="demo-"+uuid.uuid4().hex[:12]
    path=STATE/("acceptance-"+marker+".json")
    report={"run":marker}
    message={"messageId":marker,"role":"ROLE_USER","parts":[{"data":{
        "campaign":marker,
        "goal":("Prioritize a minimal, syntax-only analyzer for an empty MSTest TestMethod body. It must not suggest "
            "a behavior-changing refactor, must exclude non-empty methods and non-test methods, and must be deduplicated "
            "against existing SM3001-SM3012 rules before independent approval."),
        "candidateLimit":1,
        "implementationLimit":1,
        "execute":True,
        "approvedPrUrls":[PATTERN_PR],
        "triageWorkItemIds":[TRIAGE_ITEM],
    }}]}
    response=rpc("SendMessage",{"message":message})
    assert "error" not in response,response
    identifier=response["result"]["task"]["id"]
    report["task"]=identifier
    deadline=time.monotonic()+7200
    while time.monotonic()<deadline:
        task=rpc("GetTask",{"id":identifier})["result"]
        report["lastTask"]=task
        path.write_text(json.dumps(report))
        path.chmod(0o600)
        if task["status"]["state"] in {"TASK_STATE_COMPLETED","TASK_STATE_FAILED","TASK_STATE_CANCELED"}:
            break
        time.sleep(10)
    else:
        raise RuntimeError("full swarm campaign deadline")
    assert task["status"]["state"]=="TASK_STATE_COMPLETED",task
    manifest=json.loads(task["artifacts"][0]["parts"][0]["text"])
    assert manifest["state"]=="completed"
    assert len(manifest["implementations"])==1
    assert manifest.get("documentation")
    assert manifest.get("triage") and manifest.get("mined")
    pr_urls=[manifest["implementations"][0]["pullRequestUrl"],
        manifest["documentation"]["pullRequestUrl"]]
    config=json.loads((STATE/"service.json").read_text())
    verified=[]
    for url in pr_urls:
        result=subprocess.run([config["windowsPython"],
            r"Q:\tools\kagent-lab\agency-broker\verify_pr.py",url],
            capture_output=True,check=True,timeout=60)
        value=json.loads(result.stdout)
        assert value["status"]=="completed"
        assert value["targetRefName"]=="refs/heads/users/nibeauli/slopmop-devel"
        verified.append(value)
    baseline=json.loads((STATE/"slopmop-devel-baseline.json").read_text())["baseline"]
    observed=subprocess.check_output([r"/mnt/c/Program Files/Git/cmd/git.exe","-C",r"Q:\src\engsys",
        "ls-remote","--heads","origin","refs/heads/users/nibeauli/slopmop-devel"],text=True).split()[0]
    assert observed!=baseline
    context=ssl.create_default_context(cafile=str(STATE/"ca.crt"))
    with urllib.request.urlopen(ORIGIN+"/dashboard",context=context,timeout=10) as response:
        assert marker.encode() in response.read() or response.status==200
    report.update({"manifest":manifest,"verifiedPullRequests":verified,
        "targetCommit":observed,"passed":True})
    path.write_text(json.dumps(report))
    print(json.dumps({"passed":True,"campaign":marker,"pullRequests":pr_urls,
        "targetCommit":observed,"protectedReport":str(path)}))


if __name__=="__main__":
    run()

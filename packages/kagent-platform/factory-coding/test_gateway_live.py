"""Live proof for the direct PostgreSQL-backed A2A multiplexer."""
import json
from pathlib import Path
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from backend import kube
from gateway_store import ACTIVE, Store, TERMINAL

STATE=Path("/home/nibeauli/kagent-lab/factory-coding")
ORIGIN="https://10.82.0.2:18443"
NAMESPACE="factory-workflows"


def rpc(method,params,token=None):
    token=token or (STATE/"operator-token").read_text().strip()
    request=urllib.request.Request(ORIGIN+"/a2a",
        data=json.dumps({"jsonrpc":"2.0","id":str(uuid.uuid4()),"method":method,"params":params}).encode(),
        headers={"Content-Type":"application/json","Authorization":"Bearer "+token})
    for attempt in range(20):
        try:
            with urllib.request.urlopen(request,context=ssl.create_default_context(cafile=str(STATE/"ca.crt")),timeout=60) as response:
                return json.load(response)
        except (urllib.error.URLError,TimeoutError):
            if attempt==19:
                raise
            time.sleep(1)


def run():
    marker="gateway-"+uuid.uuid4().hex[:12]
    path=STATE/("acceptance-"+marker+".json")
    report={"run":marker,"tasks":[]}
    config=json.loads((STATE/"service.json").read_text())
    store=Store(config["postgres"],config["maximum"],config["backlog"],
        config["gatewayName"],config["taskPrefix"])
    baseline={item["metadata"]["uid"] for item in kube("get","workflows","-n",NAMESPACE,"-o","json")["items"]}
    def save():
        path.write_text(json.dumps(report))
        path.chmod(0o600)
    def submit(name,context=None):
        message={"messageId":marker+"-"+name,"role":"ROLE_USER",
            "parts":[{"data":{"fixture":marker+"-"+name}}]}
        if context:
            message["contextId"]=context
        response=rpc("SendMessage",{"message":message})
        assert "error" not in response,response
        identifier=response["result"]["task"]["id"]
        report["tasks"].append(identifier)
        save()
        return identifier
    def rows(identifiers):
        return [store.get(identifier) for identifier in identifiers]
    def wait(predicate,timeout=420):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=predicate()
            if value:
                return value
            time.sleep(.5)
        raise RuntimeError("gateway acceptance deadline; inspect "+str(path))
    try:
        with urllib.request.urlopen(ORIGIN+"/.well-known/agent-card.json",
            context=ssl.create_default_context(cafile=str(STATE/"ca.crt")),timeout=10) as response:
            card=json.load(response)
            assert card["version"]=="1" and card["name"]=="slopmop-coding"
        batch=[submit("batch-"+str(index)) for index in range(7)]
        peak=0
        while True:
            current=rows(batch)
            active=sum(row["state"] in ACTIVE for row in current)
            peak=max(peak,active)
            assert active<=3
            if all(row["state"] in TERMINAL for row in current):
                break
            time.sleep(.5)
        assert peak==3
        assert all(row["state"]=="completed" for row in current)
        assert len({row["session"] for row in current})==7
        report["sevenTasks"]={"peak":peak,"sessions":[row["session"] for row in current]}
        same_context=marker+"-shared"
        first=submit("context-first",same_context)
        assert wait(lambda: store.get(first)["state"] in TERMINAL)
        second=submit("context-second",same_context)
        third=submit("context-third",same_context)
        saw_serialized=wait(lambda: (
            store.get(second)["state"] in ACTIVE and store.get(third)["state"]=="queued"
        ) or (
            store.get(third)["state"] in ACTIVE and store.get(second)["state"]=="queued"
        ))
        assert saw_serialized
        assert wait(lambda: all(store.get(item)["state"]=="completed" for item in [second,third]))
        context_rows=rows([first,second,third])
        assert len({row["session"] for row in context_rows})==1
        report["contextReuse"]={"context":same_context,"session":context_rows[0]["session"]}
        blockers=[submit("restart-"+str(index)) for index in range(4)]
        wait(lambda: sum(store.get(item)["state"] in ACTIVE for item in blockers)==3)
        subprocess.run(["systemctl","restart","factory-coding.service"],check=True,timeout=90)
        assert wait(lambda: all(store.get(item)["state"]=="completed" for item in blockers))
        report["restartRecovery"]={"tasks":blockers,"completed":True}
        active=[submit("cancel-block-"+str(index)) for index in range(3)]
        wait(lambda: sum(store.get(item)["state"] in ACTIVE for item in active)==3)
        queued=submit("queued-cancel")
        wait(lambda: store.get(queued)["state"]=="queued")
        canceled=rpc("CancelTask",{"id":queued})
        assert canceled["result"]["status"]["state"]=="TASK_STATE_CANCELED"
        row=store.get(queued)
        assert row["session"] is None and row["native_task"] is None
        assert wait(lambda: all(store.get(item)["state"]=="completed" for item in active))
        report["queuedCancellation"]={"task":queued,"executed":False}
        retry_message={"messageId":marker+"-dedup","role":"ROLE_USER",
            "parts":[{"data":{"fixture":marker+"-dedup"}}]}
        one=rpc("SendMessage",{"message":retry_message})["result"]["task"]
        two=rpc("SendMessage",{"message":retry_message})["result"]["task"]
        assert one["id"]==two["id"]
        assert wait(lambda: store.get(one["id"])["state"]=="completed")
        report["deduplication"]={"task":one["id"]}
        observed={item["metadata"]["uid"] for item in kube("get","workflows","-n",NAMESPACE,"-o","json")["items"]}
        assert observed==baseline
        report["argoWorkflowsCreated"]=0
        report["passed"]=True
        save()
        print(json.dumps({"passed":True,"peak":peak,"protectedReport":str(path)}))
    finally:
        save()
        store.close()


if __name__=="__main__":
    run()

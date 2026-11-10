"""Trusted primary-host adapter; credentials are never put into coding actors."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path("/mnt/q/tools/kagent-lab")
sys.path.insert(0, str(ROOT))
from lab_transport import port_forward

KUBE = ["/snap/bin/microk8s", "kubectl"]
ENV = dict(os.environ, KUBECONFIG="/var/snap/microk8s/current/credentials/client.config")
SPACE = "factory-executions"

class NotAccepted(RuntimeError):
    definitely_not_accepted = True


def kube(*arguments, body=None, optional=False):
    encoded = json.dumps(body).encode() if body is not None else None
    for attempt in range(20):
        result = subprocess.run([*KUBE,*arguments],env=ENV,input=encoded,
            capture_output=True,timeout=180)
        if result.returncode == 0 or b"database is locked" not in result.stderr or attempt == 19:
            break
        time.sleep(.5)
    if result.returncode:
        if optional and b"NotFound" in result.stderr:
            return None
        diagnostic = Path("/home/nibeauli/kagent-lab/factory-coding/platform-error.log")
        diagnostic.write_bytes(result.stderr)
        diagnostic.chmod(0o600)
        raise RuntimeError("Kubernetes operation failed; inspect protected platform diagnostics")
    return json.loads(result.stdout) if result.stdout.strip() else None


class Backend:
    def __init__(self, config):
        self.config = config
        self.forward = None
        self.origin = None
        self.lock = threading.Lock()
        self.binding_lock = threading.Lock()

    def close(self):
        with self.lock:
            if self.forward:
                self.forward.__exit__(None, None, None)
                self.forward = None

    def endpoint(self):
        with self.lock:
            if self.forward is None:
                self.forward = port_forward(SPACE, "factory-controller", 18093, 8083)
                self.origin = self.forward.__enter__()
            return self.origin

    def session_rpc(self, method, identity):
        origin = self.endpoint()
        result = subprocess.run([self.config["clientBinary"], origin.removeprefix("http://"),
            self.config["controllerToken"], method, identity,self.config.get("backendAgent","factory-coder")],
            env=ENV,capture_output=True,timeout=210)
        if result.returncode:
            self.close()
            raise RuntimeError("authenticated session operation failed")
        return json.loads(result.stdout)["session"]["id"]

    def create_session(self, identity):
        return self.session_rpc("create", identity)

    def delete_session(self, session):
        return self.session_rpc("delete", session)

    def a2a(self, method, params):
        token = Path(self.config["controllerToken"]).read_text().strip()
        request = urllib.request.Request(self.endpoint()+"/agents/"+SPACE+"/"+
            self.config.get("backendAgent","factory-coder"),
            data=json.dumps({"jsonrpc":"2.0","id":str(uuid.uuid4()),"method":method,"params":params}).encode(),
            headers={"Content-Type":"application/json","Authorization":"Bearer "+token})
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                result = json.load(response)
        except OSError:
            self.close()
            raise
        if "error" in result:
            path = Path("/home/nibeauli/kagent-lab/factory-coding/a2a-error.json")
            path.write_text(json.dumps(result["error"]))
            path.chmod(0o600)
            if method == "SendMessage" and any(
                isinstance(info,dict) and info.get("metadata",{}).get("reason") == "KAGENT_SEND_NOT_ACCEPTED"
                for info in result["error"].get("data",[])):
                raise NotAccepted("Kagent confirmed no send admission; retry the same message identity")
            raise RuntimeError("Kagent operation rejected")
        return result["result"]

    def send(self, task):
        request = task["request"]
        text = "fixture:"+request["fixture"] if "fixture" in request else json.dumps({
            key:value for key,value in request.items()
            if key in {"prompt","allowEdits","allowAuditUi","refreshPlugins","baseBranch","workBranch",
                       "workingDirectory"}})
        return self.a2a("SendMessage", {"message":{
            "messageId":str(uuid.uuid5(uuid.NAMESPACE_URL, task["id"])),
            "contextId":task["session"], "role":"ROLE_USER", "parts":[{"text":text}]},
            "configuration":{"returnImmediately":True}})

    def find_task(self, task):
        result = self.a2a("ListTasks", {"contextId":task["session"],"pageSize":100})
        marker = str(uuid.uuid5(uuid.NAMESPACE_URL, task["id"]))
        for native in result.get("tasks", []):
            if any(message.get("messageId") == marker for message in native.get("history", [])):
                return native
        # Fresh sessions contain exactly one task; no other assignment can use it.
        tasks = result.get("tasks", [])
        if not task["request"].get("continuation") and len(tasks) == 1 and not result.get("nextPageToken"):
            return tasks[0]
        return None

    def get_task(self, task):
        return self.a2a("GetTask", {"id":task["native_task"]})

    def cancel(self, task):
        try:
            self.a2a("CancelTask", {"id":task["native_task"]})
        except RuntimeError:
            # A racing terminal outcome is observed by GetTask, never overwritten.
            pass

    def bind_session(self, session):
        with self.binding_lock:
            return self._bind_session(session)

    def identify_session(self, session):
        actor_name = "session-"+str(uuid.UUID(session))
        command = ["/home/nibeauli/kagent-lab/artifacts/kubectl-ate", "--preview", "get",
                   "actor", actor_name, "-a", SPACE, "-o", "json"]
        result = subprocess.run(command, env=ENV, capture_output=True, timeout=120, check=True)
        actor = json.loads(result.stdout)
        actor_uid = str(uuid.UUID(actor["metadata"]["uid"]))
        if actor["metadata"]["name"] != actor_name or actor["metadata"]["atespace"] != SPACE:
            raise RuntimeError("runtime identity mismatch")
        return actor_uid

    def _bind_session(self, session):
        actor_name = "session-"+str(uuid.UUID(session))
        actor_uid = self.identify_session(session)
        subprocess.run([self.config["windowsPython"], self.config["bindingScript"],
            "--actor-uid", actor_uid, "--subject", "actor/"+SPACE+"/"+actor_name,
            "--profile",self.config.get("agencyProfile","engsys-contributor")],
            env=ENV,capture_output=True,check=True,timeout=60)
        # The host broker reads atomic protected bindings per request. Projection
        # updates the cluster allowlist without restarting either gateway.
        bindings = json.loads((ROOT / "agency-broker/public/actors.json").read_text())
        manifest = {"apiVersion":"v1","kind":"ConfigMap",
            "metadata":{"name":"factory-agency-bindings","namespace":"proxy-access",
                        "labels":{"app.kubernetes.io/managed-by":"factory-coding"}},
            "data":{"actors.json":json.dumps(bindings)}}
        kube("apply","-f","-","-o","json",body=manifest)
        script = "const b=JSON.parse(require('fs').readFileSync('/app/agency-bindings/actors.json'));process.stdout.write(JSON.stringify(!!b.actors["+json.dumps(actor_uid)+"]));"
        for _ in range(120):
            result = subprocess.run([*KUBE,"exec","-n","proxy-access","deployment/agent-proxies",
                "--","node","-e",script], env=ENV, capture_output=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip() == b"true":
                return actor_uid
            time.sleep(1)
        raise RuntimeError("dynamic actor authorization did not propagate")

    def revoke_binding(self, task):
        if not task["actor_uid"]:
            return
        with self.binding_lock:
            subprocess.run([self.config["windowsPython"],self.config["bindingScript"],
                "--actor-uid",task["actor_uid"],"--revoke"],env=ENV,capture_output=True,check=True,timeout=90)
            bindings = json.loads((ROOT/"agency-broker/public/actors.json").read_text())
            kube("patch","configmap","factory-agency-bindings","-n","proxy-access","--type=merge",
                "-p",json.dumps({"data":{"actors.json":json.dumps(bindings)}}),"-o","json")

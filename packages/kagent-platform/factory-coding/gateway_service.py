"""Single-agent A2A multiplexer; Kagent owns execution and session lifecycle."""
import argparse
import concurrent.futures
import hashlib
import http.server
import json
from pathlib import Path
import re
import ssl
import threading
import time

from backend import Backend, NotAccepted
from gateway_store import ACTIVE, Conflict, Store, TERMINAL

TASK_STATES = {
    "queued":"TASK_STATE_SUBMITTED", "dispatching":"TASK_STATE_WORKING",
    "active":"TASK_STATE_WORKING", "uncertain":"TASK_STATE_WORKING",
    "completed":"TASK_STATE_COMPLETED", "failed":"TASK_STATE_FAILED",
    "canceled":"TASK_STATE_CANCELED",
}


def public_task(task):
    value = {
        "id":task["id"], "contextId":task["context_id"],
        "status":{"state":TASK_STATES[task["state"]]},
        "metadata":{
            "gateway":task.get("gateway"), "phase":task["state"], "executionSession":task["session"],
            "cancellationRequested":bool(task["cancel_requested"]),
        },
    }
    if task["diagnostic"]:
        value["metadata"]["diagnostic"] = task["diagnostic"]
    if task["result"] is not None:
        value["artifacts"] = [{
            "artifactId":task["id"]+"-result", "name":"agent-result",
            "parts":[{"text":json.dumps(task["result"])}],
        }]
    return value


class Gateway:
    def __init__(self, store, backend, identity, poll_seconds=3, settings=None):
        self.store, self.backend, self.identity = store, backend, identity
        self.settings = settings or {}
        self.poll_seconds = poll_seconds
        self.stop = threading.Event()
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=store.maximum, thread_name_prefix=store.gateway
        )
        self.lock, self.running = threading.Lock(), {}

    def submit(self, owner, message, fixture_allowed=False):
        request_id = message.get("messageId")
        if not isinstance(request_id,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}",request_id):
            raise ValueError("stable messageId required")
        context_id = message.get("contextId")
        if context_id is not None and (
            not isinstance(context_id,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}",context_id)
        ):
            raise ValueError("bounded contextId required")
        parts = message.get("parts")
        if not isinstance(parts,list) or len(parts)!=1 or not isinstance(parts[0],dict):
            raise ValueError("one bounded input part required")
        request = parts[0].get("data")
        if request is None:
            text = parts[0].get("text")
            if not isinstance(text,str):
                raise ValueError("text or structured data required")
            request = {"prompt":text}
        if not isinstance(request,dict) or set(request)-{
            "prompt","allowEdits","allowAuditUi","refreshPlugins","fixture","baseBranch","workBranch",
            "workingDirectory"
        }:
            raise ValueError("unapproved request fields")
        request = dict(request)
        if "fixture" in request:
            if not fixture_allowed or not isinstance(request["fixture"],str) or not re.fullmatch(
                r"[A-Za-z0-9_.-]{1,64}",request["fixture"]
            ):
                raise ValueError("fixture not authorized")
        elif not isinstance(request.get("prompt"),str) or not 0<len(request["prompt"].strip())<=16000:
            raise ValueError("bounded coding prompt required")
        for flag in ["allowEdits","allowAuditUi","refreshPlugins"]:
            if flag in request and not isinstance(request[flag],bool):
                raise ValueError("invalid authorization flag")
        fixed_edits = self.settings.get("fixedAllowEdits")
        if fixed_edits is not None:
            if request.get("allowEdits") not in {None,fixed_edits}:
                raise ValueError("request exceeds gateway edit authority")
            request["allowEdits"] = fixed_edits
        fixed_base = self.settings.get("fixedBaseBranch")
        if fixed_base:
            if request.get("baseBranch") not in {None,fixed_base}:
                raise ValueError("request exceeds gateway base-branch authority")
            request["baseBranch"] = fixed_base
        else:
            allowed_bases=self.settings.get("allowedBaseBranches",[None])
            base=request.get("baseBranch") or allowed_bases[0]
            allowed=base in allowed_bases
            allowed=allowed or any(str(base).startswith(prefix)
                for prefix in self.settings.get("allowedBaseBranchPrefixes",[]))
            if not allowed:
                raise ValueError("request exceeds gateway base-branch authority")
            request["baseBranch"]=base
        if request.get("workBranch") is not None and (
            not isinstance(request["workBranch"],str)
            or not re.fullmatch(r"users/nibeauli/slopmop-demo-[A-Za-z0-9-]{1,80}",request["workBranch"])
        ):
            raise ValueError("invalid owned work branch")
        working_directory = request.get("workingDirectory")
        if working_directory == ".":
            request.pop("workingDirectory")
        elif working_directory is not None and (
            not isinstance(working_directory,str) or len(working_directory)>512
            or working_directory == "" or working_directory.startswith("/")
            or "\\" in working_directory or ":" in working_directory
            or any(ord(character)<32 or 127<=ord(character)<160 for character in working_directory)
            or len(working_directory.split("/"))>31
            or any(part in {"", ".", ".."} or part.endswith((".", " "))
                   for part in working_directory.split("/"))
        ):
            raise ValueError("invalid working directory")
        if not request.get("allowEdits",True) and request.get("workBranch"):
            raise ValueError("read-only task cannot own a work branch")
        instruction = self.settings.get("roleInstruction")
        if instruction and "fixture" not in request:
            request["prompt"] = instruction+"\n\nAssigned task:\n"+request["prompt"]
        task = self.store.enqueue(owner,request_id,context_id,request,self.identity)
        return public_task(task)

    def process(self, identifier):
        while not self.stop.is_set():
            task = self.store.get(identifier)
            if not task or task["state"] in TERMINAL:
                return
            try:
                if task["cancel_requested"] and not task["attempted"]:
                    self.store.update(identifier,state="canceled",diagnostic=None)
                    return
                if not task["session"]:
                    session = self.store.session_for_context(task)
                    if not session:
                        identity = "context-"+hashlib.sha256(
                            json.dumps([task["owner"],task["context_id"]],separators=(",",":")).encode()
                        ).hexdigest()[:48]
                        session = self.backend.create_session(identity)
                    task = self.store.update(identifier,session=session)
                if not task["actor_uid"]:
                    operation = self.backend.identify_session if "fixture" in task["request"] else self.backend.bind_session
                    task = self.store.update(identifier,actor_uid=operation(task["session"]))
                if not task["native_task"]:
                    native = self.backend.find_task(task) if task["attempted"] else None
                    if native is None and task["attempted"]:
                        if task["state"] != "uncertain":
                            task = self.store.update(identifier,state="uncertain",
                                diagnostic="delivery response was lost; observing stable message identity without replay")
                        elif time.time()-task["updated"] > 120:
                            self.store.update(identifier,state="failed",
                                diagnostic="no accepted native task observed after uncertain delivery; caller may retry with a new request")
                            return
                        time.sleep(self.poll_seconds)
                        continue
                    if native is None:
                        task = self.store.update(identifier,attempted=True,state="dispatching")
                        try:
                            result = self.backend.send(task)
                        except NotAccepted:
                            self.store.update(identifier,attempted=False,state="queued",
                                available_at=time.time()+2,
                                diagnostic="Kagent did not accept input; stable delivery will retry")
                            return
                        except Exception:
                            self.store.update(identifier,state="uncertain",
                                diagnostic="delivery outcome uncertain; observing without replay")
                            time.sleep(self.poll_seconds)
                            continue
                        native = result.get("task",result)
                    task = self.store.update(identifier,native_task=native["id"],state="active",diagnostic=None)
                if task["cancel_requested"]:
                    self.backend.cancel(task)
                native = self.backend.get_task(task)
                state = native.get("status",{}).get("state")
                if state in {"TASK_STATE_COMPLETED","TASK_STATE_FAILED","TASK_STATE_CANCELED","TASK_STATE_REJECTED"}:
                    target = {"TASK_STATE_COMPLETED":"completed","TASK_STATE_CANCELED":"canceled"}.get(state,"failed")
                    self.store.update(identifier,state=target,result=native,diagnostic=None)
                    return
            except Exception:
                self.store.update(identifier,diagnostic="Kagent temporarily unavailable; task retained")
                self.backend.close()
            time.sleep(self.poll_seconds)

    def schedule(self):
        while not self.stop.is_set():
            with self.lock:
                for identifier,future in list(self.running.items()):
                    if future.done():
                        self.running.pop(identifier)
                available = self.store.maximum-len(self.running)
                candidates = [
                    task for task in self.store.active()
                    if task["id"] not in self.running
                ][:available]
                while len(candidates)<available:
                    task = self.store.claim()
                    if not task:
                        break
                    candidates.append(task)
                for task in candidates:
                    self.running[task["id"]] = self.executor.submit(self.process,task["id"])
            self.stop.wait(1)

    def cancel(self, identifier, owner):
        return public_task(self.store.cancel(identifier,owner))

    def close(self):
        self.stop.set()
        self.executor.shutdown(wait=True,cancel_futures=False)
        self.backend.close()
        self.store.close()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self,address,gateway,clients):
        super().__init__(address,Handler)
        self.gateway,self.clients = gateway,clients


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self,*_):
        pass
    def reply(self,status,value):
        data=json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        self.wfile.write(data)
    def client(self):
        values=self.headers.get_all("Authorization",[])
        if len(values)!=1 or not values[0].startswith("Bearer "):
            return None
        return self.server.clients.get(hashlib.sha256(values[0][7:].encode()).hexdigest())
    def do_GET(self):
        if self.path=="/readyz":
            return self.reply(200,{"ready":True,"gateway":self.server.gateway.store.gateway})
        if self.path=="/.well-known/agent-card.json":
            card=self.server.gateway.settings["agentCard"]
            return self.reply(200,{
                "name":card["name"],"description":card["description"],
                "version":"1","capabilities":{"streaming":False,"pushNotifications":False},
                "supportedInterfaces":[{"url":card["url"],
                    "protocolBinding":"JSONRPC","protocolVersion":"1.0.0"}],
                "defaultInputModes":["text/plain","application/json"],
                "defaultOutputModes":["application/json"],
                "securitySchemes":{"bearer":{"httpAuthSecurityScheme":{"scheme":"bearer"}}},
                "security":[{"bearer":[]}],
                "skills":[card["skill"]],
            })
        extension=getattr(self.server.gateway,"handle_get",None)
        if extension and extension(self):
            return
        self.reply(404,{"error":"not_found"})
    def do_POST(self):
        try:
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length",[]))!=1:
                return self.reply(400,{"error":"invalid_request_framing"})
            length=int(self.headers["Content-Length"])
            if not 0<=length<=20000:
                return self.reply(413,{"error":"request_too_large"})
            body=json.loads(self.rfile.read(length))
            client=self.client()
            if not client:
                return self.reply(401,{"error":"unauthenticated"})
            if self.path!="/a2a" or body.get("jsonrpc")!="2.0":
                return self.reply(404,{"error":"not_found"})
            method,params=body.get("method"),body.get("params",{})
            if not isinstance(params,dict):
                raise ValueError("parameters object required")
            gateway=self.server.gateway
            if method=="SendMessage":
                result={"task":gateway.submit(client["owner"],params["message"],client.get("fixture",False))}
            elif method=="ListTasks":
                size=params.get("pageSize",50)
                if isinstance(size,bool) or not isinstance(size,int) or not 1<=size<=100:
                    raise ValueError("bounded page size required")
                rows=gateway.store.list(client["owner"])
                if params.get("contextId"):
                    rows=[row for row in rows if row["context_id"]==params["contextId"]]
                total=len(rows)
                cursor=params.get("pageToken")
                if cursor:
                    positions=[index for index,row in enumerate(rows) if row["id"]==cursor]
                    if not positions:
                        raise ValueError("owned page cursor required")
                    rows=rows[positions[0]+1:]
                page=rows[:size]
                values=[public_task(row) for row in page]
                if not params.get("includeArtifacts",False):
                    for value in values:
                        value.pop("artifacts",None)
                result={"tasks":values,"totalSize":total,"pageSize":size,
                    "nextPageToken":page[-1]["id"] if len(rows)>size else ""}
            elif method in {"GetTask","CancelTask"}:
                task=gateway.store.get(params.get("id"),client["owner"])
                if not task:
                    return self.reply(200,{"jsonrpc":"2.0","id":body.get("id"),
                        "error":{"code":-32001,"message":"Task not found"}})
                result=gateway.cancel(task["id"],client["owner"]) if method=="CancelTask" else public_task(task)
            else:
                return self.reply(200,{"jsonrpc":"2.0","id":body.get("id"),
                    "error":{"code":-32601,"message":"Method not supported"}})
            self.reply(200,{"jsonrpc":"2.0","id":body.get("id"),"result":result})
        except KeyError:
            self.reply(404,{"error":"not_found"})
        except (ValueError,TypeError,Conflict):
            self.reply(400,{"error":"invalid_or_conflicting_request"})
        except Exception:
            self.reply(503,{"error":"service_unavailable; retry with the same request identity"})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    store=Store(config["postgres"],config["maximum"],config["backlog"],
        config["gatewayName"],config["taskPrefix"])
    store.acquire_scheduler()
    gateway=Gateway(store,Backend(config),config["runtimeIdentity"],settings=config)
    server=Server((config.get("listenAddress","10.82.0.2"),config["listenPort"]),gateway,config["clients"])
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version=ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(config["certificate"],config["key"])
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    scheduler=threading.Thread(target=gateway.schedule,daemon=True)
    scheduler.start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        gateway.close()


if __name__=="__main__":
    main()

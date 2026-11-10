import json
import threading
import time
import unittest

from gateway_service import Gateway
from backend import Backend


class FakeStore:
    maximum=3
    gateway="test"
    def __init__(self):
        self.rows={}
        self.contexts={}
        self.lock=threading.Lock()
    def enqueue(self,owner,request_id,context_id,request,_identity):
        identifier="coding-"+request_id
        old=self.rows.get(identifier)
        if old:
            return old
        row={"id":identifier,"owner":owner,"request_id":request_id,
            "gateway":"test",
            "context_id":context_id or identifier,"request":request,"state":"queued",
            "session":None,"actor_uid":None,"native_task":None,"attempted":False,
            "cancel_requested":False,"result":None,"diagnostic":None,
            "created":time.time(),"updated":time.time()}
        self.rows[identifier]=row
        return dict(row)
    def get(self,identifier,owner=None):
        row=self.rows.get(identifier)
        return dict(row) if row and (owner is None or row["owner"]==owner) else None
    def update(self,identifier,**values):
        self.rows[identifier].update(values,updated=time.time())
        return self.get(identifier)
    def session_for_context(self,task):
        for row in self.rows.values():
            if row["id"]!=task["id"] and row["owner"]==task["owner"] and row["context_id"]==task["context_id"] and row["session"]:
                return row["session"]
        return None
    def active(self):
        return [self.get(row["id"]) for row in self.rows.values() if row["state"] in {"dispatching","active","uncertain"}]
    def claim(self):
        active=self.active()
        if len(active)>=self.maximum:
            return None
        for row in self.rows.values():
            if row["state"]=="queued" and not any(
                other["owner"]==row["owner"] and other["context_id"]==row["context_id"] for other in active
            ):
                row["state"]="dispatching"
                return self.get(row["id"])
        return None
    def cancel(self,identifier,owner):
        row=self.rows[identifier]
        row["cancel_requested"]=True
        if row["state"]=="queued":
            row["state"]="canceled"
        return self.get(identifier,owner)
    def list(self,owner=None):
        return [self.get(row["id"]) for row in self.rows.values() if owner is None or row["owner"]==owner]
    def close(self):
        pass


class FakeBackend:
    def __init__(self):
        self.sessions=0
        self.tasks={}
        self.sends=[]
    def create_session(self,identity):
        self.sessions+=1
        return "session-"+identity
    def identify_session(self,session):
        return "actor-"+session
    bind_session=identify_session
    def find_task(self,task):
        return self.tasks.get(task["id"])
    def send(self,task):
        self.sends.append(task["id"])
        native={"id":"native-"+task["id"],"status":{"state":"TASK_STATE_WORKING"},
            "history":[{"messageId":task["id"]}]}
        self.tasks[task["id"]]=native
        return {"task":native}
    def get_task(self,task):
        return self.tasks[task["id"]]
    def cancel(self,task):
        self.tasks[task["id"]]["status"]["state"]="TASK_STATE_CANCELED"
    def close(self):
        pass


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.store,self.backend=FakeStore(),FakeBackend()
        self.gateway=Gateway(self.store,self.backend,{"image":"immutable"},poll_seconds=.01)
        self.addCleanup(self.gateway.close)
    def submit(self,name,context=None):
        message={"messageId":name,"parts":[{"data":{"fixture":name}}]}
        if context:
            message["contextId"]=context
        return self.gateway.submit("owner",message,True)
    def test_three_concurrent_and_same_context_serialization(self):
        tasks=[self.submit(str(index)) for index in range(7)]
        same=[self.submit("same-a","shared"),self.submit("same-b","shared")]
        thread=threading.Thread(target=self.gateway.schedule,daemon=True)
        thread.start()
        deadline=time.time()+5
        peak=0
        while time.time()<deadline:
            active=[row for row in self.store.rows.values() if row["state"]=="active"]
            peak=max(peak,len(active))
            self.assertLessEqual(len(active),3)
            shared=[row for row in active if row["context_id"]=="shared"]
            self.assertLessEqual(len(shared),1)
            for row in active:
                self.backend.tasks[row["id"]]["status"]["state"]="TASK_STATE_COMPLETED"
            if all(row["state"]=="completed" for row in self.store.rows.values()):
                break
            time.sleep(.02)
        self.assertEqual(peak,3)
        self.assertEqual(len(self.backend.sends),9)
        self.assertEqual(self.store.get(same[0]["id"])["session"],self.store.get(same[1]["id"])["session"])
    def test_queued_cancel_never_dispatches(self):
        task=self.submit("cancel")
        self.gateway.cancel(task["id"],"owner")
        self.assertEqual(self.store.get(task["id"])["state"],"canceled")
        self.assertEqual(self.backend.sends,[])
    def test_request_validation_and_context(self):
        task=self.submit("one","context-one")
        self.assertEqual(task["contextId"],"context-one")
        scoped=self.gateway.submit("owner",{"messageId":"scoped","parts":[{"data":{
            "prompt":"inspect","workingDirectory":"src/component"}}]},True)
        self.assertEqual("src/component",self.store.get(scoped["id"])["request"]["workingDirectory"])
        explicit_root=self.gateway.submit("owner",{"messageId":"explicit-root","parts":[{"data":{
            "prompt":"inspect","workingDirectory":"."}}]},True)
        self.assertNotIn("workingDirectory",self.store.get(explicit_root["id"])["request"])
        with self.assertRaises(ValueError):
            self.gateway.submit("owner",{"messageId":"bad","contextId":"contains space",
                "parts":[{"data":{"fixture":"x"}}]},True)
        with self.assertRaises(ValueError):
            self.gateway.submit("owner",{"messageId":"escape","parts":[{"data":{
                "prompt":"inspect","workingDirectory":"../outside"}}]},True)
        with self.assertRaises(ValueError):
            self.gateway.submit("owner",{"messageId":"alias","parts":[{"data":{
                "prompt":"inspect","workingDirectory":"src./component"}}]},True)

    def test_backend_preserves_selected_working_directory_in_native_payload(self):
        backend=Backend.__new__(Backend)
        captured=[]
        backend.a2a=lambda method,params: captured.append((method,params)) or {"task":{}}
        backend.send({"id":"coding-scope","session":"session-id","request":{
            "prompt":"inspect","workingDirectory":"src/component","allowEdits":False}})
        sent=json.loads(captured[0][1]["message"]["parts"][0]["text"])
        self.assertEqual("src/component",sent["workingDirectory"])
        self.assertFalse(sent["allowEdits"])

    def test_role_gateway_enforces_read_only_authority_and_instruction(self):
        gateway=Gateway(self.store,self.backend,{"image":"immutable"},poll_seconds=.01,
            settings={"fixedAllowEdits":False,"roleInstruction":"Act only as reviewer."})
        task=gateway.submit("owner",{"messageId":"review","parts":[{"data":{"prompt":"Review this."}}]})
        stored=self.store.get(task["id"])
        self.assertFalse(stored["request"]["allowEdits"])
        self.assertTrue(stored["request"]["prompt"].startswith("Act only as reviewer."))
        with self.assertRaises(ValueError):
            gateway.submit("owner",{"messageId":"unsafe","parts":[{"data":{
                "prompt":"Edit this.","allowEdits":True}}]})
        gateway.close()


if __name__=="__main__":
    unittest.main()

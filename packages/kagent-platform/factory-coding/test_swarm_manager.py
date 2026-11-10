import http.server
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from swarm_manager import (DashboardServer, SwarmManager, parse_json, public_pr_urls,
    validate_pr_url, validation_succeeded)
from swarm_autonomous import campaign_identity
from otel_exporter import OtelExporter


def completed(answer):
    execution={"status":"completed","answer":json.dumps(answer),"tools":["repo_read"]}
    receipt={"detail":json.dumps(execution)}
    native={"status":{"message":{"parts":[{"text":json.dumps(receipt)}]}}}
    return {"id":"child","contextId":"context","status":{"state":"TASK_STATE_COMPLETED"},
        "artifacts":[{"parts":[{"text":json.dumps(native)}]}]}


class FakeStore:
    maximum=1
    gateway="swarm-manager"
    def __init__(self):
        self.rows={}
    def enqueue(self,owner,request_id,context_id,request,_identity):
        identifier="campaign-"+request_id
        self.rows.setdefault(identifier,{"gateway":"swarm-manager","id":identifier,"owner":owner,
            "request_id":request_id,"context_id":context_id,"request":request,"state":"queued",
            "session":None,"actor_uid":None,"native_task":None,"attempted":False,
            "cancel_requested":False,"result":None,"diagnostic":None,
            "created":time.time(),"updated":time.time()})
        return dict(self.rows[identifier])
    def get(self,identifier,owner=None):
        row=self.rows.get(identifier)
        return dict(row) if row and (owner is None or row["owner"]==owner) else None
    def update(self,identifier,**values):
        self.rows[identifier].update(values,updated=time.time())
        return self.get(identifier)
    def active(self):
        return [self.get(row["id"]) for row in self.rows.values()
            if row["state"] in {"dispatching","active","uncertain"}]
    def claim(self):
        for row in self.rows.values():
            if row["state"]=="queued":
                row["state"]="dispatching"
                return self.get(row["id"])
        return None
    def cancel(self,identifier,owner):
        self.rows[identifier]["cancel_requested"]=True
        return self.get(identifier,owner)
    def list(self,owner=None):
        return [self.get(row["id"]) for row in self.rows.values()]
    def close(self):
        pass


class FakeRoles:
    def __init__(self):
        self.submissions={}
        self.tasks={}
    def submit(self,role,message_id,prompt,context_id=None,**data):
        self.submissions[message_id]={"role":role,"prompt":prompt,"context":context_id,"data":data}
        task={"id":message_id,"contextId":context_id or message_id,
            "status":{"state":"TASK_STATE_SUBMITTED"}}
        self.tasks[message_id]=task
        return task
    def get(self,role,identifier):
        return self.tasks[identifier]
    def wait(self,role,identifier,cancelled,timeout=2400):
        if role=="pr-pattern-miner":
            return completed({"coverage":{"prs":3,"threads":5,"files":7,"searches":2},
                "candidates":[{"pattern":"Repeated cleanup","evidence":["PR 1"]}]})
        if role=="opportunity-scout":
            return completed({"candidates":[
                {"title":"First","problem":"p1","evidence":[],"evidenceStrength":4,
                    "generality":4,"falsePositiveRisk":1,"implementationFit":5},
                {"title":"Second","problem":"p2","evidence":[],"evidenceStrength":3,
                    "generality":3,"falsePositiveRisk":2,"implementationFit":4}]})
        if role=="planner":
            return completed({"proposedRule":{"id":"SM4001"},"tests":["positive","negative"]})
        if role=="plan-reviewer":
            return completed({"decision":"approved","blockingFindings":[]})
        raise AssertionError(role)


class RevisionRoles(FakeRoles):
    def wait(self,role,identifier,cancelled,timeout=2400):
        if role=="plan-reviewer":
            if "plan-rereview-0-1" in identifier:
                return completed({"decision":"approved","blockingFindings":[]})
            return completed({"decision":"changes_requested",
                "blockingFindings":[{"finding":"Preserve existing registration",
                    "requiredChange":"Keep the unconditional action."}]})
        return super().wait(role,identifier,cancelled,timeout)


class OtlpCapture(http.server.BaseHTTPRequestHandler):
    payloads=[]
    def log_message(self,*_):
        pass
    def do_POST(self):
        body=self.rfile.read(int(self.headers["Content-Length"]))
        self.payloads.append(json.loads(body))
        self.send_response(200)
        self.send_header("Content-Length","2")
        self.end_headers()
        self.wfile.write(b"{}")


class SwarmManagerTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        token=Path(self.directory.name)/"token"
        token.write_text("test")
        self.store=FakeStore()
        self.manager=SwarmManager(self.store,{"roleEndpoints":{},"roleToken":str(token),
            "ca":str(token),"agentCard":{"name":"manager"},
            "deploymentProjection":False},roles=FakeRoles())
        self.addCleanup(self.manager.close)
    def test_planning_campaign_uses_child_a2a_tasks_and_completes(self):
        task=self.manager.submit("owner",{"messageId":"one","parts":[{"data":{
            "campaign":"demo-one","goal":"Find two conservative analyzer ideas.",
            "candidateLimit":2,"implementationLimit":0,"execute":False}}]})
        self.manager.process(task["id"])
        row=self.store.get(task["id"])
        self.assertEqual(row["state"],"completed")
        self.assertEqual(len(row["result"]["plans"]),2)
        self.assertEqual(len(row["result"]["approvedPlans"]),2)
        self.assertEqual({child["role"] for child in row["result"]["children"].values()},
                         {"opportunity-scout","planner","plan-reviewer"})
        public=self.manager.public_campaigns()[0]["manifest"]
        self.assertEqual(public["outcome"],"Planning run completed")
        self.assertEqual(public["stages"],["scouting","planning","plan-review"])
        self.assertTrue(public["sequence"])
        self.assertEqual(public["sequence"][0]["source"],"swarm-manager")
        self.assertEqual(public["sequence"][0]["target"],"opportunity-scout")
        self.assertTrue(any(value["node"]=="unknown" for value in public["placements"]))
    def test_autonomous_discovery_mines_before_scouting_and_uses_inventory(self):
        task=self.manager.submit("owner",{"messageId":"auto","parts":[{"data":{
            "campaign":"auto-demo","goal":"Find generalized cleanup opportunities.",
            "candidateLimit":1,"implementationLimit":0,"execute":False,
            "autonomousDiscovery":True,"lookbackDays":5,"sourceLimit":8}}]})

        self.manager.process(task["id"])

        row=self.store.get(task["id"])
        self.assertEqual(row["state"],"completed")
        self.assertIn("evidence-crawl",row["result"]["children"])
        keys=list(row["result"]["children"])
        self.assertLess(keys.index("evidence-crawl"),keys.index("scout"))
        scout=next(value for value in self.manager.roles.submissions.values()
            if value["role"]=="opportunity-scout")
        self.assertIn("Evidence-miner and triage signals",scout["prompt"])
        self.assertIn("Prior campaign inventory",scout["prompt"])
        self.assertEqual(row["result"]["candidates"][0]["priorityScore"],23)
    def test_planning_allows_two_bounded_revision_rounds(self):
        manager=SwarmManager(self.store,self.manager.config,roles=RevisionRoles())
        self.addCleanup(manager.close)
        task=manager.submit("owner",{"messageId":"revision","parts":[{"data":{
            "campaign":"revision-demo","goal":"Exercise bounded plan revision.",
            "candidateLimit":1,"implementationLimit":0,"execute":False}}]})

        manager.process(task["id"])

        row=self.store.get(task["id"])
        self.assertEqual(row["state"],"completed")
        self.assertEqual(len(row["result"]["approvedPlans"]),1)
        self.assertIn("plan-revision-0-1",row["result"]["children"])
        self.assertIn("plan-rereview-0-1",row["result"]["children"])
        prompt=next(value["prompt"] for key,value in manager.roles.submissions.items()
            if "plan-revision-0-1" in key)
        self.assertIn("map each blocking finding",prompt)
    def test_campaign_validation_and_fenced_json(self):
        self.assertEqual(campaign_identity(0),("auto-19700101-00","autonomous-auto-19700101-00"))
        self.assertEqual(parse_json("```json\n{\"ok\":true}\n```"),{"ok":True})
        self.assertTrue(validation_succeeded({"validation":{"ok":True}}))
        self.assertFalse(validation_succeeded({"validation":{"ok":False}}))
        self.assertEqual(validate_pr_url(
            "https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/123"),
            "https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/123")
        with self.assertRaises(RuntimeError):
            validate_pr_url("https://dev.azure.com/other/project/_git/repo/pullrequest/123")
        self.assertEqual(public_pr_urls({"receipt":{
            "pullRequestUrl":"https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/123"},
            "other":"https://example.com/pullrequest/999"}),
            ["https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/123"])
        self.assertEqual(self.manager.produced_pr_urls({"children":{
            "scout":{"role":"opportunity-scout","answer":{"evidence":
                "https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/111"}},
            "code-0":{"role":"coding","answer":{"pullRequestUrl":
                "https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/222"}}}}),
            ["https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/222"])
        unsafe={"disposition":"Reject as unsafe","implementationTrack":"inventory-guard"}
        self.assertFalse(self.manager.candidate_execution(unsafe))
        self.assertEqual(unsafe["decision"],"reject")
        analyzer={"decision":"promote","implementationTrack":"slopmop-analyzer"}
        self.assertTrue(self.manager.candidate_execution(analyzer))
        with self.assertRaises(ValueError):
            self.manager.submit("owner",{"messageId":"bad","parts":[{"data":{
                "campaign":"x","goal":"g"}}]})
        with self.assertRaises(ValueError):
            self.manager.submit("owner",{"messageId":"bad-window","parts":[{"data":{
                "campaign":"demo-window","goal":"g","autonomousDiscovery":True,
                "lookbackDays":31}}]})
    def test_otel_exporter_emits_safe_correlated_lifecycle_spans(self):
        OtlpCapture.payloads=[]
        server=http.server.ThreadingHTTPServer(("127.0.0.1",0),OtlpCapture)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        exporter=OtelExporter("http://127.0.0.1:"+str(server.server_port)+"/v1/traces")

        exporter.emit("campaign-task","child_completed",10.0,{
            "campaign.id":"demo","swarm.role":"planner","swarm.task":"plan-1",
            "eventIndex":2},first_time=1.0,terminal=False)

        self.assertEqual(exporter.status()["emitted"],1)
        span=OtlpCapture.payloads[0]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        self.assertEqual(span["name"],"swarm.child_completed")
        attributes={item["key"] for item in span["attributes"]}
        self.assertIn("campaign.id",attributes)
        self.assertNotIn("prompt",attributes)
    def test_role_task_recovers_completed_child_without_resubmission(self):
        identifier="campaign-existing"
        self.store.rows[identifier]={"gateway":"swarm-manager","id":identifier,"owner":"owner",
            "request_id":"existing","context_id":"campaign-existing","request":{},"state":"failed",
            "session":None,"actor_uid":None,"native_task":None,"attempted":False,
            "cancel_requested":False,"result":None,"diagnostic":None,
            "created":time.time(),"updated":time.time()}
        child=completed({"decision":"approved"})
        child["id"]="review-existing"
        child["contextId"]="review-context"
        self.manager.roles.tasks[child["id"]]=child
        manifest={"children":{"review":{"role":"plan-reviewer","task":child["id"],
            "context":child["contextId"]}}}

        answer,context=self.manager.role_task(identifier,manifest,"plan-reviewer","review","unused")

        self.assertEqual(answer,{"decision":"approved"})
        self.assertEqual(context,"review-context")
        self.assertEqual(self.manager.roles.submissions,{})
        self.assertEqual(manifest["events"][-1]["event"],"child_recovered")
    def test_host_dashboard_exposes_only_read_only_projection(self):
        server=DashboardServer(("127.0.0.1",0),self.manager)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin="http://127.0.0.1:"+str(server.server_port)

        with urllib.request.urlopen(origin+"/dashboard",timeout=5) as response:
            body=response.read()
            self.assertIn(b"SlopMop named-agent swarm",body)
            self.assertIn(b"Campaign sequence and placement",body)
            self.assertIn(b"Campaign View",body)
            self.assertIn(b"Current Cluster",body)
            self.assertIn(b"campaign-select",body)
            self.assertIn(b"Instances by node",body)
            self.assertIn(b"node-stacks",body)
            self.assertIn(b"Swarm Runs",body)
            self.assertIn(b"A campaign is one parent swarm run",body)
        with urllib.request.urlopen(origin+"/api/campaigns",timeout=5) as response:
            projection=json.load(response)
        self.assertEqual(projection["campaigns"],[])
        self.assertEqual(len(projection["topology"]["agents"]),9)
        self.assertEqual(projection["topology"]["deployment"],
            {"nodes":[],"parked":[],"capturedAt":None})
        coding=next(agent for agent in projection["topology"]["agents"] if agent["id"]=="coding")
        self.assertEqual(coding["name"],"Coding Agent")
        self.assertIn("factory-coder",coding["runtime"])
        self.assertTrue(any(edge["source"]=="coding" and edge["target"]=="implementation-reviewer"
            for edge in projection["topology"]["edges"]))
        request=urllib.request.Request(origin+"/a2a",data=b"{}",method="POST")
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request,timeout=5)
        self.assertEqual(error.exception.code,404)
        error.exception.close()


if __name__=="__main__":
    unittest.main()

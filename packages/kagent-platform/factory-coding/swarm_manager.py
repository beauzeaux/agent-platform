"""Deterministic A2A Swarm Manager using specialized role agents."""
import argparse
import concurrent.futures
import hashlib
import html
import http.server
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from gateway_service import Handler, Server, public_task
from gateway_store import Conflict, Store, TERMINAL
from otel_exporter import OtelExporter
from swarm_roles import ROLES


AGENT_NAMES={
    "swarm-manager":"Swarm Manager",
    "opportunity-scout":"Opportunity Scout",
    "planner":"Implementation Planner",
    "plan-reviewer":"Plan Reviewer",
    "coding":"Coding Agent",
    "implementation-reviewer":"Implementation Reviewer",
    "documentation-steward":"Documentation Steward",
    "ado-triage":"ADO Triage Agent",
    "pr-pattern-miner":"Evidence Miner",
}

FLOW_EDGES=[
    ("swarm-manager","ado-triage","triage scoped work"),
    ("swarm-manager","pr-pattern-miner","mine approved PRs"),
    ("ado-triage","opportunity-scout","candidate signal"),
    ("pr-pattern-miner","opportunity-scout","pattern signal"),
    ("swarm-manager","opportunity-scout","start campaign"),
    ("opportunity-scout","planner","evidence-backed candidate"),
    ("planner","plan-reviewer","implementation plan"),
    ("plan-reviewer","planner","requested revision"),
    ("plan-reviewer","coding","approved specification"),
    ("coding","implementation-reviewer","PR and validation receipts"),
    ("implementation-reviewer","coding","blocking findings"),
    ("coding","documentation-steward","completed implementation"),
    ("documentation-steward","swarm-manager","documentation receipt"),
]


def parse_json(text):
    value=text.strip()
    if value.startswith("```json") and value.endswith("```"):
        value=value[7:-3].strip()
    return json.loads(value)


def native_answer(task):
    artifact=task.get("artifacts",[{}])[0]
    native=json.loads(artifact["parts"][0]["text"])
    receipt=json.loads(native["status"]["message"]["parts"][0]["text"])
    execution=json.loads(receipt["detail"])
    if execution.get("status")!="completed" or not execution.get("answer"):
        raise RuntimeError("role execution did not produce a completed answer")
    return execution["answer"],execution


def find_value(value,names):
    if isinstance(value,dict):
        for name in names:
            candidate=value.get(name)
            if candidate is not None and candidate!="":
                return candidate
        for child in value.values():
            found=find_value(child,names)
            if found is not None and found!="":
                return found
    if isinstance(value,list):
        for child in value:
            found=find_value(child,names)
            if found is not None and found!="":
                return found
    return None


def validate_pr_url(value):
    if not isinstance(value,str):
        raise RuntimeError("pull request URL required")
    parsed=urllib.parse.urlparse(value)
    segments=[urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if parsed.scheme!="https" or (parsed.hostname or "").lower()!="dev.azure.com" or parsed.fragment:
        raise RuntimeError("unapproved pull request URL")
    expected=["msasg","Engineering Fundamentals","_git","engsys","pullrequest"]
    if segments[:5]!=expected or len(segments)!=6 or not segments[-1].isdigit():
        raise RuntimeError("unapproved pull request URL")
    return value


def validation_succeeded(value):
    if isinstance(value,dict):
        if value.get("ok") is True:
            return True
        return any(validation_succeeded(child) for child in value.values())
    if isinstance(value,list):
        return any(validation_succeeded(child) for child in value)
    if isinstance(value,str):
        try:
            return validation_succeeded(json.loads(value))
        except ValueError:
            return False
    return False


def public_pr_urls(value):
    found=set()
    def visit(item):
        if isinstance(item,dict):
            for child in item.values():
                visit(child)
        elif isinstance(item,list):
            for child in item:
                visit(child)
        elif isinstance(item,str):
            for candidate in re.findall(
                r"https://dev\.azure\.com/msasg/Engineering(?:%20| )Fundamentals/_git/engsys/pullrequest/\d+",
                item):
                try:
                    found.add(validate_pr_url(candidate))
                except RuntimeError:
                    pass
    visit(value)
    return sorted(found,key=lambda url:int(url.rsplit("/",1)[-1]))


class RoleClient:
    def __init__(self,config):
        self.endpoints=config["roleEndpoints"]
        self.token=Path(config["roleToken"]).read_text().strip()
        self.context=ssl.create_default_context(cafile=config["ca"])

    def rpc(self,role,method,params):
        request=urllib.request.Request(self.endpoints[role]+"/a2a",
            data=json.dumps({"jsonrpc":"2.0","id":str(uuid.uuid4()),
                "method":method,"params":params}).encode(),
            headers={"Content-Type":"application/json","Authorization":"Bearer "+self.token})
        for attempt in range(20):
            try:
                with urllib.request.urlopen(request,context=self.context,timeout=300) as response:
                    return json.load(response)
            except (urllib.error.URLError,TimeoutError):
                if attempt==19:
                    raise
                time.sleep(2)

    def submit(self,role,message_id,prompt,context_id=None,**data):
        message={"messageId":message_id,"role":"ROLE_USER",
            "parts":[{"data":{"prompt":prompt,**data}}]}
        if context_id:
            message["contextId"]=context_id
        response=self.rpc(role,"SendMessage",{"message":message})
        if "error" in response:
            raise RuntimeError("role submission rejected: "+json.dumps(response["error"]))
        return response["result"]["task"]

    def get(self,role,identifier):
        response=self.rpc(role,"GetTask",{"id":identifier})
        if "error" in response:
            raise RuntimeError("role task disappeared")
        return response["result"]

    def wait(self,role,identifier,cancelled,timeout=2400):
        deadline=time.monotonic()+timeout
        canceled=False
        while time.monotonic()<deadline:
            if cancelled() and not canceled:
                self.rpc(role,"CancelTask",{"id":identifier})
                canceled=True
            task=self.get(role,identifier)
            state=task["status"]["state"]
            if state in {"TASK_STATE_COMPLETED","TASK_STATE_FAILED","TASK_STATE_CANCELED","TASK_STATE_REJECTED"}:
                if state!="TASK_STATE_COMPLETED":
                    raise RuntimeError(role+" task ended "+state)
                return task
            time.sleep(3)
        raise RuntimeError(role+" task deadline")


class SwarmManager:
    def __init__(self,store,config,roles=None):
        self.store,self.config=store,config
        self.roles=roles or RoleClient(config)
        self.stop=threading.Event()
        self.executor=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix="swarm-manager")
        self.running={}
        self.lock=threading.Lock()
        self.manifest_lock=threading.Lock()
        self.deployment_lock=threading.Lock()
        self.deployment_cache=None
        self.deployment_cache_at=0
        self.telemetry=OtelExporter(config.get("otelHttpEndpoint"))
        self.settings={"agentCard":config["agentCard"]}

    def submit(self,owner,message,fixture_allowed=False):
        request_id=message.get("messageId")
        if not isinstance(request_id,str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}",request_id):
            raise ValueError("stable messageId required")
        parts=message.get("parts")
        request=parts[0].get("data") if isinstance(parts,list) and len(parts)==1 and isinstance(parts[0],dict) else None
        allowed={"campaign","goal","candidateLimit","implementationLimit","execute",
                 "approvedPrUrls","triageWorkItemIds","autonomousDiscovery",
                 "lookbackDays","sourceLimit"}
        if not isinstance(request,dict) or set(request)-allowed:
            raise ValueError("bounded campaign request required")
        if not isinstance(request.get("campaign"),str) or not re.fullmatch(r"[a-z0-9-]{3,48}",request["campaign"]):
            raise ValueError("campaign identifier required")
        if not isinstance(request.get("goal"),str) or not 0<len(request["goal"].strip())<=8000:
            raise ValueError("bounded campaign goal required")
        for field,default,maximum in [("candidateLimit",3,6),("implementationLimit",1,3)]:
            value=request.get(field,default)
            if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=maximum:
                raise ValueError("invalid campaign limit")
            request[field]=value
        if not isinstance(request.get("execute",False),bool):
            raise ValueError("invalid execute flag")
        request.setdefault("execute",False)
        if not isinstance(request.get("autonomousDiscovery",False),bool):
            raise ValueError("invalid autonomous discovery flag")
        request.setdefault("autonomousDiscovery",False)
        for field,default,maximum in [("lookbackDays",7,30),("sourceLimit",12,30)]:
            value=request.get(field,default)
            if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=maximum:
                raise ValueError("invalid discovery bound")
            request[field]=value
        for field in ["approvedPrUrls","triageWorkItemIds"]:
            request.setdefault(field,[])
            if not isinstance(request[field],list) or len(request[field])>10:
                raise ValueError("invalid campaign evidence list")
        if any(not isinstance(value,str) or len(value)>1000 for value in request["approvedPrUrls"]):
            raise ValueError("invalid approved PR URL")
        if any(isinstance(value,bool) or not isinstance(value,int) or value<=0
               for value in request["triageWorkItemIds"]):
            raise ValueError("invalid triage work item")
        context_id=message.get("contextId") or "campaign-"+request["campaign"]
        task=self.store.enqueue(owner,request_id,context_id,request,
            {"managerVersion":"1","roles":sorted(self.config["roleEndpoints"])})
        return public_task(task)

    def canceled(self,identifier):
        task=self.store.get(identifier)
        return not task or task["cancel_requested"]

    def event(self,identifier,manifest,event,**details):
        with self.manifest_lock:
            occurred=time.time()
            events=manifest.setdefault("events",[])
            events.append({"time":occurred,"event":event,**details})
            manifest["events"]=manifest["events"][-500:]
            self.store.update(identifier,result=manifest)
        safe={"campaign.id":manifest.get("campaign"),"campaign.stage":manifest.get("state"),
            "eventIndex":len(events)-1}
        for key,value in details.items():
            if key in {"role","key","task","context","stage","count","approved",
                       "total","implementations","index"}:
                safe["swarm."+key]=value
        self.telemetry.emit(identifier,event,occurred,safe,
            first_time=events[0]["time"] if events else occurred,
            terminal=event in {"campaign_completed","campaign_failed"})

    def stage(self,identifier,manifest,name):
        manifest["state"]=name
        self.event(identifier,manifest,"stage_changed",stage=name)

    def child_placement(self,role,task_id):
        try:
            with self.store.connection() as db:
                row=db.execute(
                    """SELECT session FROM factory.gateway_tasks
                       WHERE gateway=%s AND id=%s""",(role,task_id)).fetchone()
            if not row or not row["session"]:
                return {}
            session=str(row["session"])
            env=dict(os.environ,KUBECONFIG="/var/snap/microk8s/current/credentials/client.config")
            actor=json.loads(subprocess.check_output([
                "/home/nibeauli/kagent-lab/artifacts/kubectl-ate","--preview",
                "get","actor","session-"+session,"-a","factory-executions","-o","json"],
                env=env,text=True,stderr=subprocess.DEVNULL,timeout=15))
            return {"session":session,"node":actor.get("status",{}).get("assignedNode")}
        except Exception:
            return {}

    def role_task(self,identifier,manifest,role,key,prompt,context_id=None,**data):
        existing=manifest.get("children",{}).get(key)
        if existing and "answer" in existing:
            return existing["answer"],existing["context"]
        if existing:
            task=self.roles.get(role,existing["task"])
            state=task["status"]["state"]
            if state=="TASK_STATE_COMPLETED":
                answer,execution=native_answer(task)
                parsed=parse_json(answer)
                self.event(identifier,manifest,"child_recovered",role=role,key=key,task=task["id"])
                placement=self.child_placement(role,task["id"])
                with self.manifest_lock:
                    existing.update({"answer":parsed,"tools":execution.get("tools",[]),
                        "toolResults":execution.get("toolResults",[]),**placement})
                    self.store.update(identifier,result=manifest)
                return parsed,existing["context"]
            if state in {"TASK_STATE_FAILED","TASK_STATE_CANCELED","TASK_STATE_REJECTED"}:
                raise RuntimeError(role+" task ended "+state)
            completed=self.roles.wait(role,existing["task"],lambda:self.canceled(identifier))
            answer,execution=native_answer(completed)
            parsed=parse_json(answer)
            self.event(identifier,manifest,"child_recovered",role=role,key=key,task=completed["id"])
            placement=self.child_placement(role,completed["id"])
            with self.manifest_lock:
                existing.update({"answer":parsed,"tools":execution.get("tools",[]),
                    "toolResults":execution.get("toolResults",[]),**placement})
                self.store.update(identifier,result=manifest)
            return parsed,existing["context"]
        message_id=identifier+"-"+role+"-"+key
        task=self.roles.submit(role,message_id,prompt,context_id,**data)
        self.event(identifier,manifest,"child_submitted",role=role,key=key,task=task["id"],
            context=task["contextId"])
        with self.manifest_lock:
            manifest.setdefault("children",{})[key]={"role":role,"task":task["id"],
                "context":task["contextId"]}
            self.store.update(identifier,result=manifest)
        completed=self.roles.wait(role,task["id"],lambda:self.canceled(identifier))
        answer,execution=native_answer(completed)
        parsed=parse_json(answer)
        self.event(identifier,manifest,"child_completed",role=role,key=key,task=task["id"])
        placement=self.child_placement(role,task["id"])
        with self.manifest_lock:
            manifest["children"][key].update({"answer":parsed,"tools":execution.get("tools",[]),
                **placement})
            manifest["children"][key]["toolResults"]=execution.get("toolResults",[])
            self.store.update(identifier,result=manifest)
        return parsed,task["contextId"]

    def candidate_history(self,campaign,limit=24):
        history=[]
        for task in reversed(self.store.list()):
            prior=task.get("result") or {}
            if prior.get("campaign")==campaign:
                continue
            reviews=prior.get("planReviews") or []
            for index,candidate in enumerate(prior.get("candidates") or []):
                history.append({"campaign":prior.get("campaign"),"state":task.get("state"),
                    "title":candidate.get("title") or candidate.get("pattern"),
                    "disposition":candidate.get("disposition"),
                    "overlap":candidate.get("overlap"),
                    "reviewDecision":reviews[index].get("decision")
                        if index<len(reviews) and isinstance(reviews[index],dict) else None,
                    "pullRequests":self.produced_pr_urls(prior)})
                if len(history)>=limit:
                    return history
        return history

    @staticmethod
    def produced_pr_urls(manifest):
        evidence={"implementations":manifest.get("implementations",[]),
            "documentation":manifest.get("documentation")}
        for key,child in (manifest.get("children") or {}).items():
            if (child.get("role") in {"coding","documentation-steward"}
                    and key.startswith(("code","validation","create-pr","correction",
                        "finish-pr","documentation"))):
                evidence[key]=child.get("answer")
        return public_pr_urls(evidence)

    @staticmethod
    def rank_candidates(candidates):
        for candidate in candidates:
            values={}
            for field in ["evidenceStrength","generality","falsePositiveRisk","implementationFit"]:
                value=candidate.get(field,0)
                values[field]=value if isinstance(value,int) and not isinstance(value,bool) and 0<=value<=5 else 0
                candidate[field]=values[field]
            candidate["priorityScore"]=(3*values["evidenceStrength"]+2*values["generality"]+
                values["implementationFit"]-2*values["falsePositiveRisk"])
        return sorted(candidates,key=lambda value:value["priorityScore"],reverse=True)

    @staticmethod
    def candidate_execution(candidate):
        decision=str(candidate.get("decision","")).lower()
        disposition=str(candidate.get("disposition","")).lower()
        if decision not in {"promote","calibrate","watch","reject"}:
            if any(value in disposition for value in
                   ["reject","deduplicat","do not","watchlist","retain only"]):
                decision="reject"
            elif "calibrat" in disposition:
                decision="calibrate"
            else:
                decision="promote"
        track=str(candidate.get("implementationTrack") or "slopmop-analyzer").lower()
        candidate["decision"],candidate["implementationTrack"]=decision,track
        eligible=decision in {"promote","calibrate"} and track in {
            "slopmop-analyzer","slopmop-calibration"}
        return eligible

    def mine_evidence(self,identifier,manifest,request):
        self.stage(identifier,manifest,"evidence-mining")
        explicit=request["approvedPrUrls"]
        if request["autonomousDiscovery"]:
            prompt=("Perform a bounded autonomous cleanup-opportunity crawl over the last "+
                str(request["lookbackDays"])+" days. Inspect at most "+str(request["sourceLimit"])+
                " Azure DevOps PRs selected for substantive review discussion or engsys changes. Read their review "+
                "threads and changed files, then perform targeted code/history searches for recurring forms. Include "+
                "active and completed PRs, but never comment or write. Return coverage and at most 8 generalized "+
                "candidate signals. Explicit seed PRs, if any: "+json.dumps(explicit))
            key="evidence-crawl"
        else:
            prompt=("Mine only these explicitly approved PRs for generalized SlopMop patterns, include coverage "+
                "receipts, and return the required JSON: "+json.dumps(explicit))
            key="miner"
        mined=self.role_task(identifier,manifest,"pr-pattern-miner",key,prompt)[0]
        manifest["mined"]=mined
        return mined

    def planning(self,identifier,manifest,request,evidence=None):
        self.stage(identifier,manifest,"scouting")
        inventory=self.candidate_history(request["campaign"])
        scout_prompt=("Campaign goal:\n"+request["goal"]+
            "\nInspect the SlopMop implementation on users/nibeauli/slopmop-devel and return at most "+
            str(request["candidateLimit"])+" strongest non-duplicate candidates.\nEvidence-miner and triage signals:\n"+
            json.dumps(evidence or {},separators=(",",":"))[:16000]+
            "\nPrior campaign inventory and dispositions:\n"+
            json.dumps(inventory,separators=(",",":"))[:12000]+
            "\nReport what sources you actually checked. Verify signals before promotion, reject duplicates and "+
            "known-unsafe ideas, and include the four required 0-5 ranking dimensions.")
        scout,_=self.role_task(identifier,manifest,"opportunity-scout","scout",scout_prompt)
        candidates=scout.get("candidates")
        if not isinstance(candidates,list) or not candidates:
            raise RuntimeError("scout returned no candidates")
        ranked=self.rank_candidates(candidates)
        manifest["scoutedCandidates"]=ranked
        eligible=[candidate for candidate in ranked if self.candidate_execution(candidate)]
        candidates=eligible[:request["candidateLimit"]]
        manifest["rejectedCandidates"]=[candidate for candidate in ranked if candidate not in eligible]
        manifest["deferredCandidates"]=eligible[request["candidateLimit"]:]
        if not candidates:
            raise RuntimeError("scout returned no executable SlopMop candidates")
        manifest["candidates"]=candidates
        self.event(identifier,manifest,"candidates_ready",count=len(candidates))
        self.stage(identifier,manifest,"planning")
        def plan(index):
            prompt="Create an implementation-ready SlopMop plan for this candidate:\n"+json.dumps(candidates[index])
            return index,self.role_task(identifier,manifest,"planner","plan-"+str(index),prompt)[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(2,len(candidates))) as pool:
            plans=[None]*len(candidates)
            futures={pool.submit(plan,index):index for index in range(len(candidates))}
            for future in concurrent.futures.as_completed(futures):
                index=futures[future]
                try:
                    _,value=future.result()
                    plans[index]=value
                except Exception as error:
                    plans[index]={"error":str(error)}
                    self.event(identifier,manifest,"plan_failed",index=index,error=str(error))
        manifest["plans"]=plans
        self.stage(identifier,manifest,"plan-review")
        def review(index):
            prompt=("Review this SlopMop plan independently. Candidate:\n"+json.dumps(candidates[index])+
                "\nPlan:\n"+json.dumps(plans[index]))
            if plans[index].get("error"):
                return index,{"decision":"failed","blockingFindings":[plans[index]["error"]]}
            return index,self.role_task(identifier,manifest,"plan-reviewer","plan-review-"+str(index),prompt)[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(2,len(plans))) as pool:
            reviews=[None]*len(plans)
            futures={pool.submit(review,index):index for index in range(len(plans))}
            for future in concurrent.futures.as_completed(futures):
                index=futures[future]
                try:
                    _,value=future.result()
                    reviews[index]=value
                except Exception as error:
                    reviews[index]={"decision":"failed","blockingFindings":[str(error)]}
                    self.event(identifier,manifest,"plan_review_failed",index=index,error=str(error))
        for index in range(len(reviews)):
            planner_context=manifest["children"]["plan-"+str(index)]["context"]
            reviewer_context=manifest["children"]["plan-review-"+str(index)]["context"]
            for attempt in range(2):
                review_value=reviews[index]
                if review_value.get("decision")!="changes_requested":
                    break
                suffix="" if attempt==0 else "-"+str(attempt)
                revision_key="plan-revision-"+str(index)+suffix
                revision_prompt=("Revise the plan to address every blocking finding from this independent review. "+
                    "Preserve approved scope, map each blocking finding to an explicit plan change, and do not merely "+
                    "rephrase the previous design. Candidate:\n"+json.dumps(candidates[index])+
                    "\nCurrent plan:\n"+json.dumps(plans[index])+
                    "\nReview:\n"+json.dumps(review_value))
                plans[index],_=self.role_task(identifier,manifest,"planner",revision_key,
                    revision_prompt,context_id=planner_context)
                rereview_key="plan-rereview-"+str(index)+suffix
                rereview_prompt=("Re-review this revised SlopMop plan against every previous blocking finding. "+
                    "Approve only if each required change is explicit and testable. Candidate:\n"+
                    json.dumps(candidates[index])+"\nRevised plan:\n"+json.dumps(plans[index])+
                    "\nPrevious review:\n"+json.dumps(review_value))
                reviews[index],_=self.role_task(identifier,manifest,"plan-reviewer",
                    rereview_key,rereview_prompt,context_id=reviewer_context)
        manifest["planReviews"]=reviews
        approved=[{"candidate":candidates[index],"plan":plans[index],"review":reviews[index],"index":index}
            for index in range(len(plans)) if reviews[index].get("decision")=="approved"]
        manifest["approvedPlans"]=approved
        self.event(identifier,manifest,"plans_reviewed",approved=len(approved),total=len(plans))
        return approved

    def implement(self,identifier,manifest,approved,slot):
        index=approved["index"]
        campaign=manifest["campaign"]
        proposed=approved["plan"].get("proposedRule",{})
        rule_id=str(proposed.get("id","SlopMop"))
        rule_title=str(proposed.get("title","Implement approved analyzer rule"))
        branch=("users/nibeauli/slopmop-demo-"+campaign+"-"+str(slot))[:100].rstrip("-")
        context=identifier+"-implementation-"+str(slot)
        prompt=("Implement this approved SlopMop plan on base users/nibeauli/slopmop-devel.\n"+
            json.dumps(approved["plan"])+"\nPlan review:\n"+json.dumps(approved["review"])+
            "\nUse only the owned branch "+branch+". Run repo_validate, commit and push only required files, "+
            "then create a non-draft Azure DevOps PR targeting refs/heads/users/nibeauli/slopmop-devel. "+
            "If repo_validate reports ok false, stop without pushing or creating a PR. Return JSON with branch, commit, "+
            "pullRequestId, pullRequestUrl, files, and the exact parsed repo_validate result in validation.")
        validation_key="code-"+str(slot)
        try:
            coding,coding_context=self.role_task(identifier,manifest,"coding",validation_key,prompt,
                context_id=context,allowEdits=True,allowAuditUi=True,
                baseBranch="users/nibeauli/slopmop-devel",workBranch=branch)
        except RuntimeError:
            validation_key="code-retry-v1-"+str(slot)
            coding,coding_context=self.role_task(identifier,manifest,"coding",validation_key,
                prompt+"\nThis is a fresh-context retry after explicit pre-task rejection; do not duplicate remote artifacts.",
                allowEdits=True,allowAuditUi=True,
                baseBranch="users/nibeauli/slopmop-devel",workBranch=branch)
        validations=[item for item in manifest["children"][validation_key].get("toolResults",[])
            if item.get("operation")=="repo_validate"]
        validation=validations[-1] if validations else None
        for attempt in range(2):
            if validation_succeeded(validation):
                break
            diagnostic=str((validation or {}).get("diagnostic","validation receipt missing"))[-8000:]
            correction=("Validation failed before PR creation. Fix only these exact diagnostics, rerun repo_validate, "+
                "and proceed to commit, push, and create the requested PR only after validation reports ok true. "+
                "Return the complete implementation JSON receipt.\nDiagnostics:\n"+diagnostic)
            validation_key="validation-correction-"+str(slot)+"-"+str(attempt)
            coding,_=self.role_task(identifier,manifest,"coding",validation_key,correction,
                context_id=coding_context,allowEdits=True,allowAuditUi=True,
                baseBranch="users/nibeauli/slopmop-devel",workBranch=branch)
            validations=[item for item in manifest["children"][validation_key].get("toolResults",[])
                if item.get("operation")=="repo_validate"]
            validation=validations[-1] if validations else None
        if not validation_succeeded(validation):
            raise RuntimeError("coding validation did not succeed")
        pr_url=find_value(coding,{"pullRequestUrl","pull_request_url","prUrl"})
        if not pr_url:
            create_pr=("The implementation is validated, committed, and pushed on "+branch+
                ". Create its non-draft Azure DevOps PR now. Use gateway load_toolset with toolset_id ado and "+
                "toolset_args {\"organization\":\"msasg\"}. Then use gateway call_tool with the returned toolset ID, "+
                "tool_name exactly repo_pull_request_write, and arguments containing action create, repositoryId engsys, "+
                "project Engineering Fundamentals, sourceRefName refs/heads/"+branch+
                ", targetRefName refs/heads/users/nibeauli/slopmop-devel, title "+rule_id+": "+rule_title+
                ", a concise description, "+
                "and isDraft false. Do not prefix the nested tool name. Return JSON with branch, commit, "+
                "pullRequestId, and pullRequestUrl.")
            try:
                coding,_=self.role_task(identifier,manifest,"coding","create-pr-v3-"+str(slot),create_pr,
                    context_id=coding_context,allowEdits=True,allowAuditUi=True,
                    baseBranch="users/nibeauli/slopmop-devel",workBranch=branch)
            except RuntimeError:
                coding,_=self.role_task(identifier,manifest,"coding","create-pr-recovery-v1-"+str(slot),
                    create_pr+"\nThis is PR-only recovery from an already validated and pushed branch. Do not edit files.",
                    allowEdits=False,baseBranch="users/nibeauli/slopmop-devel")
            pr_url=find_value(coding,{"pullRequestUrl","pull_request_url","prUrl"})
        pr_url=validate_pr_url(pr_url)
        review_prompt=("Review this implementation against the approved plan. Plan:\n"+
            json.dumps(approved["plan"])+"\nCoding receipt:\n"+json.dumps(coding)+
            "\nInspect the explicit PR "+pr_url+" and return the required review JSON.")
        review,_=self.role_task(identifier,manifest,"implementation-reviewer","implementation-review-"+str(slot),
            review_prompt)
        for attempt in range(2):
            if review.get("decision")!="changes_requested":
                break
            correction=("Address only these implementation-review findings, rerun repo_validate, commit and push the "+
                "same branch, and return updated JSON receipts:\n"+json.dumps(review))
            correction_key="correction-"+str(slot) if attempt==0 else "correction-"+str(slot)+"-"+str(attempt)
            try:
                coding,_=self.role_task(identifier,manifest,"coding",correction_key,correction,
                    context_id=coding_context,allowEdits=True,allowAuditUi=True,
                    baseBranch="users/nibeauli/slopmop-devel",workBranch=branch)
            except RuntimeError:
                coding=None
            if coding is None or not manifest["children"].get(correction_key,{}).get("toolResults"):
                recovery_key="correction-recovery-v1-"+str(slot)+"-"+str(attempt)
                coding,_=self.role_task(identifier,manifest,"coding",recovery_key,
                    correction+"\nThis is recovery from the already pushed owned branch. It is already checked out. "+
                    "Use repo_delete for files the reviewer requires removed; do not create a replacement branch or PR.",
                    allowEdits=True,allowAuditUi=True,baseBranch=branch,workBranch=branch)
                correction_key=recovery_key
            validations=[item for item in manifest["children"][correction_key].get("toolResults",[])
                if item.get("operation")=="repo_validate"]
            validation=validations[-1] if validations else None
            if not validation_succeeded(validation):
                raise RuntimeError("corrected coding validation did not succeed")
            review_prompt=("Re-review the corrected PR against the approved plan. Plan:\n"+
                json.dumps(approved["plan"])+"\nCorrection receipt:\n"+json.dumps(coding)+"\nPR: "+pr_url)
            review_key=("implementation-rereview-"+str(slot) if attempt==0
                else "implementation-rereview-"+str(slot)+"-"+str(attempt))
            review,_=self.role_task(identifier,manifest,"implementation-reviewer",
                review_key,review_prompt)
        if review.get("decision")!="approved":
            raise RuntimeError("implementation review did not approve")
        finish_prompt=("Use the finish-pr skill and approved watch MCP to drive this exact PR to completion: "+pr_url+
            ". Always pass the explicit PR URL. Address only actionable feedback within the approved plan, "+
            "rerun validation after code changes, commit and push the same branch, and continue until the PR is "+
            "completed or an unresolved handoff is proven. Return JSON with status, pullRequestUrl, commit, and evidence.")
        try:
            finished,_=self.role_task(identifier,manifest,"coding","finish-pr-"+str(slot),finish_prompt,
                context_id=coding_context,allowEdits=True,allowAuditUi=True,
                baseBranch="users/nibeauli/slopmop-devel",workBranch=branch)
        except RuntimeError:
            finished={}
        status=find_value(finished,{"status","pullRequestStatus","pull_request_status"})
        if str(status).lower() not in {"completed","merged","succeeded"}:
            finished,_=self.role_task(identifier,manifest,"coding","finish-pr-recovery-v2-"+str(slot),
                finish_prompt+"\nThis is PR-only recovery for an already reviewed branch. Do not edit files.",
                allowEdits=False,allowAuditUi=True,baseBranch="users/nibeauli/slopmop-devel")
            status=find_value(finished,{"status","pullRequestStatus","pull_request_status"})
        if str(status).lower() not in {"completed","merged","succeeded"}:
            raise RuntimeError("finish-pr did not prove completion")
        return {"plan":approved["plan"],"coding":coding,"review":review,"finish":finished,
            "branch":branch,"pullRequestUrl":pr_url}

    def document(self,identifier,manifest,implementations):
        campaign=manifest["campaign"]
        branch=("users/nibeauli/slopmop-demo-"+campaign+"-docs")[:100].rstrip("-")
        context=identifier+"-documentation"
        prompt=("Update SlopMop consumer documentation for these completed implementation PRs:\n"+
            json.dumps([{"plan":item["plan"],"pullRequestUrl":item["pullRequestUrl"]}
                for item in implementations])+
            "\nRead the completed state from users/nibeauli/slopmop-devel. Change only required README, rule-family "+
            "documentation, configuration examples, help links, or release notes. Use branch "+branch+
            ", validate the documentation links and diff, commit, push, and create a non-draft PR targeting "+
            "refs/heads/users/nibeauli/slopmop-devel. Return JSON with branch, commit, pullRequestId, "+
            "pullRequestUrl, files, and validation. If the implementation is test-only or consumer documentation "+
            "already matches, do not create an empty or explanatory PR; return JSON with noChangeRequired true, "+
            "rationale, and files [].")
        documented,docs_context=self.role_task(identifier,manifest,"documentation-steward","documentation",
            prompt,context_id=context,allowEdits=True,allowAuditUi=True,
            baseBranch="users/nibeauli/slopmop-devel",workBranch=branch)
        if documented.get("noChangeRequired") is True:
            return {"receipt":documented,"finish":{"status":"not-required"},
                "branch":branch,"pullRequestUrl":None}
        pr_url=find_value(documented,{"pullRequestUrl","pull_request_url","prUrl"})
        pr_url=validate_pr_url(pr_url)
        finish_prompt=("Use the finish-pr skill and approved watch MCP to drive this exact documentation PR to "+
            "completion: "+pr_url+". Always pass the explicit PR URL. Address only documentation feedback, "+
            "commit and push the same branch if needed, and return JSON with status, pullRequestUrl, commit, and evidence.")
        finished,_=self.role_task(identifier,manifest,"documentation-steward","documentation-finish",
            finish_prompt,context_id=docs_context,allowEdits=True,
            allowAuditUi=True,
            baseBranch="users/nibeauli/slopmop-devel",workBranch=branch)
        status=find_value(finished,{"status","pullRequestStatus","pull_request_status"})
        if str(status).lower() not in {"completed","merged","succeeded"}:
            raise RuntimeError("documentation finish-pr did not prove completion")
        return {"receipt":documented,"finish":finished,"branch":branch,"pullRequestUrl":pr_url}

    def process(self,identifier):
        task=self.store.get(identifier)
        if not task or task["state"] in TERMINAL:
            return
        request=task["request"]
        manifest=task["result"] or {"campaign":request["campaign"],"goal":request["goal"],
            "state":"starting","children":{},"events":[]}
        try:
            self.store.update(identifier,state="active",result=manifest,diagnostic=None)
            self.event(identifier,manifest,"campaign_started",campaign=request["campaign"])
            evidence={}
            if request["autonomousDiscovery"] or request["approvedPrUrls"]:
                evidence["mined"]=self.mine_evidence(identifier,manifest,request)
            if request["triageWorkItemIds"]:
                self.stage(identifier,manifest,"ado-triage")
                prompt=("Triage only these SlopMop work items and return the required JSON: "+
                    json.dumps(request["triageWorkItemIds"]))
                manifest["triage"]=self.role_task(identifier,manifest,"ado-triage","triage",prompt)[0]
                evidence["triage"]=manifest["triage"]
            approved=self.planning(identifier,manifest,request,evidence)
            if request["execute"]:
                self.stage(identifier,manifest,"implementation")
                selected=approved[:request["implementationLimit"]]
                if not selected:
                    raise RuntimeError("no approved plan available for implementation")
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected)) as pool:
                    implementations=list(pool.map(
                        lambda pair:self.implement(identifier,manifest,pair[1],pair[0]),
                        enumerate(selected)))
                manifest["implementations"]=implementations
                self.stage(identifier,manifest,"documentation")
                manifest["documentation"]=self.document(identifier,manifest,implementations)
            manifest["state"]="completed"
            self.event(identifier,manifest,"campaign_completed",
                implementations=len(manifest.get("implementations",[])))
            self.store.update(identifier,state="completed",result=manifest,diagnostic=None)
        except Exception as error:
            manifest["state"]="failed"
            manifest["error"]=str(error)
            self.event(identifier,manifest,"campaign_failed",error=str(error))
            self.store.update(identifier,state="failed",result=manifest,diagnostic=str(error))

    def schedule(self):
        while not self.stop.is_set():
            with self.lock:
                for identifier,future in list(self.running.items()):
                    if future.done():
                        self.running.pop(identifier)
                if not self.running:
                    active=self.store.active()
                    task=active[0] if active else self.store.claim()
                    if task:
                        self.running[task["id"]]=self.executor.submit(self.process,task["id"])
            self.stop.wait(1)

    def cancel(self,identifier,owner):
        return public_task(self.store.cancel(identifier,owner))

    def public_campaigns(self):
        campaigns=[]
        node_names={"cpc-nibea-8t76e":"primary-devbox",
            "cpc-nibea-2l35i":"xl-devbox","cpc-nibea-4memo":"devbox-2"}
        for task in self.store.list():
            manifest=task["result"] or {}
            stages=[]
            for event in manifest.get("events",[]):
                stage=event.get("stage") if event.get("event")=="stage_changed" else None
                if stage and stage not in stages:
                    stages.append(stage)
            implementations=len(manifest.get("implementations",[]))
            if task["state"]=="completed" and implementations:
                outcome="Delivered implementation and documentation"
            elif task["state"]=="completed":
                outcome="Planning run completed"
            elif task["state"]=="failed":
                outcome="Stopped and retained for evidence"
            else:
                outcome="In progress"
            children={key:{"role":value.get("role"),"task":value.get("task"),
                "context":value.get("context"),"session":value.get("session"),
                "node":value.get("node")} for key,value in manifest.get("children",{}).items()}
            sequence=[]
            represented=set()
            for event in manifest.get("events",[]):
                if event.get("event")=="child_submitted" and event.get("role"):
                    represented.add(event.get("key"))
                    sequence.append({"source":"swarm-manager","target":event["role"],
                        "label":"submit "+str(event.get("key","task")),
                        "time":event.get("time"),"key":event.get("key")})
                elif event.get("event") in {"child_completed","child_recovered"} and event.get("role"):
                    represented.add(event.get("key"))
                    sequence.append({"source":event["role"],"target":"swarm-manager",
                        "label":("recover " if event["event"]=="child_recovered" else "complete ")+
                            str(event.get("key","task")),
                        "time":event.get("time"),"key":event.get("key")})
            for key,child in children.items():
                if key not in represented:
                    sequence.append({"source":child.get("role"),"target":"swarm-manager",
                        "label":"adopt retained "+key,"time":None,"key":key})
            grouped={("cpc-nibea-8t76e","swarm-manager","campaign-manager"):{
                "role":"swarm-manager","name":AGENT_NAMES["swarm-manager"],
                "context":"campaign manager","tasks":["campaign"],"observed":True}}
            for key,child in children.items():
                group=(child.get("node") or "unknown",child.get("role"),child.get("context") or key)
                instance=grouped.setdefault(group,{"role":child.get("role"),
                    "name":AGENT_NAMES.get(child.get("role"),child.get("role")),
                    "context":child.get("context") or "unknown context","tasks":[],
                    "observed":bool(child.get("node"))})
                instance["tasks"].append(key)
            placements=[]
            for node_id in ["cpc-nibea-8t76e","cpc-nibea-2l35i","cpc-nibea-4memo","unknown"]:
                instances=[value for (node,_,_),value in grouped.items() if node==node_id]
                if instances:
                    placements.append({"node":node_id,
                        "name":node_names.get(node_id,"Historical node unavailable"),
                        "instances":sorted(instances,key=lambda value:(value["role"],value["context"]))})
            public={"campaign":manifest.get("campaign"),"goal":manifest.get("goal"),
                "state":manifest.get("state"),"events":manifest.get("events",[]),
                "children":children,"sequence":sequence,"placements":placements,
                "candidateCount":len(manifest.get("candidates",[])),
                "approvedCount":len(manifest.get("approvedPlans",[])),
                "implementationCount":implementations,
                "outcome":outcome,"stages":stages,
                "pullRequests":self.produced_pr_urls(manifest),
                "error":str(manifest.get("error",""))[:500]}
            campaigns.append({"id":task["id"],"state":task["state"],
                "contextId":task["context_id"],"manifest":public})
        return campaigns

    def public_topology(self):
        manager={
            "id":"swarm-manager","name":AGENT_NAMES["swarm-manager"],
            "description":"Coordinate deterministic campaign stages and retain A2A child lineage.",
            "gateway":"primary-devbox · Ubuntu-26.04 · port 18451",
            "runtime":"primary-devbox · systemd service",
            "placement":"fixed control-plane process",
        }
        agents=[manager]
        for role_id,role in ROLES.items():
            agents.append({
                "id":role_id,"name":AGENT_NAMES[role_id],
                "description":role["description"],
                "gateway":"primary-devbox · Ubuntu-26.04 · port "+str(role["port"]),
                "runtime":"MicroK8s · factory-executions · "+role["backendAgent"],
                "placement":"dynamic actor on primary-devbox or xl-devbox",
            })
        return {
            "agents":agents,
            "edges":[{"source":source,"target":target,"label":label}
                for source,target,label in FLOW_EDGES],
            "locations":[
                {"name":"primary-devbox","purpose":"Swarm Manager and all A2A gateways",
                 "address":"10.82.0.2"},
                {"name":"primary-devbox + xl-devbox","purpose":"Ready MicroK8s execution pool",
                 "address":"10.82.0.2 / 10.82.0.3"},
            ],
            "placementNote":"Execution location is the eligible pool; per-session node placement is not yet projected.",
            "deployment":self.deployment_snapshot(),
        }

    def deployment_snapshot(self):
        if not self.config.get("deploymentProjection",False):
            return {"nodes":[],"parked":[],"capturedAt":None}
        with self.deployment_lock:
            now=time.time()
            if self.deployment_cache and now-self.deployment_cache_at<15:
                return self.deployment_cache
            try:
                kube=["/snap/bin/microk8s","kubectl"]
                nodes=json.loads(subprocess.check_output(
                    [*kube,"get","nodes","-o","json"],text=True,timeout=15))
                pods=json.loads(subprocess.check_output(
                    [*kube,"get","pods","-n","factory-executions","-o","json"],
                    text=True,timeout=15))
                env=dict(os.environ,KUBECONFIG="/var/snap/microk8s/current/credentials/client.config")
                actors=json.loads(subprocess.check_output([
                    "/home/nibeauli/kagent-lab/artifacts/kubectl-ate","--preview",
                    "get","actors","-a","factory-executions","-o","json"],
                    env=env,text=True,stderr=subprocess.DEVNULL,timeout=20)).get("actors",[])
                with self.store.connection() as db:
                    rows=db.execute(
                        """SELECT DISTINCT ON (session) session,gateway
                           FROM factory.gateway_tasks
                           WHERE session IS NOT NULL AND gateway<>%s
                           ORDER BY session,updated DESC""",
                        ("swarm-manager",)).fetchall()
                session_roles={str(row["session"]):row["gateway"] for row in rows}
                names={"10.82.0.2":"primary-devbox","10.82.0.3":"xl-devbox",
                    "10.82.0.4":"devbox-2"}
                values=[]
                by_kube={}
                for item in nodes["items"]:
                    addresses={address["type"]:address["address"]
                        for address in item.get("status",{}).get("addresses",[])}
                    ready=next((condition["status"] for condition in
                        item.get("status",{}).get("conditions",[])
                        if condition["type"]=="Ready"),"Unknown")
                    node={"id":item["metadata"]["name"],
                        "name":names.get(addresses.get("InternalIP"),item["metadata"]["name"]),
                        "address":addresses.get("InternalIP",""),
                        "status":"Ready" if ready=="True" else "NotReady",
                        "instances":[]}
                    values.append(node)
                    by_kube[node["id"]]=node
                primary=next((node for node in values if node["address"]=="10.82.0.2"),None)
                if primary:
                    primary["instances"].append({"name":"Swarm Manager","kind":"WSL service",
                        "role":"swarm-manager","state":"running","detail":"port 18451"})
                    for role_id,role in ROLES.items():
                        primary["instances"].append({"name":AGENT_NAMES[role_id]+" Gateway",
                            "kind":"WSL service","role":role_id,"state":"running",
                            "detail":"port "+str(role["port"])})
                for pod in pods["items"]:
                    node=by_kube.get(pod.get("spec",{}).get("nodeName"))
                    if node:
                        node["instances"].append({"name":pod["metadata"]["name"],
                            "kind":"Kubernetes pod","role":None,
                            "state":pod.get("status",{}).get("phase","Unknown"),
                            "detail":"factory-executions"})
                parked={}
                for actor in actors:
                    actor_name=actor["metadata"]["name"]
                    session=actor_name.removeprefix("session-")
                    role=session_roles.get(session,"unknown")
                    state=actor.get("status",{}).get("state","UNKNOWN").removeprefix("ACTOR_STATE_").lower()
                    assigned=actor.get("status",{}).get("assignedNode")
                    node=by_kube.get(assigned)
                    if node:
                        node["instances"].append({"name":AGENT_NAMES.get(role,role)+" actor",
                            "kind":"Actor session","role":role,"state":state,
                            "detail":session})
                    else:
                        key=(role,state)
                        parked[key]=parked.get(key,0)+1
                for node in values:
                    node["instances"].sort(key=lambda value:(value["kind"],value["name"],value["detail"]))
                projection={"nodes":sorted(values,key=lambda value:value["address"]),
                    "parked":[{"role":role,"name":AGENT_NAMES.get(role,role),
                        "state":state,"count":count}
                        for (role,state),count in sorted(parked.items())],
                    "capturedAt":now}
                self.deployment_cache,self.deployment_cache_at=projection,now
                return projection
            except Exception as error:
                if self.deployment_cache:
                    return {**self.deployment_cache,"warning":"Using stale placement: "+str(error)}
                return {"nodes":[],"parked":[],"capturedAt":now,
                    "warning":"Placement unavailable: "+str(error)}

    def public_state(self):
        return {"topology":self.public_topology(),"campaigns":self.public_campaigns(),
            "telemetry":self.telemetry.status()}

    def handle_get(self,handler):
        path=urllib.parse.urlparse(handler.path)
        if path.path=="/api/campaigns":
            handler.reply(200,self.public_state())
            return True
        if path.path=="/events":
            handler.send_response(200)
            handler.send_header("Content-Type","text/event-stream")
            handler.send_header("Cache-Control","no-store")
            handler.send_header("Connection","keep-alive")
            handler.end_headers()
            last=None
            try:
                while not self.stop.is_set():
                    payload=json.dumps(self.public_state(),separators=(",",":"))
                    if payload!=last:
                        handler.wfile.write(("data: "+payload+"\n\n").encode())
                        handler.wfile.flush()
                        last=payload
                    self.stop.wait(2)
            except (BrokenPipeError,ConnectionResetError):
                pass
            return True
        if path.path=="/dashboard":
            body=DASHBOARD.encode()
            handler.send_response(200)
            handler.send_header("Content-Type","text/html; charset=utf-8")
            handler.send_header("Content-Length",str(len(body)))
            handler.send_header("Cache-Control","no-store")
            handler.end_headers()
            handler.wfile.write(body)
            return True
        return False

    def close(self):
        self.stop.set()
        self.executor.shutdown(wait=True,cancel_futures=False)
        self.store.close()


class DashboardServer(http.server.ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,manager):
        super().__init__(address,DashboardHandler)
        self.gateway=manager


class DashboardHandler(Handler):
    def do_GET(self):
        path=urllib.parse.urlparse(self.path).path
        if path=="/readyz":
            return self.reply(200,{"ready":True,"gateway":"swarm-dashboard"})
        if path in {"/dashboard","/api/campaigns","/events"}:
            return self.server.gateway.handle_get(self)
        self.reply(404,{"error":"not_found"})

    def do_POST(self):
        self.reply(404,{"error":"not_found"})


DASHBOARD="""<!doctype html>
<html><head><meta charset="utf-8"><title>SlopMop Agent Swarm</title>
<style>
:root{color-scheme:dark;--bg:#08101f;--panel:#111b31;--line:#31446e;--muted:#9cacca;--blue:#58a6ff;--green:#3fb950;--red:#ff7b72}
*{box-sizing:border-box}body{font-family:Segoe UI,sans-serif;background:var(--bg);color:#e8ecff;margin:0}
header{padding:18px 24px;background:#101a30;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:5}
header small{color:var(--muted)}h1,h2,h3,p{margin-top:0}
.tabs{display:flex;gap:6px;padding:12px 24px;background:#0d1629;border-bottom:1px solid #263755;position:sticky;top:74px;z-index:4}
.tab{border:1px solid var(--line);background:#152441;color:#dce6ff;border-radius:9px;padding:9px 16px;cursor:pointer;font-weight:600}
.tab.active{background:#1f6feb;border-color:#79c0ff}.view{display:none;padding:20px 24px 24px}.view.active{display:block}
.run-picker{display:flex;align-items:center;gap:10px;margin-bottom:14px}.run-picker select{min-width:320px;background:#152441;color:#e8ecff;border:1px solid var(--line);border-radius:8px;padding:8px}
.note{color:var(--muted);font-size:12px;margin:10px 0}
.node-stacks{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:14px}
.node-stack{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}.node-stack.not-ready{border-color:var(--red)}
.node-head{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #263755;padding-bottom:10px}.node-head h3{margin-bottom:3px}
.node-status{font-weight:700;color:var(--green)}.node-status.not-ready{color:var(--red)}
.instance{background:#152441;border-left:4px solid #64799e;border-radius:8px;padding:9px 10px;margin-top:9px}
.instance.actor-session{border-left-color:var(--blue)}.instance.wsl-service{border-left-color:#d2a8ff}.instance.kubernetes-pod{border-left-color:var(--green)}
.instance b{display:block}.instance-meta{color:var(--muted);font-size:11px;margin-top:3px}.parked-count{font-size:20px;font-weight:700;color:#d2a8ff}
.sequence-shell{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px}
#sequence{display:block}.participant{fill:#152441;stroke:var(--line);stroke-width:2}.participant.done{stroke:var(--green)}.participant.active{stroke:var(--blue);stroke-width:3}
.lifeline{stroke:#48618f;stroke-width:1.5;stroke-dasharray:6 6}.seq-line{stroke:#64799e;stroke-width:2}.seq-line.done{stroke:var(--green)}.seq-line.active{stroke:var(--blue);stroke-width:3}
.seq-name,.seq-label{fill:#e8ecff;text-anchor:middle}.seq-name{font-weight:600;font-size:12px}.seq-id{fill:var(--muted);text-anchor:middle;font:10px Consolas,monospace}
.seq-label{font-size:11px;paint-order:stroke;stroke:var(--bg);stroke-width:6;stroke-linejoin:round}
#campaigns{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
.stage{color:#7ee787}.failed{color:var(--red)}.event{font-size:12px;color:#aab4d6;margin:4px 0}
.metrics{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.metric,.role{background:#26345b;border-radius:10px;padding:4px 8px;font-size:12px}
.roles{display:flex;gap:6px;flex-wrap:wrap}.outcome{font-size:17px;font-weight:600;color:#dce6ff;margin:8px 0}
.phases{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0}.phase{border:1px solid var(--line);border-radius:12px;padding:4px 8px;font-size:12px;color:var(--muted)}
.phase.done{border-color:var(--green);color:#7ee787}.pr-link{display:inline-block;color:#79c0ff;margin:4px 10px 4px 0}
details{margin-top:12px;border-top:1px solid #263755;padding-top:10px}summary{cursor:pointer;color:#aab4d6}.error-text{color:#ffb3ad;font-size:12px}
</style></head><body>
<header><div><b>SlopMop named-agent swarm</b><br><small>Live A2A handoffs and deployment locations</small></div><span id="clock"></span></header>
<nav class="tabs" aria-label="Dashboard views">
  <button class="tab active" data-tab="sequence">Campaign View</button>
  <button class="tab" data-tab="agents">Current Cluster</button>
  <button class="tab" data-tab="campaigns">Swarm Runs</button>
</nav>
<section id="view-sequence" class="view active"><h2>Campaign sequence and placement</h2><p class="note">Choose one retained campaign to see its actual manager-to-agent A2A calls, completions, and the node observed for each actor context.</p><label class="run-picker"><b>Campaign</b><select id="campaign-select"></select></label><div id="campaign-summary" class="outcome"></div><div class="sequence-shell"><svg id="sequence" role="img" aria-label="Per-campaign agent handoff sequence diagram"></svg></div><h2>Where these agent instances ran</h2><div id="campaign-stacks" class="node-stacks"></div></section>
<section id="view-agents" class="view"><h2>Instances by node</h2><p class="note">Fixed WSL services, Kubernetes control pods, and currently assigned actor sessions are stacked under their actual node. Suspended actors have no node assignment and appear in the parked stack.</p><div id="node-stacks" class="node-stacks"></div><p id="placement-note" class="note"></p></section>
<section id="view-campaigns" class="view"><h2>Swarm runs</h2><p class="note">A campaign is one parent swarm run: one goal and every discovery, planning, review, implementation, PR, documentation, triage, and mining task created to pursue it.</p><main id="campaigns"></main></section>
<script>
const escapeHtml=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const agentName=(topology,id)=>escapeHtml((topology.agents.find(a=>a.id===id)||{}).name||id);
let latestData=null,selectedCampaign=null;
function selectTab(name){document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===name));document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id==='view-'+name))}
document.querySelectorAll('.tab').forEach(x=>x.addEventListener('click',()=>selectTab(x.dataset.tab)));
document.getElementById('campaign-select').addEventListener('change',event=>{selectedCampaign=event.target.value;renderCampaign(latestData)});
function activity(campaigns){
  const seen=new Set(['swarm-manager']),active=new Set(),open=new Map();
  for(const c of campaigns){for(const x of Object.values(c.manifest?.children||{}))seen.add(x.role);
    if(c.state!=='active')continue;active.add('swarm-manager');
    for(const e of c.manifest?.events||[]){if(e.event==='child_submitted')open.set(e.key,e.role);if(e.event==='child_completed')open.delete(e.key)}
  }for(const role of open.values())active.add(role);return {seen,active}
}
function renderSequence(topology,state,edges,participantIds){
  const agents=topology.agents.filter(a=>participantIds.has(a.id)),lane=190,left=18,top=18,messageTop=105,row=48,width=Math.max(900,left*2+agents.length*lane),height=messageTop+edges.length*row+35;
  const positions=new Map(agents.map((a,i)=>[a.id,left+i*lane+lane/2]));
  const marker=(id,color)=>`<marker id="${id}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="${color}"/></marker>`;
  let svg=`<defs>${marker('arrow','#64799e')}${marker('arrow-done','#3fb950')}${marker('arrow-active','#58a6ff')}</defs>`;
  for(const a of agents){const x=positions.get(a.id),kind=state.active.has(a.id)?'active':state.seen.has(a.id)?'done':'';
    svg+=`<rect class="participant ${kind}" x="${x-78}" y="${top}" width="156" height="52" rx="9"/><text class="seq-name" x="${x}" y="${top+22}">${escapeHtml(a.name)}</text><text class="seq-id" x="${x}" y="${top+40}">${escapeHtml(a.id)}</text><line class="lifeline" x1="${x}" y1="${top+52}" x2="${x}" y2="${height-12}"/>`}
  edges.forEach((e,i)=>{const x1=positions.get(e.source),x2=positions.get(e.target),y=messageTop+i*row,kind=state.active.has(e.target)?'active':state.seen.has(e.source)&&state.seen.has(e.target)?'done':'',markerId=kind==='active'?'arrow-active':kind==='done'?'arrow-done':'arrow';
    svg+=`<line class="seq-line ${kind}" x1="${x1}" y1="${y}" x2="${x2+(x2>x1?-9:9)}" y2="${y}" marker-end="url(#${markerId})"/><text class="seq-label" x="${(x1+x2)/2}" y="${y-7}">${escapeHtml(e.label)}</text>`});
  const element=document.getElementById('sequence');element.setAttribute('viewBox',`0 0 ${width} ${height}`);element.setAttribute('width',width);element.setAttribute('height',height);element.innerHTML=svg;
}
function campaignPlacement(campaign){
  return (campaign?.manifest?.placements||[]).map(node=>`<article class="node-stack ${node.node==='unknown'?'not-ready':''}"><div class="node-head"><div><h3>${escapeHtml(node.name)}</h3><span class="role-id">${escapeHtml(node.node)}</span></div></div>${node.instances.map(x=>`<div class="instance actor-session"><b>${escapeHtml(x.name)}</b><div class="instance-meta">${escapeHtml(x.role)} · ${x.observed?'observed':'historical placement unavailable'}<br>${escapeHtml(x.context)}<br>${escapeHtml(x.tasks.join(', '))}</div></div>`).join('')}</article>`).join('');
}
function renderCampaign(data){
  if(!data)return;const campaigns=data.campaigns||[],picker=document.getElementById('campaign-select');
  if(!selectedCampaign||!campaigns.some(c=>c.manifest?.campaign===selectedCampaign))selectedCampaign=(campaigns.at(-1)?.manifest?.campaign)||'';
  picker.innerHTML=campaigns.map(c=>`<option value="${escapeHtml(c.manifest?.campaign||'')}" ${c.manifest?.campaign===selectedCampaign?'selected':''}>${escapeHtml(c.manifest?.campaign||c.id)} · ${escapeHtml(c.manifest?.outcome||c.state)}</option>`).join('');
  const campaign=campaigns.find(c=>c.manifest?.campaign===selectedCampaign),topology=data.topology||{agents:[]};
  if(!campaign){document.getElementById('campaign-summary').textContent='No retained campaigns.';return}
  const edges=campaign.manifest.sequence||[],participants=new Set(['swarm-manager']);for(const edge of edges){participants.add(edge.source);participants.add(edge.target)}
  renderSequence(topology,activity([campaign]),edges,participants);
  document.getElementById('campaign-summary').textContent=campaign.manifest.outcome||campaign.state;
  document.getElementById('campaign-stacks').innerHTML=campaignPlacement(campaign)||'<p class="note">No placement evidence retained.</p>';
}
function render(data){
  latestData=data;
  document.getElementById('clock').textContent=new Date().toLocaleTimeString();
  const topology=data.topology||{agents:[],edges:[],locations:[]},state=activity(data.campaigns||[]);
  renderCampaign(data);
  const deployment=topology.deployment||{nodes:[],parked:[]};
  const stacks=deployment.nodes.map(node=>`<article class="node-stack ${node.status==='Ready'?'':'not-ready'}"><div class="node-head"><div><h3>${escapeHtml(node.name)}</h3><span class="role-id">${escapeHtml(node.id)} · ${escapeHtml(node.address)}</span></div><span class="node-status ${node.status==='Ready'?'':'not-ready'}">${escapeHtml(node.status)}</span></div>
    ${node.instances.length?node.instances.map(x=>`<div class="instance ${escapeHtml(x.kind.toLowerCase().replaceAll(' ','-'))}"><b>${escapeHtml(x.name)}</b><div class="instance-meta">${escapeHtml(x.kind)}${x.role?' · '+escapeHtml(x.role):''} · ${escapeHtml(x.state)}<br>${escapeHtml(x.detail)}</div></div>`).join(''):'<p class="note">No projected instances.</p>'}</article>`).join('');
  const parked=deployment.parked.length?`<article class="node-stack"><div class="node-head"><div><h3>Parked actor sessions</h3><span class="role-id">persistent state · no assigned node</span></div></div>${deployment.parked.map(x=>`<div class="instance actor-session"><b>${escapeHtml(x.name)}</b><div class="parked-count">${x.count}</div><div class="instance-meta">${escapeHtml(x.role)} · ${escapeHtml(x.state)}</div></div>`).join('')}</article>`:'';
  document.getElementById('node-stacks').innerHTML=stacks+parked;
  document.getElementById('placement-note').textContent=(deployment.warning||'')+(deployment.capturedAt?' Placement captured '+new Date(deployment.capturedAt*1000).toLocaleTimeString()+'.':'');
  document.getElementById('campaigns').innerHTML=(data.campaigns||[]).map(c=>{const m=c.manifest||{};
    const roles=Object.values(m.children||{}).map(x=>`<span class="role">${agentName(topology,x.role)} · ${escapeHtml(x.task)}</span>`).join('');
    const events=(m.events||[]).slice(-12).reverse().map(e=>`<div class="event">${new Date(e.time*1000).toLocaleTimeString()} · ${escapeHtml(e.event)} ${escapeHtml(e.role||'')}</div>`).join('');
    const phases=['scouting','planning','plan-review','implementation','documentation'].map(x=>`<span class="phase ${m.stages?.includes(x)?'done':''}">${escapeHtml(x.replaceAll('-',' '))}</span>`).join('');
    const prs=(m.pullRequests||[]).map(url=>`<a class="pr-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">PR ${escapeHtml(url.split('/').pop())}</a>`).join('');
    return `<section class="card"><h2>${escapeHtml(m.campaign||c.id)}</h2><div class="${c.state==='failed'?'failed':'stage'}">${escapeHtml(c.state)}</div><div class="outcome">${escapeHtml(m.outcome||'')}</div><p>${escapeHtml(m.goal||'')}</p>
      <div class="metrics"><span class="metric">${m.candidateCount||0} candidates</span><span class="metric">${m.approvedCount||0} approved</span><span class="metric">${m.implementationCount||0} implemented</span></div>
      <div class="phases">${phases}</div>${prs||'<span class="note">No PR recorded for this run.</span>'}${m.error?`<p class="error-text">${escapeHtml(m.error)}</p>`:''}
      <details><summary>Technical lineage (${Object.keys(m.children||{}).length} child tasks)</summary><div class="roles">${roles}</div><h3>Recent events</h3>${events}</details></section>`}).join('')
}
const events=new EventSource('/events');events.onmessage=e=>render(JSON.parse(e.data));
fetch('/api/campaigns').then(r=>r.json()).then(render);
</script></body></html>"""


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    store=Store(config["postgres"],1,20,"swarm-manager","campaign")
    store.acquire_scheduler()
    manager=SwarmManager(store,config)
    server=Server((config.get("listenAddress","10.82.0.2"),config["listenPort"]),
        manager,config["clients"])
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version=ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(config["certificate"],config["key"])
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    dashboard=DashboardServer((config.get("hostDashboardAddress","127.0.0.1"),
        config.get("hostDashboardPort",18452)),manager)
    dashboard_thread=threading.Thread(target=dashboard.serve_forever,daemon=True)
    dashboard_thread.start()
    scheduler=threading.Thread(target=manager.schedule,daemon=True)
    scheduler.start()
    try:
        server.serve_forever()
    finally:
        dashboard.shutdown()
        dashboard.server_close()
        server.server_close()
        manager.close()


if __name__=="__main__":
    main()

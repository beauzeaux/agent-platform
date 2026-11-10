"""Minimal OTLP/HTTP JSON exporter for safe swarm lifecycle metadata."""
import hashlib
import json
import threading
import time
import urllib.request


def hex_id(*values,length):
    digest=hashlib.sha256("\0".join(str(value) for value in values).encode()).hexdigest()
    return digest[:length]


def attribute(key,value):
    if isinstance(value,bool):
        body={"boolValue":value}
    elif isinstance(value,int):
        body={"intValue":str(value)}
    elif isinstance(value,float):
        body={"doubleValue":value}
    else:
        body={"stringValue":str(value)[:1000]}
    return {"key":key,"value":body}


class OtelExporter:
    def __init__(self,endpoint=None,service_name="slopmop-swarm-manager"):
        self.endpoint=endpoint
        self.service_name=service_name
        self.lock=threading.Lock()
        self.emitted=0
        self.last_error=None
        self.last_success=None

    def status(self):
        with self.lock:
            return {"enabled":bool(self.endpoint),"emitted":self.emitted,
                "lastSuccess":self.last_success,"lastError":self.last_error}

    def emit(self,campaign_task,event,event_time,details,first_time=None,terminal=False):
        if not self.endpoint:
            return
        trace_id=hex_id(campaign_task,length=32)
        root_span=hex_id(campaign_task,"root",length=16)
        index=details.get("eventIndex",0)
        spans=[self._span(trace_id,hex_id(campaign_task,event,index,event_time,length=16),
            root_span,"swarm."+event,event_time,event_time+.001,
            {"campaign.task.id":campaign_task,"swarm.event":event,**details},
            failed=event=="campaign_failed")]
        if terminal:
            spans.append(self._span(trace_id,root_span,None,"swarm.campaign",
                first_time or event_time,event_time,
                {"campaign.task.id":campaign_task,
                 "campaign.state":"failed" if event=="campaign_failed" else "completed"},
                failed=event=="campaign_failed"))
        payload={"resourceSpans":[{"resource":{"attributes":[
            attribute("service.name",self.service_name),
            attribute("deployment.environment","kagent-lab"),
            attribute("host.name","primary-devbox"),
        ]},"scopeSpans":[{"scope":{"name":"slopmop.swarm","version":"1"},
            "spans":spans}]}]}
        request=urllib.request.Request(self.endpoint,data=json.dumps(payload).encode(),
            headers={"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(request,timeout=5) as response:
                response.read()
                if response.status//100!=2:
                    raise RuntimeError("OTLP returned HTTP "+str(response.status))
            with self.lock:
                self.emitted+=len(spans)
                self.last_success=time.time()
                self.last_error=None
        except Exception as error:
            with self.lock:
                self.last_error=str(error)[:500]

    @staticmethod
    def _span(trace_id,span_id,parent_span_id,name,start,end,values,failed=False):
        body={"traceId":trace_id,"spanId":span_id,"name":name,"kind":1,
            "startTimeUnixNano":str(int(start*1_000_000_000)),
            "endTimeUnixNano":str(int(max(end,start+.000001)*1_000_000_000)),
            "attributes":[attribute(key,value) for key,value in values.items()
                if value is not None and isinstance(value,(str,int,float,bool))],
            "status":{"code":2 if failed else 1}}
        if parent_span_id:
            body["parentSpanId"]=parent_span_id
        return body

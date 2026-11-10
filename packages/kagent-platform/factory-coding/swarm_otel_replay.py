"""Replay retained safe swarm lifecycle metadata into Tempo."""
import argparse
import json
from pathlib import Path

from gateway_store import Store
from otel_exporter import OtelExporter


STATE=Path("/home/nibeauli/kagent-lab/factory-coding")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default=str(STATE/"service-swarm-manager.json"))
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    exporter=OtelExporter(config.get("otelHttpEndpoint"))
    store=Store(config["postgres"],1,20,"swarm-manager","campaign")
    campaigns=events=0
    for task in store.list():
        manifest=task.get("result") or {}
        values=manifest.get("events") or []
        if not values:
            continue
        campaigns+=1
        stage="starting"
        for index,event in enumerate(values):
            if event.get("event")=="stage_changed" and event.get("stage"):
                stage=event["stage"]
            elif event.get("event")=="campaign_completed":
                stage="completed"
            elif event.get("event")=="campaign_failed":
                stage="failed"
            safe={"campaign.id":manifest.get("campaign"),
                "campaign.stage":event.get("stage") or stage,
                "eventIndex":index}
            for key,value in event.items():
                if key in {"role","key","task","context","stage","count","approved",
                           "total","implementations","index"}:
                    safe["swarm."+key]=value
            exporter.emit(task["id"],event.get("event","unknown"),event.get("time"),
                safe,first_time=values[0].get("time"),
                terminal=event.get("event") in {"campaign_completed","campaign_failed"})
            events+=1
    store.close()
    status=exporter.status()
    if status["lastError"]:
        raise RuntimeError(status["lastError"])
    print(json.dumps({"campaigns":campaigns,"events":events,
        "spansEmitted":status["emitted"]}))


if __name__=="__main__":
    main()

"""Root-only revocation of settled direct-gateway actor authority."""
import argparse
import json
from pathlib import Path

from backend import Backend
from gateway_store import Store, TERMINAL

STATE=Path("/home/nibeauli/kagent-lab/factory-coding")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["revoke"])
    parser.add_argument("task")
    args=parser.parse_args()
    config=json.loads((STATE/"service.json").read_text())
    store=Store(config["postgres"],config["maximum"],config["backlog"],
        config["gatewayName"],config["taskPrefix"])
    task=store.get(args.task)
    if not task or task["state"] not in TERMINAL or not task["actor_uid"]:
        raise RuntimeError("only a settled direct-gateway actor may be revoked")
    if any(row["id"]!=task["id"] and row["session"]==task["session"] and row["state"] not in TERMINAL
           for row in store.list()):
        raise RuntimeError("session still has active or queued work")
    backend=Backend(config)
    try:
        backend.revoke_binding(task)
    finally:
        backend.close()
        store.close()
    print("Settled actor authorization revoked; Kagent session and task receipts retained.")


if __name__=="__main__":
    main()

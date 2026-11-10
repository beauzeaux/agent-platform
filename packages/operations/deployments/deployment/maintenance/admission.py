"""Pause and restore only the explicitly owned factory admission units."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess

STATE = Path("/home/nibeauli/kagent-lab/host-maintenance")
UNITS = (
    "factory-ado-triage.service", "factory-coding.service", "factory-documentation-steward.service",
    "factory-implementation-reviewer.service", "factory-opportunity-scout.service", "factory-plan-reviewer.service",
    "factory-planner.service", "factory-pr-pattern-miner.service", "factory-swarm-manager.service",
)
TIMER = "factory-swarm-autonomous.timer"


def command(*arguments, **kwargs):
    return subprocess.run(arguments, **kwargs)


def pause():
    if os.geteuid() != 0:
        raise RuntimeError("Run as primary WSL root.")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    record = STATE / "admission.json"
    if record.exists() and json.loads(record.read_text()).get("paused"):
        raise RuntimeError("Admission is already paused; preserve its original restore state.")
    active = {}
    for unit in (*UNITS, TIMER):
        path = command("systemctl", "show", unit, "-p", "FragmentPath", "--value",
                       capture_output=True, text=True, check=True).stdout.strip()
        if path != "/etc/systemd/system/" + unit:
            raise RuntimeError("Refusing to pause an unexpected admission unit.")
        if unit != TIMER:
            launch = command("systemctl", "show", unit, "-p", "ExecStart", "--value",
                             capture_output=True, text=True, check=True).stdout
            if "/mnt/q/tools/kagent-lab/factory-coding/" not in launch:
                raise RuntimeError("Admission implementation ownership changed.")
        active[unit] = command("systemctl", "is-active", "--quiet", unit).returncode == 0
    value = {"paused": True, "before": active,
             "startedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    record.write_text(json.dumps(value, indent=2))
    record.chmod(0o600)
    command("systemctl", "stop", TIMER, *UNITS, check=True, timeout=120)
    print("Nine owned factory admission services and the autonomous timer are paused.")


def resume():
    record = STATE / "admission.json"
    value = json.loads(record.read_text())
    if not value.get("paused"):
        print("No paused admission state remains.")
        return
    for unit in UNITS:
        if value["before"].get(unit):
            command("systemctl", "start", unit, check=True, timeout=60)
    if value["before"].get(TIMER):
        command("systemctl", "start", TIMER, check=True, timeout=30)
    value["paused"] = False
    value["resumedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record.write_text(json.dumps(value, indent=2))
    record.chmod(0o600)
    print("Original factory admission activity restored; no work was resubmitted.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["pause", "resume"])
    args = parser.parse_args()
    pause() if args.action == "pause" else resume()

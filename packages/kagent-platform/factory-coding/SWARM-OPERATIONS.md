# SlopMop swarm operations

## Endpoints

| Agent | Port |
|---|---:|
| Coding | 18443 |
| Opportunity scout | 18444 |
| Planner | 18445 |
| Plan reviewer | 18446 |
| Implementation reviewer | 18447 |
| Documentation steward | 18448 |
| ADO triage | 18449 |
| PR pattern miner | 18450 |
| Swarm Manager | 18451 |

Every endpoint serves `/.well-known/agent-card.json`, `/readyz`, and authenticated
JSON-RPC A2A at `/a2a`. The manager also serves:

- dashboard: `https://10.82.0.2:18451/dashboard`
- redacted campaign projection: `https://10.82.0.2:18451/api/campaigns`
- server-sent events: `https://10.82.0.2:18451/events`

Windows host access uses a separate read-only WSL localhost listener:

- dashboard: `http://localhost:18452/dashboard`
- redacted campaign projection: `http://localhost:18452/api/campaigns`
- server-sent events: `http://localhost:18452/events`

The host listener does not expose A2A POST operations or credentials. The
authenticated control endpoint remains TLS-only on the WireGuard overlay.

Grafana host access is `http://localhost:13000`. The provisioned **SlopMop
Autonomous Swarm** dashboard uses Tempo for safe campaign/child lifecycle spans,
Loki for Kubernetes infrastructure logs, and Prometheus for target health.
Swarm spans intentionally exclude prompts, source code, tool payloads, and
credentials.

The dashboard is agent-centric and split into Campaign View, Current Cluster,
and Swarm Runs tabs. Campaign View selects one retained campaign and renders its
actual child submissions/completions as a UML-style participant/lifeline
sequence. The same view groups that campaign's actor contexts by their observed
node. Placement is captured into the parent manifest at child completion;
historical sessions that predate capture remain explicitly unknown.

Current Cluster stacks fixed WSL services, Kubernetes control pods, and currently
assigned actor sessions under their actual node. Suspended actor sessions are
grouped separately because ATE removes their current node assignment while
parked. A swarm run (campaign) is one parent goal and its complete child-task
lineage; the summary view leads with outcome, phases, metrics, and PR links while
keeping task IDs and raw recent events collapsed.

## Deployment

From root in the primary WSL distribution:

```bash
python3 /mnt/q/tools/kagent-lab/factory-coding/build.py managed
python3 /mnt/q/tools/kagent-lab/factory-coding/install.py control-plane
python3 /mnt/q/tools/kagent-lab/factory-coding/install.py proxy-bindings
python3 /mnt/q/tools/kagent-lab/factory-coding/install.py observability
python3 /mnt/q/tools/kagent-lab/factory-coding/install.py service
```

`proxy-bindings` performs the one shared host-broker activation required after
broker code or policy changes. New actor bindings do not restart that process.

## Autonomous discovery loop

`factory-swarm-autonomous.timer` submits one idempotent campaign per 12-hour UTC
window, with up to ten minutes of jitter. It skips submission while any campaign
is active. The bounded default is:

- seven-day evidence lookback;
- at most 12 ADO PRs selected for substantive review or engsys changes;
- read-only review-thread, diff, and targeted code/history inspection;
- at most three curated candidates;
- at most one implementation;
- all writes confined to the existing disposable branch and owned PR prefixes.

The Evidence Miner runs before Opportunity Scout. Scout receives its signals,
explicit triage evidence, and a bounded inventory of prior campaign candidates,
review dispositions, and PR outcomes. The manager computes a deterministic
priority score from Scout's 0-5 evidence strength, generality,
false-positive-risk, and implementation-fit dimensions.

Run a plan-only crawl immediately:

```bash
sudo python3 /mnt/q/tools/kagent-lab/factory-coding/swarm_autonomous.py
```

Run the same bounded crawl through implementation:

```bash
sudo python3 /mnt/q/tools/kagent-lab/factory-coding/swarm_autonomous.py --execute
```

## Campaign request

Use the protected operator token and submit:

```json
{
  "jsonrpc": "2.0",
  "id": "request-id",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "stable-campaign-request-id",
      "role": "ROLE_USER",
      "parts": [{
        "data": {
          "campaign": "demo-identifier",
          "goal": "Bounded SlopMop campaign goal",
          "candidateLimit": 3,
          "implementationLimit": 1,
          "execute": true,
          "approvedPrUrls": [],
          "triageWorkItemIds": []
        }
      }]
    }
  }
}
```

Retry only with the same `messageId` and input. Poll the returned task with
`GetTask`. The task artifact is the durable campaign manifest and child-task
lineage.

## Recovery

Retained failed campaigns can be resumed without replaying completed child work:

```bash
sudo python3 /mnt/q/tools/kagent-lab/factory-coding/swarm_operate.py \
  resume <campaign>
```

The manager reads every retained child with `GetTask` before attempting a new
submission. Completed answers and tool receipts are adopted into the manifest;
failed terminal children fall through to the existing bounded recovery key.

If an operator had to run an equivalent child outside the normal manager path,
adopt only a completed `swarm-manager`-owned task:

```bash
sudo python3 /mnt/q/tools/kagent-lab/factory-coding/swarm_operate.py \
  adopt <campaign> --role <role> --key <manifest-key> --task <task-id>
```

## Safety boundaries

- Integration target: `users/nibeauli/slopmop-devel`
- Owned source prefix: `users/nibeauli/slopmop-demo-`
- Never target or update `main`.
- Coding and documentation actors use `slopmop-contributor`.
- Reader roles cannot load ADO or PR write tools.
- Triage can update scoped work items but cannot create repository PRs.
- The broker validates SlopMop source and target branches before PR creation.
- The broker resolves the explicit PR URL before allowing `finish-pr`.
- Review agents cannot edit implementation branches.

## Reset

Inspect the exact owned artifacts first:

```bash
python3 /mnt/q/tools/kagent-lab/factory-coding/swarm_reset.py <campaign>
```

Apply only after every campaign PR is terminal:

```bash
python3 /mnt/q/tools/kagent-lab/factory-coding/swarm_reset.py <campaign> \
  --apply \
  --baseline <recorded-40-character-commit> \
  --confirm users/nibeauli/slopmop-devel
```

The reset archives the manifest, revokes exact actor bindings, deletes exact
Kagent sessions and task rows, deletes campaign source branches, and resets only
the disposable integration target with a force-with-lease.

## Validation

```bash
python3 -m unittest discover \
  -s /mnt/q/tools/kagent-lab/factory-coding \
  -p 'test_*.py' -v

python3 /mnt/q/tools/kagent-lab/factory-coding/test_swarm_live.py
python3 /mnt/q/tools/kagent-lab/factory-coding/test_swarm_execute.py
```

Protected acceptance reports and reset archives are retained under
`/home/nibeauli/kagent-lab/factory-coding`.

## Pilot limitations

- One process per role and one Swarm Manager process; no HA claim.
- PostgreSQL and the XL-hosted database remain dependencies.
- Agent outcomes are best-effort durable, not exactly once.
- Unknown native delivery is observed without blind replay.
- The dashboard is a read-only projection of authoritative A2A, Kagent, Git, and
  ADO state.
- The pre-existing devbox-2 node and original Agency actor remain unavailable;
  swarm execution uses the Ready primary and XL workers.

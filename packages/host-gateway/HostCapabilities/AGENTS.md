# Host capability integration

This repository is the supported Windows host integration and deployment home.
Preserve the existing Copilot, Agency, and ADO protocol/credential boundaries.

- Use only the configured 1ES feeds for package restoration.
- Runtime credentials, certificates, and generated configuration stay outside source control.
- Run the smallest relevant tests. No live inference, token output, or remote writes in unit tests.
- Preserve strict TLS verification and never replay accepted inference or Git/MCP writes.
- Keep capabilities independently bounded and independently ready.
- Manual source edits use apply_patch. Do not overwrite another contributor's changes.
- Keep source dependency paths explicit; publish output must run independently of source directories except configured Agency artifacts.

## Kagent deployment model

- Prefer the existing factory Kagent controller and `kagent.internal` dashboard, with a separate namespace per workload.
- Use the UI namespace selector; do not deploy another controller/dashboard merely to add a namespace.
- Enroll namespaces explicitly in the shared instance's watch/RBAC scope and provision their required runtime/worker/credential scope.
- Preserve existing namespace watches, credential grants, storage and workloads on upgrades; never substitute an unrestricted watch-all.
- A separate Kagent instance requires a demonstrated isolation need and explicit operator approval. The legacy pilot was retired; do not reinstall it.
- Recurring real-model work requires an explicit cadence and stop/quota policy; namespace enrollment does not authorize an infinite loop.

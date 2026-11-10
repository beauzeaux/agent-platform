# Operations

Cross-package lifecycle and operator material:

- current platform operations runbook;
- certificate enablement;
- Windows-to-WSL invocation helper;
- maintenance and native-canary deployments;
- consolidated current-state documentation under `../../docs/current-state`.

Operational automation should call package-owned deployment/build commands rather than duplicating their logic.
Temporary port forwards are diagnostics, not durable service definitions.

# Validation status

Validated from the imported paths on 2026-11-09:

| Area | Result |
|---|---|
| Repository layout and protected-path scan | Passed |
| AgencyCoder | 20 passed |
| HostCapabilities | 50 passed, 1 skipped |
| NativeAdo | 21 passed |
| LocalLlmGateway | 92 passed |
| AdoDevProxy | 39 passed |
| Agency broker and Windows relay | 28 passed, 1 skipped |
| Proxy-access host/native bridge | 29 passed |
| WireGuard WSL bootstrap | Passed |
| AgencyHarness shell syntax | Passed |

Go and Helm validation remain pending because neither executable is currently installed on the Windows host or the
`Ubuntu-26.04` WSL distribution. Podman is also unavailable there; OCI work must remain Podman-first when a suitable
runner is provisioned.

The test runner removes generated build/cache directories after execution. Live deployment cutover has not occurred;
the currently running platform still uses the original source locations documented in `MIGRATION.md`.

## Open live acceptance findings

The final disposable engsys actor proved sandboxed shell execution, preserved a clean repository and unchanged
`.npmrc`, and created no repository credential file. Authentication acceptance is not complete:

- Azure CLI account and token commands timed out.
- NuGet restore could not resolve the feed hostname.
- The npm credential-provider runtime could not load `libhostpolicy.so`.

These are tracked as runtime/network packaging defects, not consolidation failures. Do not treat the current engsys
image as accepted for Azure/NuGet/npm until those three failures are corrected and the bounded test is repeated.

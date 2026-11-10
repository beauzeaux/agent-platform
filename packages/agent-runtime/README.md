# Agent runtime

Repository-neutral OCI runtime source:

- `AgencyHarness`: common runtime/toolchain image and automatic Azure Artifacts/Azure CLI wrappers.
- `AgencyCoder`: .NET adapter around GitHub Copilot SDK.
- `AgencyCoder.Tests`: permission, repository, plugin, and orchestration tests.
- `deployment/agency-coder`: generic native Kagent deployment contract.

The runtime does not select a repository. Repository-specific image layers live in `../agent-workloads`.
Images are built before deployment and charts/manifests consume immutable digests.

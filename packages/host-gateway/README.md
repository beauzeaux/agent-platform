# Host gateway

This package owns the Windows capability boundary:

- `HostCapabilities`: actor admission, Agency policy/plugin preparation, model routing, WAM-backed IMDS, and health.
- `LocalLlmGateway`: local inference gateway implementation.
- `ado-dev-proxy`: retained ADO proxy and WAM provider source. Its credential-injecting frontend remains disconnected.

The original relative .NET project references are preserved by placing the three source trees beside one another.
Production configuration, WAM state, certificates, actor registries, tokens, and published `.artifacts` are excluded.

Kubernetes-facing services and egress resources live with the packages that own them; Windows service installation and
protected configuration remain operational concerns.

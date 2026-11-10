# Agent platform repository

This repository consolidates the supported host, network, Kagent/Substrate, agent runtime, identity, state,
observability, and operations source for the devbox agent platform.

- Use only configured 1ES feeds for package restoration.
- Prefer Podman for OCI operations when available; use another OCI client only when Podman is unavailable.
- Never commit credentials, private keys, certificates, actor registries, production configuration, session state,
  generated deployment payloads, caches, archives, or build outputs.
- Kubernetes components may use package-owned Helm charts. Windows services, WSL networking, WireGuard, and host
  lifecycle remain host automation and must not be hidden behind Helm.
- Keep package boundaries explicit. Cross-package dependencies must use documented paths or published artifacts.
- Preserve strict TLS and actor authorization boundaries. Do not reconnect the legacy credential-injecting proxy.
- Validate the smallest affected package first, then run repository-wide structural checks.

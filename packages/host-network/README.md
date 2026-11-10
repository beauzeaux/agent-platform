# Host network

This package owns transport below the application layer:

- `wireguard-hub`: Azure Bicep and WSL peer bootstrap for the hub-and-spoke overlay.
- `proxy-access`: the cluster-to-host bridge, its Helm chart, tests, and exact egress examples.
- `microk8s-storage`: host disk attachment and MicroK8s hostpath preparation.

Private WireGuard keys, deployed bridge configuration, generated packages, certificates, and public actor projections
are intentionally excluded.

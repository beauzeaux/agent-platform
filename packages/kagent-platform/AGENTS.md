# Kagent lab deployment conventions

Prefer the existing factory Kagent controller and `http://kagent.internal` UI,
with a separate Kubernetes namespace per workload. The dashboard's namespace
selector spans managed namespaces; a namespace is not a separate Kagent instance.
Do not install another controller or UI without a demonstrated isolation need
and explicit operator approval. The legacy pilot was retired; do not reinstall it.

Namespace enrollment includes explicit watch/RBAC and the required namespace-local
runtime space, worker pool and credential policy. Preserve all existing watch
namespaces and credential grants on upgrades; do not broaden them to watch-all.
Namespace preparation alone does not authorize recurring or indefinite model work.

Use the supported integration/deployment source in
`Q:\src\tools\HostCapabilities`. Protect credentials and generated state, never
replay uncertain accepted work, and delete only verified owned resources.

# Read-only cluster dashboard

**URL: http://kagent.internal/headlamp/**

Official Headlamp 0.46.0 runs in namespace `headlamp`, behind the existing
loopback-only front door. It views the same three-node MicroK8s cluster as Kagent,
including `baseline`, `cluster-management`, system namespaces and deployments.
The Kagent UI remains at http://kagent.internal/.

For login, copy a short-lived read-only token without printing it:

```powershell
& Q:\src\tools\HostCapabilities\deployment\headlamp\Copy-ViewerToken.ps1
```

Paste into Headlamp's token login. The requested lifetime is eight hours.
There is no anonymous login or cluster-admin binding. The dedicated viewer
can inspect namespace/workload metadata but cannot read Secrets, exec,
port-forward or change workloads. ConfigMaps and pod logs are not granted.

Actual viewer-token checks passed: namespaces/deployments readable (200),
Headlamp namespace proxy readable (200), Secrets and namespace creation denied
(403), exec denied by self-access review. Page/assets and the existing Kagent
dashboard returned 200 from Windows.

Deployment source, pinned chart/checksum, reapply steps and permission manifest:
`Q:\src\tools\HostCapabilities\deployment\headlamp`.

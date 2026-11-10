# Kagent platform

Kagent/Substrate control-plane and workload-management source:

- shared controller and Substrate values;
- namespace enrollment, worker placement, golden-template and BYO validation;
- the factory controller/UI backend source;
- baseline, Headlamp, and A2A sanity deployments;
- bootstrap helper source.

This package must preserve explicit watched namespaces, worker pools, storage, and credential grants. It must not
install another controller merely to add a workload namespace or broaden access to watch all namespaces.

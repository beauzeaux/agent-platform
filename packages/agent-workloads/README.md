# Agent workloads

Thin repository-specific layers over the shared `agent-runtime` coding image:

- `deployment/agency-coder/engsys`: engsys repository and feed declarations.
- `deployment/agency-coder/audit`: Audit repository declaration.

These packages may add repository-required tools and feed endpoints, but must not duplicate the common harness,
credential framework, or controller configuration. Repository CI owns image build and publication; deployment values
select the resulting immutable digest.

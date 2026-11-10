# Existing Kagent dashboards under the cluster portal

Run `python3 ui_subpaths.py --apply` in Ubuntu to configure the existing
factory and pilot UI ConfigMaps and restart only their UI Deployments.
Without `--apply` the helper only inspects resources. Deployment, environment
ConfigMap and nginx configuration snapshots are retained in a root-only
directory outside source control; the factory nginx Secret must remain private.

The installed RC image supports `KAGENT_UI_BASE_PATH` at container startup:
it renders `BASE_PATH` in `env-config.js`, sets the HTML base element, uses a
React Router basename and prefixes root-relative API and SSO URLs.
Keep `KAGENT_UI_API_BASE_URL=/api`: setting it to `/kagent/api` would double-prefix it.

The front door **must strip** `/kagent` for `factory-ui.factory-executions:8080`
and `/pilot` for `kagent-ui.kagent:8080`. Their existing nginx configurations
remain unchanged: upstream `/assets/*`, `/env-config.js`, `/api/*`, `/a2a/*`,
`/mcp` and SPA deep links retain their original routing and streaming behavior.
Redirect each bare public prefix to its trailing-slash form.
Old browser hostnames should redirect to the corresponding cluster portal prefix;
old API clients can continue using the unprefixed upstream endpoints.
Do not expose browser assets or navigation at the portal root.

Reapply this helper after a chart upgrade that replaces the UI ConfigMaps.
Run focused tests with `python3 -m unittest test_ui_subpaths.py`.

The prefixes are hostname-independent. The canonical origin is
`http://cluster.internal`; `http://localhost` remains a compatibility/test alias.
This helper neither changes DNS nor writes the Windows hosts file.

## Verification

Both existing RC UI Deployments were rolled out with the supported environment
setting, without rebuilding the image or changing nginx/controller configuration.
The focused helper tests passed (2 tests).
Installed Edge with cached Playwright exercised `/kagent/agents` and
`/pilot/agents`, then reloaded each deep link, using a test-only prefix-stripping
request fixture against the existing host routes. Both pages rendered with no
JavaScript errors; the entry bundle, lazy runtime bundle, favicon,
`env-config.js`, `oauth2/userinfo` and list RPCs returned HTTP 200 under their
own public prefixes. Same-origin navigation used the correct router basename.
Read-only namespace RPCs returned the existing factory namespaces
`baseline`, `cluster-management`, `factory-executions`, and pilot namespace
`kagent`. No inference or mutation actions were performed.

These observations verify the UI backend contract, not the final front-door
configuration. The portal owner must verify the canonical origin after applying
Caddy routes. Streaming actions were not invoked; existing nginx upgrade headers,
1800-second proxy timeouts and disabled buffering were preserved untouched.

ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG BASE_MANIFEST
ARG SOURCE_SHA256
LABEL io.host-capabilities.public-canary="true" \
      io.host-capabilities.base-manifest="${BASE_MANIFEST}" \
      io.host-capabilities.source-sha256="${SOURCE_SHA256}"
USER root
COPY public-probe.mjs /app/coder/agent.mjs
COPY probe-worker.mjs /app/coder/probe-worker.mjs
COPY pins.json /app/coder/public-probe-pins.json
RUN test -x /app/harness && test -r /app/coder/session-archive.mjs && \
    test -r /app/coder/native-memory.mjs && test -r /app/coder/native-config.mjs && \
    test -x /app/coder/node_modules/@github/copilot-linux-x64/copilot
USER 10001:10001
ENV PUBLIC_CANARY_INFERENCE=false COPILOT_OFFLINE=true \
    PUBLIC_CANARY_BASE_MANIFEST="${BASE_MANIFEST}" PUBLIC_CANARY_SOURCE_SHA256="${SOURCE_SHA256}" \
    COPILOT_TELEMETRY_DISABLED=true COPILOT_GITHUB_TELEMETRY_DISABLED=true \
    HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= http_proxy= https_proxy= all_proxy=
ENTRYPOINT ["/app/harness"]
CMD []

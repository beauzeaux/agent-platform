ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_SHA256
LABEL io.host-capabilities.a2a-sanity="true" io.host-capabilities.public-canary="false" \
      io.host-capabilities.source-sha256="${SOURCE_SHA256}"
USER root
COPY entry.mjs /app/coder/agent.mjs
COPY worker.mjs public-sdk.mjs /app/coder/
USER 10001:10001
ENV A2A_SANITY_LIVE=false COPILOT_OFFLINE=true \
    COPILOT_TELEMETRY_DISABLED=true COPILOT_GITHUB_TELEMETRY_DISABLED=true \
    HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= http_proxy= https_proxy= all_proxy=
ENTRYPOINT ["/app/harness"]
CMD []

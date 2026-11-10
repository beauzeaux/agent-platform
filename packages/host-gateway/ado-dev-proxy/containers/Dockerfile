ARG NODE_IMAGE=node:24-bookworm-slim
ARG SDK_IMAGE=mcr.microsoft.com/dotnet/sdk:10.0
FROM ${NODE_IMAGE} AS node
FROM ${SDK_IMAGE}
COPY --from=node /usr/local/ /usr/local/
COPY containers/entrypoint.sh /usr/local/bin/ado-proxy-entrypoint
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/ado-dev-proxy.crt \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    GIT_TERMINAL_PROMPT=0
WORKDIR /work
ENTRYPOINT ["/bin/sh", "/usr/local/bin/ado-proxy-entrypoint"]
CMD ["sleep", "infinity"]

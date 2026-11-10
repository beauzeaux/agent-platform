FROM kagent-engsys-agency-coder:local
USER root
COPY managed /app/harness
COPY controller /controller
COPY agent.mjs /app/coder/agent.mjs
COPY agency.mjs /app/coder/agency.mjs
COPY contribution-tools.mjs repo-tools.mjs audit-ui.mjs /app/coder/
COPY native-config.mjs session-archive.mjs native-memory.mjs /app/coder/
RUN chmod 755 /app/harness /controller && mkdir -p /config /data && chown 10001:10001 /config /data
USER 10001:10001
ENV READY_PORT=18081 HTTP_PROXY=http://127.0.0.1:15480 HTTPS_PROXY=http://127.0.0.1:15480 \
    http_proxy=http://127.0.0.1:15480 https_proxy=http://127.0.0.1:15480 \
    NO_PROXY=localhost,127.0.0.1,.svc,.cluster.local,10.152.183.0/24 \
    no_proxy=localhost,127.0.0.1,.svc,.cluster.local,10.152.183.0/24 \
    SESSION_URL=https://agent-proxies.proxy-access.svc.cluster.local:8443/session \
    DATA_URL=https://agent-proxies.proxy-access.svc.cluster.local:8444 \
    EGRESS_CA_FILE=/run/kagent/egress/trust-bundle.pem \
    SSL_CERT_FILE=/run/kagent/egress/trust-bundle.pem \
    NUGET_CLI_CERT_REVOCATION_MODE=offline
ENTRYPOINT ["/controller"]
CMD []

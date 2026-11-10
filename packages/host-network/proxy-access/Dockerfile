FROM ado-dev-proxy-agent:local
USER root
WORKDIR /app
COPY gateway.js client.js ./
COPY chart/files/*.js ./chart/files/
COPY chart/files/*.js /opt/proxy/
COPY public/gateway-ca.crt /opt/proxy/gateway-ca.crt
COPY public/ado-ca.crt /opt/proxy/ado-ca.crt
COPY actor_probe.js /opt/proxy/actor_probe.js
COPY public/ado-ca.crt /usr/local/share/ca-certificates/ado-dev-proxy.crt
RUN update-ca-certificates
USER 10001:10001
ENTRYPOINT ["node"]
CMD ["/opt/proxy/client.js"]

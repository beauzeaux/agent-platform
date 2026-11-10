'use strict';

const fs = require('node:fs');
const https = require('node:https');
const { createActorVerifier, createTokenStore, bearer } = require('./auth');
const {agencyRoute, configuredBrokerActor, brokerAllowed, forwardAgency} = require('./agency-transport');
const { DEFAULT_LIMITS, authority, status, socketStatus, modelRoute, bodyAllowed,
  forwardModel, connectTunnel, connectFailureStatus, pipeTunnel, hardenServer, createBudget,
  destroyServerConnections } = require('./transport');

function createGateway(config) {
  const limits = { ...DEFAULT_LIMITS, ...config.limits };
  const verifyActor = createActorVerifier(config.auth, config.clock);
  const tokens = createTokenStore({ key: config.tokenSigningKey, clock: config.clock });
  const acquire = createBudget(limits.maxConcurrent);
  const acquireBroker = createBudget(4);
  const log = config.log || (() => {});
  const tlsOptions = { minVersion: 'TLSv1.2', cert: config.tls.cert, key: config.tls.key,
    maxHeaderSize: 16384, handshakeTimeout: limits.headersTimeoutMs };
  const bridgeOptions = { host: config.bridge.host, servername: config.bridge.servername,
    minVersion: 'TLSv1.2', rejectUnauthorized: true, ca: config.bridge.ca,
    cert: config.bridge.cert, key: config.bridge.key };
  const agent = new https.Agent({ keepAlive: true, maxSockets: limits.maxConcurrent,
    maxFreeSockets: 4, ...bridgeOptions });
  const sessionServer = https.createServer(tlsOptions, (req, res) => {
    res.once('finish', () => log({ endpoint: 'session', status: res.statusCode }));
    if (req.method === 'GET' && req.url === '/health') return status(res, 204);
    if (req.method !== 'POST' || req.url !== '/session') return status(res, 404);
    if (!bodyAllowed(req, { maxBodyBytes: 1024 })) return status(res, 413);
    let actor;
    try { actor = verifyActor(bearer(req.headers.authorization)); }
    catch { return status(res, 401); }
    if (configuredBrokerActor(actor, config.broker)) actor.capabilities = ['agency'];
    const release = acquire();
    if (!release) return status(res, 503);
    res.once('close', release);
    let bytes = 0;
    req.on('data', chunk => {
      bytes += chunk.length;
      if (bytes > 1024) { status(res, 413); req.destroy(); }
    });
    req.once('end', () => {
      if (res.destroyed || res.writableEnded) return;
      try {
        const body = JSON.stringify(tokens.issue(actor));
        res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store',
          'content-length': Buffer.byteLength(body) });
        res.end(body);
      } catch { status(res, 401); }
    });
  });
  const dataServer = https.createServer(tlsOptions, (req, res) => {
    res.once('finish', () => log({ endpoint: 'model', status: res.statusCode }));
    if (req.method === 'GET' && req.url === '/health') return status(res, 204);
    if (agencyRoute(req)) {
      let actor;
      try { actor = tokens.verify(bearer(req.headers.authorization)); }
      catch { return status(res, 401); }
      if (!brokerAllowed(actor, config.broker)) return status(res, 403);
      const release = acquireBroker();
      if (!release) return status(res, 503);
      res.once('close', release);
      void forwardAgency(req, res, {...bridgeOptions, port: config.bridge.brokerPort ?? 15442,
        headers: {'x-agency-actor': actor.sub, 'x-agency-actor-uid': actor.actorUID}});
      return;
    }
    if (!modelRoute(req)) return status(res, 404);
    try { tokens.verify(bearer(req.headers.authorization)); }
    catch { return status(res, 401); }
    const release = acquire();
    if (!release) return status(res, 503);
    res.once('close', release);
    forwardModel(req, res, { ...bridgeOptions, port: config.bridge.modelPort, agent }, limits);
  });
  dataServer.on('connect', async (req, socket, head) => {
    let target;
    try { target = authority(req.url); }
    catch { log({ endpoint: 'connect', status: 400 }); return socketStatus(socket, 400); }
    try { tokens.verify(bearer(req.headers['proxy-authorization'])); }
    catch { log({ endpoint: 'connect', status: 401 }); return socketStatus(socket, 401); }
    const release = acquire();
    if (!release) return socketStatus(socket, 503);
    socket.once('close', release);
    socket.on('error', () => {});
    const cancellation = new AbortController();
    socket.once('close', () => cancellation.abort());
    // The bridge is an mTLS HTTP CONNECT service, never an arbitrary TCP dialer.
    try {
      const upstream = await connectTunnel(target,
        { ...bridgeOptions, port: config.bridge.connectPort, signal: cancellation.signal }, limits);
      log({ endpoint: 'connect', status: 200 });
      pipeTunnel(socket, head, upstream.socket, upstream.head, limits);
    } catch (error) {
      const code = connectFailureStatus(error);
      log({ endpoint: 'connect', status: code });
      socketStatus(socket, code);
    }
  });
  sessionServer.on('connect', (_req, socket) => socketStatus(socket, 405));
  hardenServer(sessionServer, limits);
  hardenServer(dataServer, limits);
  return { sessionServer, dataServer, tokens,
    async listen() {
      await Promise.all([
        listen(sessionServer, config.sessionPort ?? 8443, config.listenHost ?? '0.0.0.0'),
        listen(dataServer, config.dataPort ?? 8444, config.listenHost ?? '0.0.0.0')
      ]);
    },
    async close() {
      agent.destroy();
      await Promise.all([closeServer(sessionServer), closeServer(dataServer)]);
    }
  };
}

function listen(server, port, host) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => { server.removeListener('error', reject); resolve(); });
  });
}

function closeServer(server) {
  return new Promise(resolve => {
    server.close(resolve);
    server.closeAllConnections();
    destroyServerConnections(server);
  });
}

function loadConfig(env = process.env) {
  const config = JSON.parse(fs.readFileSync(env.GATEWAY_CONFIG || '/app/config/config.json', 'utf8'));
  const dir = env.TLS_DIRECTORY || '/app/tls';
  const names = config.tlsNames || {};
  const read = name => fs.readFileSync(`${dir}/${name}`);
  config.tls = { cert: read(names.serverCert || 'tls.crt'), key: read(names.serverKey || 'tls.key') };
  config.bridge = { ...config.bridge, ca: read(names.ca || 'ca.crt'),
    cert: read(names.clientCert || 'bridge-client.crt'), key: read(names.clientKey || 'bridge-client.key') };
  if (names.tokenSigningKey) config.tokenSigningKey = read(names.tokenSigningKey);
  config.log = record => process.stdout.write(`${JSON.stringify(record)}\n`);
  return config;
}

function startGateway() {
  let gateway;
  try {
    gateway = createGateway(loadConfig());
    gateway.listen().catch(() => { console.error('gateway startup failed'); process.exit(1); });
  } catch { console.error('gateway configuration invalid'); process.exit(1); }
  for (const signal of ['SIGTERM', 'SIGINT']) {
    process.on(signal, () => gateway.close().then(() => process.exit(0)));
  }
}

if (require.main === module) startGateway();

module.exports = { createGateway, loadConfig, listen, closeServer, startGateway };

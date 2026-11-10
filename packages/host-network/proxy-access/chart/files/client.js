'use strict';

const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const { listen, closeServer } = require('./gateway');
const {agencyRoute, forwardAgency} = require('./agency-transport');
const { DEFAULT_LIMITS, authority, status, socketStatus, modelRoute, forwardModel,
  connectTunnel, connectFailureStatus, pipeTunnel, hardenServer, createBudget } = require('./transport');

function endpointOptions(url, ca, servername) {
  const endpoint = new URL(url);
  if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password ||
      endpoint.search || endpoint.hash) throw new Error('HTTPS endpoint required');
  return { hostname: endpoint.hostname, port: Number(endpoint.port || 443),
    servername: servername || endpoint.hostname, ca, rejectUnauthorized: true,
    minVersion: 'TLSv1.2', path: endpoint.pathname };
}

function createSessionClient(config) {
  const options = endpointOptions(config.sessionUrl, config.ca, config.sessionServername);
  const clock = config.clock || (() => Date.now() / 1000);
  let cached = null;
  let pending = null;
  async function fetchSession() {
    return new Promise((resolve, reject) => {
      const started = clock();
      const req = https.request({ ...options, agent: false, method: 'POST',
        headers: { authorization: 'Bearer egress-injected', 'content-length': '0' },
        maxHeaderSize: 16384 }, res => {
        if (res.statusCode !== 200) { res.resume(); reject(new Error('session refused')); return; }
        const chunks = [];
        let size = 0;
        res.on('data', chunk => {
          size += chunk.length;
          if (size > 8192) req.destroy(new Error('session response limit'));
          else chunks.push(chunk);
        });
        res.once('error', reject);
        res.once('end', () => {
          try {
            const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
            if (data.token_type !== 'Bearer' || typeof data.access_token !== 'string' ||
                !/^[A-Za-z0-9_.-]+$/.test(data.access_token) || data.access_token.length > 4096 ||
                !Number.isFinite(data.expires_in) || data.expires_in <= 0 || data.expires_in > 300) {
              throw new Error('invalid session response');
            }
            cached = { token: data.access_token, expiresAt: started + data.expires_in };
            resolve(cached.token);
          } catch { reject(new Error('invalid session response')); }
        });
      });
      const timer = setTimeout(() => req.destroy(new Error('session timeout')),
        config.sessionTimeoutMs || 15000);
      timer.unref();
      req.once('close', () => clearTimeout(timer));
      req.once('error', reject);
      req.end();
    });
  }
  return {
    getToken() {
      if (cached && cached.expiresAt > clock() + 5) return Promise.resolve(cached.token);
      if (!pending) pending = fetchSession().finally(() => { pending = null; });
      return pending;
    },
    invalidate(token) { if (cached?.token === token) cached = null; }
  };
}

function createAdapter(config) {
  const limits = { ...DEFAULT_LIMITS, ...config.limits };
  const session = createSessionClient(config);
  const data = endpointOptions(config.dataUrl, config.ca, config.dataServername);
  if (data.path !== '/') throw new Error('data URL must have no path');
  const acquire = createBudget(limits.maxConcurrent);
  const acquireBroker = createBudget(4);
  const log = config.log || (() => {});
  const agent = new https.Agent({ keepAlive: true, maxSockets: limits.maxConcurrent, maxFreeSockets: 4 });
  const modelServer = http.createServer({ maxHeaderSize: 16384 }, async (req, res) => {
    res.once('finish', () => log({ endpoint: 'model', status: res.statusCode }));
    if (!modelRoute(req)) return status(res, 404);
    const release = acquire();
    if (!release) return status(res, 503);
    res.once('close', release);
    req.pause();
    try {
      const token = await session.getToken();
      if (res.destroyed || req.destroyed) return;
      forwardModel(req, res, { ...data, agent, headers: { authorization: `Bearer ${token}` } },
        limits, code => { if (code === 401 || code === 403) session.invalidate(token); });
      req.resume();
    } catch { status(res, 502); }
  });
  modelServer.on('connect', (_req, socket) => socketStatus(socket, 405));
  const proxyServer = http.createServer({ maxHeaderSize: 16384 }, (_req, res) => status(res, 405));
  proxyServer.on('connect', async (req, socket, head) => {
    let target;
    try { target = authority(req.url); }
    catch { return socketStatus(socket, 400); }
    const release = acquire();
    if (!release) return socketStatus(socket, 503);
    socket.once('close', release);
    socket.on('error', () => {});
    const cancellation = new AbortController();
    socket.once('close', () => cancellation.abort());
    let token;
    try {
      token = await session.getToken();
      if (socket.destroyed) return;
      const upstream = await connectTunnel(target,
        { ...data, signal: cancellation.signal,
          headers: { 'proxy-authorization': `Bearer ${token}` } }, limits);
      log({ endpoint: 'connect', status: 200 });
      pipeTunnel(socket, head, upstream.socket, upstream.head, limits);
    } catch (error) {
      if (token) session.invalidate(token);
      const code = connectFailureStatus(error);
      log({ endpoint: 'connect', status: code });
      socketStatus(socket, code);
    }
  });
  const readyServer = http.createServer((_req, res) => {
    status(res, _req.method === 'GET' && _req.url === '/readyz' ? 204 : 404);
  });
  const brokerServer = config.brokerEnabled ? http.createServer(async (req, res) => {
    if (!agencyRoute(req)) return status(res, 404);
    const release = acquireBroker();
    if (!release) return status(res, 503);
    res.once('close', release);
    req.pause();
    try {
      const token = await session.getToken();
      req.resume();
      await forwardAgency(req, res, {...data, headers: {authorization: `Bearer ${token}`}});
    } catch { status(res, 502); }
  }) : null;
  const servers = [modelServer, proxyServer, readyServer, ...(brokerServer ? [brokerServer] : [])];
  for (const server of servers) hardenServer(server, {...limits, requestTimeoutMs: 210000});
  return { modelServer, proxyServer, readyServer, brokerServer, session,
    async listen() {
      const host = config.listenHost || '127.0.0.1';
      // Listening is purely local: no DNS, session fetch, or egress prerequisite.
      await Promise.all([
        listen(modelServer, config.modelPort ?? 14141, host),
        listen(proxyServer, config.proxyPort ?? 15480, host),
        listen(readyServer, config.readyPort ?? 8081, host),
        ...(brokerServer ? [listen(brokerServer, config.brokerPort ?? 15442, host)] : [])
      ]);
    },
    async close() {
      agent.destroy();
      await Promise.all(servers.map(closeServer));
    }
  };
}

function loadAdapterConfig(env = process.env) {
  const sessionUrl = env.SESSION_URL || env.GATEWAY_SESSION_URL;
  if (!sessionUrl || !env.DATA_URL || !env.EGRESS_CA_FILE) {
    throw new Error('SESSION_URL, DATA_URL and EGRESS_CA_FILE required');
  }
  const systemRoots = require('node:tls').getCACertificates('system');
  return { sessionUrl, dataUrl: env.DATA_URL,
    ca: [...systemRoots, fs.readFileSync(env.GATEWAY_CA_FILE || '/opt/proxy/gateway-ca.crt'),
      fs.readFileSync(env.EGRESS_CA_FILE)],
    sessionServername: env.SESSION_TLS_SERVERNAME, dataServername: env.DATA_TLS_SERVERNAME,
    modelPort: Number(env.MODEL_PORT || 14141), proxyPort: Number(env.PROXY_PORT || 15480),
    readyPort: Number(env.READY_PORT || 8081),
    brokerEnabled: env.AGENCY_BROKER_ENABLED === 'true',
    log: record => process.stdout.write(`${JSON.stringify(record)}\n`) };
}

function startAdapter() {
  let adapter;
  try {
    adapter = createAdapter(loadAdapterConfig());
    adapter.listen().catch(() => { console.error('adapter startup failed'); process.exit(1); });
  } catch { console.error('adapter configuration invalid'); process.exit(1); }
  for (const signal of ['SIGTERM', 'SIGINT']) {
    process.on(signal, () => adapter.close().then(() => process.exit(0)));
  }
}

if (require.main === module) startAdapter();

module.exports = { createAdapter, createSessionClient, loadAdapterConfig, endpointOptions, startAdapter };

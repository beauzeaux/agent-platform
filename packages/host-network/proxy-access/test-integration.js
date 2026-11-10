'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const http = require('node:http');
const https = require('node:https');
const net = require('node:net');
const tls = require('node:tls');
const { once } = require('node:events');
const { key, cert } = require('./test-fixtures.cjs');
const runtime = process.env.PROXY_RUNTIME_DIR || '.';
const { createGateway, listen, closeServer } = require(`${runtime}/gateway`);
const { createAdapter, createSessionClient } = require(`${runtime}/client`);
const { hardenServer } = require(`${process.env.PROXY_RUNTIME_DIR || './chart/files'}/transport`);

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const port = server => server.address().port;

function request(url, options = {}, body) {
  return new Promise((resolve, reject) => {
    const req = (url.startsWith('https:') ? https : http).request(url,
      { ca: cert, servername: 'localhost', agent: false, ...options }, res => {
        const chunks = [];
        res.on('data', chunk => chunks.push(chunk));
        res.once('error', reject);
        res.once('end', () => resolve({ status: res.statusCode, headers: res.headers,
          body: Buffer.concat(chunks).toString('utf8') }));
      });
    req.once('error', reject);
    req.end(body);
  });
}

function connectStatus(server, target, authorization, secure = true) {
  return new Promise((resolve, reject) => {
    const req = (secure ? https : http).request({ hostname: '127.0.0.1', port: port(server),
      servername: 'localhost', ca: cert, agent: false, method: 'CONNECT', path: target,
      headers: authorization ? { 'proxy-authorization': authorization } : {} });
    req.once('connect', (res, socket) => { socket.destroy(); resolve(res.statusCode); });
    req.once('response', res => { res.resume(); resolve(res.statusCode); });
    req.once('error', reject);
    req.end();
  });
}

async function fixture(t, { modelHandler, connectHandler, limits, agencyPilot = false } = {}) {
  const signing = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
  const auth = { issuer: 'https://idp.ate-system.svc',
    audience: 'https://agent-proxies.proxy-access.svc/session', allowedAtespaces: ['proxy-access'],
    jwks: { keys: [{ ...signing.publicKey.export({ format: 'jwk' }), kid: 'test', alg: 'RS256' }] } };
  const actorClaims = { iss: auth.issuer, aud: auth.audience, sub: 'actor/proxy-access/build',
    'actor.ate.dev': { atespace: 'proxy-access', actorName: 'build', actorUID: 'fixture-uid' },
    exp: Math.floor(Date.now() / 1000) + 900 };
  if (agencyPilot) {
    actorClaims.sub = 'actor/proxy-access/engsys-agency-coder';
    actorClaims['actor.ate.dev'].actorName = 'engsys-agency-coder';
  }
  const parts = [{ alg: 'RS256', kid: 'test' }, actorClaims].map(value =>
    Buffer.from(JSON.stringify(value)).toString('base64url'));
  const actorJWT = `${parts.join('.')}.${crypto.sign('sha256', Buffer.from(parts.join('.')),
    signing.privateKey).toString('base64url')}`;
  const state = { sessions: 0, models: [], connects: [], logs: [] };
  const bridge = https.createServer({ key, cert, ca: cert, requestCert: true, rejectUnauthorized: true },
    (req, res) => {
      state.models.push({ headers: req.headers, authorized: req.socket.authorized });
      if (modelHandler) return modelHandler(req, res, state);
      const chunks = [];
      req.on('data', chunk => chunks.push(chunk));
      req.on('end', () => {
        state.models.at(-1).body = Buffer.concat(chunks).toString('utf8');
        res.writeHead(200, { 'content-type': 'application/json', 'x-upstream-secret': 'must-be-stripped' });
        res.end(JSON.stringify({ data: [{ id: 'fixture-model' }] }));
      });
    });
  bridge.on('connect', (req, socket, head) => {
    state.connects.push({ target: req.url, headers: req.headers, authorized: req.socket.authorized });
    socket.on('error', () => {});
    if (connectHandler) return connectHandler(req, socket, head, state);
    socket.write('HTTP/1.1 200 Connection Established\r\n\r\nbridge-head');
    if (head.length) socket.write(head);
    socket.on('data', chunk => socket.write(chunk));
  });
  bridge.on('tlsClientError', () => {});
  hardenServer(bridge);
  await listen(bridge, 0, '127.0.0.1');
  const gatewayConfig = { tls: { key, cert }, auth, sessionPort: 0, dataPort: 0,
    listenHost: '127.0.0.1', limits, bridge: { host: '127.0.0.1', servername: 'localhost',
      modelPort: port(bridge), connectPort: port(bridge), key, cert, ca: cert },
    log: record => state.logs.push(record) };
  gatewayConfig.bridge.brokerPort = port(bridge);
  if (agencyPilot) gatewayConfig.broker = {actorUID: 'fixture-uid'};
  const gateway = createGateway(gatewayConfig);
  await gateway.listen();
  const sessionUrl = `https://127.0.0.1:${port(gateway.sessionServer)}/session`;
  const dataUrl = `https://127.0.0.1:${port(gateway.dataServer)}`;
  const egress = https.createServer({ key, cert }, (req, res) => {
    state.sessions++;
    assert.equal(req.headers.authorization, 'Bearer egress-injected');
    const upstream = https.request(sessionUrl, { ca: cert, servername: 'localhost', agent: false,
      method: 'POST', headers: { authorization: `Bearer ${actorJWT}`, 'content-length': '0' } },
    incoming => {
      res.writeHead(incoming.statusCode, incoming.headers);
      incoming.pipe(res);
    });
    upstream.on('error', () => { res.writeHead(502); res.end(); });
    upstream.end();
  });
  hardenServer(egress);
  await listen(egress, 0, '127.0.0.1');
  const adapterConfig = { sessionUrl: `https://127.0.0.1:${port(egress)}/session`,
    dataUrl, ca: cert, sessionServername: 'localhost', dataServername: 'localhost',
    modelPort: 0, proxyPort: 0, readyPort: 0, limits, log: record => state.logs.push(record) };
  if (agencyPilot) { adapterConfig.brokerEnabled = true; adapterConfig.brokerPort = 0; }
  const adapter = createAdapter(adapterConfig);
  await adapter.listen();
  t.after(async () => {
    await adapter.close();
    await closeServer(egress);
    await gateway.close();
    await closeServer(bridge);
  });
  return { state, bridge, gateway, gatewayConfig, adapter, adapterConfig,
    sessionUrl, dataUrl, actorJWT,
    modelUrl: `http://127.0.0.1:${port(adapter.modelServer)}` };
}

test('Agency routes use explicit pilot leases, derived identity and an authenticated fixed bridge',
  {timeout: 10000}, async t => {
    const f = await fixture(t, {agencyPilot: true, modelHandler: (req, res, state) => {
      const chunks = [];
      req.on('data', chunk => chunks.push(chunk));
      req.on('end', () => {
        state.models.at(-1).body = Buffer.concat(chunks).toString();
        res.writeHead(200, {'content-type': 'application/json', 'mcp-session-id': 'bound-session'});
        res.end('{"jsonrpc":"2.0","id":1,"result":{}}');
      });
    }});
    const result = await request(`http://127.0.0.1:${port(f.adapter.brokerServer)}/agency/mcp`, {
      method: 'POST', headers: {'x-agency-actor': 'forged', 'x-agency-actor-uid': 'forged',
        'mcp-session-id': 'prior-session'},
    }, '{"jsonrpc":"2.0","id":1,"method":"initialize"}');
    assert.equal(result.status, 200);
    assert.equal(result.headers['mcp-session-id'], 'bound-session');
    assert.equal(f.state.models.length, 1);
    const forwarded = f.state.models[0];
    assert.equal(forwarded.authorized, true);
    assert.equal(forwarded.headers['x-agency-actor'], 'actor/proxy-access/engsys-agency-coder');
    assert.equal(forwarded.headers['x-agency-actor-uid'], 'fixture-uid');
    assert.equal(forwarded.headers['mcp-session-id'], 'prior-session');
    assert.equal(forwarded.headers.authorization, undefined);
    assert.equal(forwarded.body, '{"jsonrpc":"2.0","id":1,"method":"initialize"}');
  });

test('existing actors cannot acquire broker capabilities with valid model leases or spoofed headers',
  {timeout: 10000}, async t => {
    const f = await fixture(t);
    assert.equal(f.adapter.brokerServer, null);
    const session = await request(f.sessionUrl, {method: 'POST',
      headers: {authorization: `Bearer ${f.actorJWT}`}});
    const lease = JSON.parse(session.body).access_token;
    const denied = await request(f.dataUrl + '/agency/plugins', {method: 'POST',
      headers: {authorization: `Bearer ${lease}`, 'x-agency-actor-uid': 'fixture-uid'}}, '{}');
    assert.equal(denied.status, 403);
    assert.equal(f.state.models.length, 0);
  });

test('gateway session/auth and token-free health are minimal; model secrets/headers are not forwarded',
  { timeout: 10000 }, async t => {
    const f = await fixture(t);
    assert.equal((await request(f.dataUrl + '/health')).status, 204);
    assert.equal((await request(f.sessionUrl.replace('/session', '/health'))).body, '');
    assert.equal((await request(f.sessionUrl, { method: 'POST',
      headers: { authorization: 'Bearer egress-injected' } })).status, 401);
    assert.equal((await request(f.dataUrl + '/v1/models')).status, 401);
    const session = await request(f.sessionUrl, { method: 'POST',
      headers: { authorization: `Bearer ${f.actorJWT}` } });
    assert.equal(session.status, 200);
    assert.equal(session.headers['cache-control'], 'no-store');
    const token = JSON.parse(session.body).access_token;
    const result = await request(f.dataUrl + '/v1/models', { headers: {
      authorization: `Bearer ${token}`, 'proxy-authorization': 'Bearer do-not-forward',
      'x-client-secret': 'do-not-forward', cookie: 'do-not-forward'
    } });
    assert.equal(result.status, 200);
    assert.equal(JSON.parse(result.body).data[0].id, 'fixture-model');
    assert.equal(result.headers['x-upstream-secret'], undefined);
    assert.equal(f.state.models[0].authorized, true);
    for (const name of ['authorization', 'proxy-authorization', 'x-client-secret', 'cookie']) {
      assert.equal(f.state.models[0].headers[name], undefined);
    }
    assert.equal((await request(f.dataUrl + '/v1/other', { headers: {
      authorization: `Bearer ${token}` } })).status, 404);
    assert.equal(f.state.models.length, 1);
    assert.ok(f.state.logs.every(record => Object.keys(record).sort().join() === 'endpoint,status'));
    assert.ok(!JSON.stringify(f.state.logs).includes(token));
    assert.ok(!JSON.stringify(f.state.logs).includes(f.actorJWT));
  });

test('adapter readiness is network-independent and lazy sessions coalesce, expire and cold-boot in RAM',
  { timeout: 10000 }, async t => {
    const f = await fixture(t);
    assert.equal(f.state.sessions, 0);
    const readyUrl = `http://127.0.0.1:${port(f.adapter.readyServer)}/readyz`;
    assert.equal((await request(readyUrl)).status, 204);
    assert.equal(f.state.sessions, 0);
    let clock = Date.now() / 1000;
    const client = createSessionClient({ ...f.adapterConfig, clock: () => clock });
    const tokens = await Promise.all(Array.from({ length: 8 }, () => client.getToken()));
    assert.equal(new Set(tokens).size, 1);
    assert.equal(f.state.sessions, 1);
    assert.equal(await client.getToken(), tokens[0]);
    clock += 301;
    assert.notEqual(await client.getToken(), tokens[0]);
    assert.equal(f.state.sessions, 2);
    const cold = createSessionClient(f.adapterConfig);
    assert.notEqual(await cold.getToken(), tokens[0]);
    assert.equal(f.state.sessions, 3);
  });

test('local model forwarding streams request/response incrementally without replay',
  { timeout: 10000 }, async t => {
    let received = '';
    let ended = false;
    const f = await fixture(t, { modelHandler(req, res) {
      req.on('data', chunk => { received += chunk; });
      req.on('end', () => {
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        res.write('data: first\n\n');
        setTimeout(() => { ended = true; res.end('data: final\n\n'); }, 100);
      });
    } });
    const response = await new Promise((resolve, reject) => {
      const req = http.request(f.modelUrl + '/v1/responses', { method: 'POST',
        headers: { authorization: 'Bearer client-secret', 'x-client-secret': 'secret' } }, res => {
        let body = '';
        res.on('data', chunk => {
          if (!body) assert.equal(ended, false, 'first chunk arrives before upstream finishes');
          body += chunk;
        });
        res.once('end', () => resolve({ status: res.statusCode, body }));
        res.once('error', reject);
      });
      req.once('error', reject);
      req.write('{"model":"fixture-model",');
      setTimeout(() => req.end('"input":"fixture"}'), 20);
    });
    assert.equal(response.status, 200);
    assert.equal(response.body, 'data: first\n\ndata: final\n\n');
    assert.equal(received, '{"model":"fixture-model","input":"fixture"}');
    assert.equal(f.state.models.length, 1);
    assert.equal(f.state.sessions, 1);
    assert.equal(f.state.models[0].headers.authorization, undefined);
  });

test('client cancellation closes the one upstream streaming request without replay',
  { timeout: 10000 }, async t => {
    let cancelled;
    const cancellation = new Promise(resolve => { cancelled = resolve; });
    const f = await fixture(t, { modelHandler(req, res) {
      req.resume();
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      const timer = setInterval(() => res.write('data: fixture\n\n'), 10);
      res.once('close', () => { clearInterval(timer); cancelled(); });
    } });
    await new Promise((resolve, reject) => {
      const req = http.get(f.modelUrl + '/v1/models', res => {
        res.once('data', () => { res.destroy(); req.destroy(); resolve(); });
        res.on('error', () => {});
      });
      req.once('error', reject);
    });
    await cancellation;
    await pause(50);
    assert.equal(f.state.models.length, 1);
    assert.equal(f.state.sessions, 1);
  });

test('explicit CONNECT tunnels preserve both header-head byte directions and continuing data',
  { timeout: 10000 }, async t => {
    const f = await fixture(t);
    await new Promise((resolve, reject) => {
      const socket = net.connect(port(f.adapter.proxyServer), '127.0.0.1');
      let body = '';
      let sentLate = false;
      socket.once('error', reject);
      socket.once('connect', () => socket.write(
        'CONNECT dev.azure.com:443 HTTP/1.1\r\nHost: dev.azure.com:443\r\n\r\nearly-body'));
      socket.on('data', chunk => {
        body += chunk;
        if (!sentLate && body.includes('\r\n\r\n')) {
          assert.match(body, /^HTTP\/1.1 200/);
          sentLate = true;
          socket.write('late-body');
        }
        if (body.includes('bridge-head') && body.includes('early-body') && body.includes('late-body')) {
          socket.destroy();
          resolve();
        }
      });
    });
    assert.equal(f.state.connects.length, 1);
    assert.equal(f.state.connects[0].target, 'dev.azure.com:443');
    assert.equal(f.state.connects[0].authorized, true);
    assert.equal(f.state.connects[0].headers['proxy-authorization'], undefined);
    assert.equal(f.state.connects[0].headers.authorization, undefined);
    assert.equal(f.state.sessions, 1);
  });

test('malformed/non443/IP CONNECT and unauthenticated tunnels never dial the bridge',
  { timeout: 10000 }, async t => {
    const f = await fixture(t);
    for (const target of ['127.0.0.1:443', 'dev.azure.com:80', 'bad..example:443']) {
      assert.equal(await connectStatus(f.gateway.dataServer, target, 'Bearer invalid'), 400);
      assert.equal(await connectStatus(f.adapter.proxyServer, target, undefined, false), 400);
    }
    assert.equal(await connectStatus(f.gateway.dataServer, 'dev.azure.com:443'), 401);
    assert.equal((await request(`http://127.0.0.1:${port(f.adapter.proxyServer)}/`)).status, 405);
    assert.equal(f.state.connects.length, 0);
    assert.equal(f.state.sessions, 0);
  });

test('model and CONNECT reject wrong bridge root, wrong servername and missing mTLS client identity',
  { timeout: 15000 }, async t => {
    const f = await fixture(t);
    for (const bad of [{ ca: tls.rootCertificates[0] }, { servername: 'wrong.example' },
      { cert: undefined, key: undefined }]) {
      const gateway = createGateway({ ...f.gatewayConfig,
        bridge: { ...f.gatewayConfig.bridge, ...bad } });
      await gateway.listen();
      try {
        const token = gateway.tokens.issue({ sub: 'actor/proxy-access/build', atespace: 'proxy-access' }).access_token;
        assert.equal((await request(`https://127.0.0.1:${port(gateway.dataServer)}/v1/models`,
          { headers: { authorization: `Bearer ${token}` } })).status, 502);
        assert.equal(await connectStatus(gateway.dataServer, 'dev.azure.com:443', `Bearer ${token}`), 502);
      } finally { await gateway.close(); }
    }
    assert.equal(f.state.models.length, 0);
    assert.equal(f.state.connects.length, 0);
  });

test('data authentication failure invalidates only the next request; POST is never replayed',
  { timeout: 10000 }, async t => {
    const f = await fixture(t);
    const bodies = [];
    const refusal = https.createServer({ key, cert }, (req, res) => {
      const chunks = [];
      req.on('data', chunk => chunks.push(chunk));
      req.once('end', () => { bodies.push(Buffer.concat(chunks).toString()); res.writeHead(401); res.end(); });
    });
    hardenServer(refusal);
    await listen(refusal, 0, '127.0.0.1');
    const adapter = createAdapter({ ...f.adapterConfig, dataUrl: `https://127.0.0.1:${port(refusal)}` });
    await adapter.listen();
    t.after(async () => { await adapter.close(); await closeServer(refusal); });
    const url = `http://127.0.0.1:${port(adapter.modelServer)}/v1/responses`;
    assert.equal((await request(url, { method: 'POST' }, 'first-body')).status, 401);
    assert.equal(f.state.sessions, 1);
    assert.deepEqual(bodies, ['first-body']);
    assert.equal((await request(url, { method: 'POST' }, 'second-body')).status, 401);
    assert.equal(f.state.sessions, 2);
    assert.deepEqual(bodies, ['first-body', 'second-body']);
  });

test('bounded model body and concurrent request budget prevent excess bridge work',
  { timeout: 10000 }, async t => {
    const f = await fixture(t, { limits: { maxBodyBytes: 32, maxConcurrent: 1 }, modelHandler(req, res) {
      req.resume();
      res.writeHead(200);
      res.write('waiting');
    } });
    const token = f.gateway.tokens.issue({ sub: 'actor/proxy-access/build', atespace: 'proxy-access' }).access_token;
    const headers = { authorization: `Bearer ${token}` };
    assert.equal((await request(f.dataUrl + '/v1/responses', { method: 'POST',
      headers: { ...headers, 'content-length': '33' } }, 'x'.repeat(33))).status, 413);
    assert.equal(f.state.models.length, 0);
    const req = https.get(f.dataUrl + '/v1/models',
      { ca: cert, servername: 'localhost', agent: false, headers });
    req.on('error', () => {});
    const [res] = await once(req, 'response');
    await once(res, 'data');
    assert.equal((await request(f.dataUrl + '/v1/models', { headers })).status, 503);
    assert.equal(f.state.models.length, 1);
    res.destroy();
    req.destroy();
  });

test('CONNECT handshake deadline fails closed, without a retry or TCP fallback',
  { timeout: 10000 }, async t => {
    const f = await fixture(t, { limits: { requestTimeoutMs: 100 }, connectHandler() {} });
    const token = f.gateway.tokens.issue({ sub: 'actor/proxy-access/build', atespace: 'proxy-access' }).access_token;
    assert.equal(await connectStatus(f.gateway.dataServer, 'dev.azure.com:443', `Bearer ${token}`), 502);
    assert.equal(f.state.connects.length, 1);
  });

for (const upstreamStatus of [403, 407, 429, 503, 302]) {
  const expected = upstreamStatus === 302 ? 502 : upstreamStatus;
  test(`CONNECT bridge rejection ${upstreamStatus} reaches local proxy as ${expected}, without secrets or retry`,
    { timeout: 10000 }, async t => {
      const f = await fixture(t, { connectHandler(_req, socket, head, state) {
        state.deniedBytes = head.length;
        socket.on('data', chunk => { state.deniedBytes += chunk.length; });
        const body = 'upstream-credentials-must-not-leak';
        socket.end(`HTTP/1.1 ${upstreamStatus} Sensitive upstream reason\r\n` +
          'Proxy-Authenticate: Basic realm="sensitive"\r\n' +
          'X-Upstream-Secret: sensitive\r\nSet-Cookie: sensitive\r\n' +
          `Content-Length: ${Buffer.byteLength(body)}\r\n\r\n${body}`);
      } });
      const wire = await new Promise((resolve, reject) => {
        const socket = net.connect(port(f.adapter.proxyServer), '127.0.0.1');
        let response = '';
        socket.once('error', reject);
        socket.once('connect', () => socket.write(
          'CONNECT registry.npmjs.org:443 HTTP/1.1\r\nHost: registry.npmjs.org:443\r\n\r\nnever-forward'));
        socket.on('data', chunk => { response += chunk; });
        socket.once('end', () => resolve(response));
      });
      assert.equal(wire,
        `HTTP/1.1 ${expected} Error\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
      await pause(20);
      assert.equal(f.state.connects.length, 1, 'only the mTLS bridge CONNECT is attempted');
      assert.equal(f.state.connects[0].target, 'registry.npmjs.org:443');
      assert.equal(f.state.models.length, 0);
      assert.equal(f.state.sessions, 1);
      assert.equal(f.state.deniedBytes, 0, 'client head never crosses a refused tunnel');
      assert.ok(!JSON.stringify(f.state.logs).includes('sensitive'));
    });
}

test('aborted streaming uploads cancel upstream; aborted session bodies do not crash or issue tokens',
  { timeout: 10000 }, async t => {
    let closed;
    let started;
    const cancellation = new Promise(resolve => { closed = resolve; });
    const receiving = new Promise(resolve => { started = resolve; });
    const f = await fixture(t, { modelHandler(req, _res) {
      req.on('data', started);
      req.once('close', closed);
    } });
    const upload = http.request(f.modelUrl + '/v1/responses', { method: 'POST' });
    upload.on('error', () => {});
    upload.write('{"incomplete":');
    await receiving;
    upload.destroy();
    await cancellation;
    assert.equal(f.state.models.length, 1);
    const session = https.request(f.sessionUrl, { ca: cert, servername: 'localhost', agent: false,
      method: 'POST', headers: { authorization: `Bearer ${f.actorJWT}` } });
    session.on('error', () => {});
    session.write('incomplete');
    await pause(20);
    session.destroy();
    await pause(20);
    assert.equal((await request(f.sessionUrl.replace('/session', '/health'))).status, 204);
    assert.equal((await request(f.dataUrl + '/v1/models')).status, 401);
  });

test('chunked upload body limit closes excess data without replay',
  { timeout: 10000 }, async t => {
    const f = await fixture(t, { limits: { maxBodyBytes: 32 }, modelHandler(req, _res) {
      req.resume();
    } });
    await new Promise(resolve => {
      const req = http.request(f.modelUrl + '/v1/responses', { method: 'POST' }, res => {
        assert.equal(res.statusCode, 413);
        res.resume();
        res.once('end', resolve);
      });
      req.on('error', resolve);
      req.write('x'.repeat(16));
      setTimeout(() => req.end('x'.repeat(32)), 30);
    });
    await pause(30);
    assert.ok(f.state.models.length <= 1);
    assert.equal(f.state.sessions, 1);
  });

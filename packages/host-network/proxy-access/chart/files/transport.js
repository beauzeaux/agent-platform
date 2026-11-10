'use strict';

const https = require('node:https');
const net = require('node:net');
const { Transform, pipeline } = require('node:stream');
const serverSockets = new WeakMap();

const DEFAULT_LIMITS = Object.freeze({
  maxConnections: 128, maxConcurrent: 32, maxBodyBytes: 4 * 1024 * 1024,
  headersTimeoutMs: 15000, requestTimeoutMs: 30000, idleTimeoutMs: 60000,
  maxDurationMs: 600000
});

function authority(value) {
  if (typeof value !== 'string' || value.length > 260) throw new Error('invalid authority');
  const match = /^([A-Za-z0-9.-]+):443$/.exec(value);
  if (!match || net.isIP(match[1]) || !match[1].includes('.') ||
      /^\d+$/.test(match[1].split('.').at(-1)) ||
      !match[1].split('.').every(label =>
        label.length <= 63 && /^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$/.test(label))) {
    throw new Error('only DNS HTTPS:443 authorities are permitted');
  }
  return `${match[1].toLowerCase()}:443`;
}

function status(res, code) {
  if (!res.headersSent && !res.destroyed) {
    res.writeHead(code, { 'content-length': '0', 'cache-control': 'no-store' });
    res.end();
  } else if (!res.writableEnded) res.destroy();
}

function socketStatus(socket, code) {
  if (!socket.destroyed) socket.end(`HTTP/1.1 ${code} Error\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
}

function modelRoute(req) {
  return (req.method === 'GET' && req.url === '/v1/models') ||
    (req.method === 'POST' && req.url === '/v1/responses');
}

function bodyAllowed(req, limits) {
  const length = req.headers['content-length'];
  return length === undefined || (/^\d+$/.test(length) && Number(length) <= limits.maxBodyBytes);
}

function forwardModel(req, res, options, limits = DEFAULT_LIMITS, onStatus = () => {}) {
  if (!bodyAllowed(req, limits)) return status(res, 413);
  let size = 0;
  let response;
  const upstream = https.request({ ...options, method: req.method, path: req.url,
    headers: { ...(req.method === 'POST' ? { 'content-type': 'application/json' } : {}),
      ...options.headers } });
  const deadline = setTimeout(() => upstream.destroy(new Error('deadline')), limits.maxDurationMs);
  deadline.unref();
  upstream.setTimeout(limits.idleTimeoutMs, () => upstream.destroy(new Error('idle')));
  const limiter = new Transform({
    transform(chunk, encoding, callback) {
      size += chunk.length;
      callback(size > limits.maxBodyBytes ? new Error('body limit') : null, chunk);
    }
  });
  const cancel = () => {
    if (!res.writableFinished) upstream.destroy(new Error('cancelled'));
  };
  res.once('close', cancel);
  req.once('aborted', cancel);
  upstream.once('response', incoming => {
    response = incoming;
    onStatus(incoming.statusCode);
    const headers = { 'cache-control': 'no-store' };
    if (typeof incoming.headers['content-type'] === 'string') {
      headers['content-type'] = incoming.headers['content-type'];
    }
    res.writeHead(incoming.statusCode, headers);
    pipeline(incoming, res, () => {});
  });
  upstream.once('error', () => {
    onStatus(502);
    status(res, size > limits.maxBodyBytes ? 413 : 502);
  });
  upstream.once('close', () => {
    clearTimeout(deadline);
    if (response && !response.complete) response.destroy();
  });
  pipeline(req, limiter, upstream, error => {
    if (error) status(res, size > limits.maxBodyBytes ? 413 : 502);
  });
  return upstream;
}

class ConnectRejection extends Error {
  constructor(statusCode) {
    super('CONNECT refused');
    this.statusCode = [403, 407, 429, 503].includes(statusCode) ? statusCode : 502;
  }
}

function connectFailureStatus(error) {
  return error instanceof ConnectRejection ? error.statusCode : 502;
}

function connectTunnel(target, options, limits = DEFAULT_LIMITS) {
  target = authority(target);
  return new Promise((resolve, reject) => {
    const request = https.request({ ...options, agent: false, method: 'CONNECT', path: target,
      headers: { host: target, ...options.headers }, maxHeaderSize: 16384 });
    const timer = setTimeout(() => request.destroy(new Error('CONNECT timeout')), limits.requestTimeoutMs);
    timer.unref();
    request.once('connect', (response, socket, head) => {
      clearTimeout(timer);
      if (response.statusCode !== 200) {
        socket.destroy();
        reject(new ConnectRejection(response.statusCode));
      } else {
        resolve({ socket, head });
      }
    });
    request.once('response', response => {
      clearTimeout(timer);
      response.destroy();
      request.destroy();
      reject(new ConnectRejection(response.statusCode));
    });
    request.once('error', error => { clearTimeout(timer); reject(error); });
    request.end();
  });
}

function pipeTunnel(client, clientHead, upstream, upstreamHead, limits = DEFAULT_LIMITS) {
  if (client.destroyed) { upstream.destroy(); return; }
  const deadline = setTimeout(() => { client.destroy(); upstream.destroy(); }, limits.maxDurationMs);
  deadline.unref();
  const finish = () => { clearTimeout(deadline); client.destroy(); upstream.destroy(); };
  client.once('error', finish);
  upstream.once('error', finish);
  client.once('close', finish);
  upstream.once('close', finish);
  client.setTimeout(limits.idleTimeoutMs, finish);
  upstream.setTimeout(limits.idleTimeoutMs, finish);
  client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
  if (upstreamHead.length) client.write(upstreamHead);
  if (clientHead.length) upstream.write(clientHead);
  client.pipe(upstream);
  upstream.pipe(client);
}

function hardenServer(server, limits = DEFAULT_LIMITS) {
  const sockets = new Set();
  serverSockets.set(server, sockets);
  server.on('connection', socket => {
    sockets.add(socket);
    socket.once('close', () => sockets.delete(socket));
  });
  server.prependListener('request', (req, res) => {
    req.on('error', () => {});
    res.on('error', () => {});
  });
  server.maxConnections = limits.maxConnections;
  server.headersTimeout = limits.headersTimeoutMs;
  server.requestTimeout = limits.requestTimeoutMs;
  server.keepAliveTimeout = 5000;
  server.maxRequestsPerSocket = 100;
  server.on('clientError', (_error, socket) => socketStatus(socket, 400));
  server.on('tlsClientError', () => {});
}

function destroyServerConnections(server) {
  for (const socket of serverSockets.get(server) || []) socket.destroy();
}

function createBudget(limit) {
  let count = 0;
  return () => {
    if (count >= limit) return null;
    count++;
    let released = false;
    return () => { if (!released) { released = true; count--; } };
  };
}

module.exports = { DEFAULT_LIMITS, authority, status, socketStatus, modelRoute, bodyAllowed,
  forwardModel, connectTunnel, connectFailureStatus, pipeTunnel, hardenServer, createBudget,
  destroyServerConnections };

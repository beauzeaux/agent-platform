'use strict';
const https = require('node:https');
const fs = require('node:fs');
const {status} = require('./transport');

function agencyRoute(req) {
  return (req.method === 'POST' && ['/agency/plugins', '/agency/mcp', '/agency/policy'].includes(req.url)) ||
    (req.method === 'DELETE' && req.url === '/agency/mcp');
}

function configuredBrokerActor(actor, config) {
  let dynamic = {};
  if (config?.dynamicActorsFile) {
    try { dynamic = JSON.parse(fs.readFileSync(config.dynamicActorsFile, 'utf8')).actors || {}; }
    catch { return false; }
  }
  return dynamic[actor.actorUID]?.subject === actor.sub ||
    config?.actors?.[actor.actorUID]?.subject === actor.sub ||
    (!!config?.actorUID && actor.sub === 'actor/proxy-access/engsys-agency-coder' && actor.actorUID === config.actorUID);
}

function brokerAllowed(actor, config) {
  return configuredBrokerActor(actor, config) && actor.capabilities?.includes('agency') === true;
}

async function forwardAgency(req, res, options) {
  const limit = 8000000;
  try {
    const chunks = [];
    let bytes = 0;
    for await (const chunk of req) {
      bytes += chunk.length;
      if (bytes > limit) return status(res, 413);
      chunks.push(chunk);
    }
    if (res.destroyed) return;
    const body = Buffer.concat(chunks);
    const upstream = https.request({...options, method: req.method, path: req.url,
      agent: false, headers: {'content-type': 'application/json',
        'content-length': body.length, ...options.headers,
        ...(req.headers['mcp-session-id'] ? {'mcp-session-id': req.headers['mcp-session-id']} : {})}},
    incoming => {
      let size = 0;
      const output = [];
      incoming.on('data', chunk => {
        size += chunk.length;
        if (size > limit) upstream.destroy(new Error('response_limit'));
        else output.push(chunk);
      });
      incoming.once('end', () => {
        if (res.destroyed) return;
        const headers = {'content-type': 'application/json', 'cache-control': 'no-store'};
        if (incoming.headers['mcp-session-id']) headers['mcp-session-id'] = incoming.headers['mcp-session-id'];
        res.writeHead(incoming.statusCode, headers);
        res.end(Buffer.concat(output));
      });
      incoming.once('error', () => status(res, 502));
    });
    const timer = setTimeout(() => upstream.destroy(new Error('agency_timeout')), 200000);
    timer.unref();
    upstream.once('close', () => clearTimeout(timer));
    upstream.once('error', () => status(res, 502));
    res.once('close', () => { if (!res.writableFinished) upstream.destroy(); });
    upstream.end(body);
  } catch { status(res, 502); }
}

module.exports = {agencyRoute, configuredBrokerActor, brokerAllowed, forwardAgency};

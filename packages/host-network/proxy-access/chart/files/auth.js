'use strict';

const crypto = require('node:crypto');

function decode(value) {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error('invalid encoding');
  const buffer = Buffer.from(value, 'base64url');
  if (buffer.toString('base64url') !== value) throw new Error('invalid encoding');
  return buffer;
}

function objectPart(value) {
  const result = JSON.parse(decode(value).toString('utf8'));
  if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('invalid object');
  return result;
}

function createActorVerifier(config, clock = () => Date.now() / 1000) {
  if (!config.issuer || !config.audience || !config.allowedAtespaces?.length) {
    throw new Error('issuer, audience and allowedAtespaces are required');
  }
  const keys = config.jwks?.keys;
  if (!Array.isArray(keys) || keys.length === 0) throw new Error('pinned public JWKS required');
  const publicKeys = keys.map(jwk => {
    if (['d', 'k', 'p', 'q', 'dp', 'dq', 'qi', 'oth'].some(name => Object.hasOwn(jwk, name)) ||
        (jwk.use && jwk.use !== 'sig') ||
        (jwk.key_ops && (!Array.isArray(jwk.key_ops) || !jwk.key_ops.includes('verify')))) {
      throw new Error('invalid public signing key');
    }
    const alg = jwk.kty === 'RSA' ? 'RS256' :
      jwk.kty === 'EC' && jwk.crv === 'P-256' ? 'ES256' : null;
    if (!alg || (jwk.alg && jwk.alg !== alg)) throw new Error('unsupported signing key');
    const key = crypto.createPublicKey({ key: jwk, format: 'jwk' });
    if (alg === 'RS256' && key.asymmetricKeyDetails.modulusLength < 2048) {
      throw new Error('RSA key must be at least 2048 bits');
    }
    return { kid: jwk.kid, alg, key };
  });
  return token => {
    if (typeof token !== 'string' || token.length > 16384) throw new Error('invalid JWT');
    const parts = token.split('.');
    if (parts.length !== 3) throw new Error('invalid JWT');
    const header = objectPart(parts[0]);
    if (!['RS256', 'ES256'].includes(header.alg) || header.crit !== undefined ||
        (header.typ !== undefined && header.typ !== 'JWT')) throw new Error('unsupported JWT');
    const candidates = publicKeys.filter(key => key.alg === header.alg &&
      (header.kid === undefined || key.kid === header.kid));
    if (candidates.length !== 1) throw new Error('ambiguous or unknown signing key');
    const signature = decode(parts[2]);
    if (header.alg === 'ES256' && signature.length !== 64) throw new Error('invalid signature');
    if (!crypto.verify('sha256', Buffer.from(`${parts[0]}.${parts[1]}`),
      { key: candidates[0].key, ...(header.alg === 'ES256' ? { dsaEncoding: 'ieee-p1363' } : {}) },
      signature)) throw new Error('invalid signature');
    const claims = objectPart(parts[1]);
    const now = clock();
    if (!Number.isFinite(claims.exp) || claims.exp <= now ||
        (claims.nbf !== undefined && (!Number.isFinite(claims.nbf) || claims.nbf > now)) ||
        (claims.iat !== undefined && (!Number.isFinite(claims.iat) || claims.iat > now)) ||
        claims.iss !== config.issuer ||
        !(claims.aud === config.audience || (Array.isArray(claims.aud) &&
          claims.aud.every(a => typeof a === 'string') && claims.aud.includes(config.audience)))) {
      throw new Error('invalid claims');
    }
    const subject = typeof claims.sub === 'string' && /^actor\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_.-]+)$/.exec(claims.sub);
    if (!subject || !config.allowedAtespaces.includes(subject[1])) throw new Error('invalid actor scope');
    const identity = claims[config.actorClaim || 'actor.ate.dev'];
    if (!identity || typeof identity !== 'object' || Array.isArray(identity) ||
        identity.atespace !== subject[1] || identity.actorName !== subject[2] ||
        typeof identity.actorUID !== 'string' || !identity.actorUID.trim() ||
        identity.actorUID.length > 256 ||
        (claims.atespace !== undefined && claims.atespace !== subject[1])) {
      throw new Error('invalid actor identity claim');
    }
    return { sub: claims.sub, atespace: subject[1], actorUID: identity.actorUID,
      actorExpiresAt: claims.exp };
  };
}

function createTokenStore({ key = crypto.randomBytes(32), ttlSeconds = 300,
  clock = () => Date.now() / 1000 } = {}) {
  if (!Buffer.isBuffer(key) || key.length < 32 || ttlSeconds <= 0 || ttlSeconds > 300) {
    throw new Error('invalid token signing configuration');
  }
  function mac(payload) {
    return crypto.createHmac('sha256', key).update(payload).digest();
  }
  return {
    issue(actor) {
      const now = Math.floor(clock());
      const exp = Math.min(now + ttlSeconds, actor.actorExpiresAt ?? Infinity);
      if (exp <= now) throw new Error('expired actor');
      const payload = Buffer.from(JSON.stringify({ v: 1, sub: actor.sub, atespace: actor.atespace,
        ...(actor.capabilities?.length ? {actorUID: actor.actorUID, capabilities: actor.capabilities} : {}),
        iat: now, exp, jti: crypto.randomBytes(16).toString('base64url') })).toString('base64url');
      return { access_token: `${payload}.${mac(payload).toString('base64url')}`,
        token_type: 'Bearer', expires_in: exp - now, expires_at: exp };
    },
    verify(token) {
      if (typeof token !== 'string' || token.length > 4096) throw new Error('invalid token');
      const parts = token.split('.');
      if (parts.length !== 2) throw new Error('invalid token');
      const signature = decode(parts[1]);
      const expected = mac(parts[0]);
      if (signature.length !== expected.length || !crypto.timingSafeEqual(signature, expected)) {
        throw new Error('invalid token');
      }
      const claims = objectPart(parts[0]);
      const now = clock();
      if (claims.v !== 1 || !Number.isFinite(claims.exp) || claims.exp <= now ||
          !Number.isFinite(claims.iat) || claims.iat > now || claims.exp - claims.iat > 300 ||
          typeof claims.sub !== 'string' || typeof claims.atespace !== 'string') {
        throw new Error('invalid token claims');
      }
      return claims;
    }
  };
}

function bearer(value) {
  if (typeof value !== 'string' || !/^Bearer [A-Za-z0-9_.-]+$/.test(value)) throw new Error('missing bearer');
  return value.slice(7);
}

module.exports = { createActorVerifier, createTokenStore, bearer };

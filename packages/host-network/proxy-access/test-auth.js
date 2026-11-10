'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const runtime = process.env.PROXY_RUNTIME_DIR || './chart/files';
const { createActorVerifier, createTokenStore } = require(`${runtime}/auth`);
const { authority } = require(`${runtime}/transport`);

function signer(alg) {
  const keys = alg === 'RS256' ? crypto.generateKeyPairSync('rsa', { modulusLength: 2048 }) :
    crypto.generateKeyPairSync('ec', { namedCurve: 'P-256' });
  const jwk = { ...keys.publicKey.export({ format: 'jwk' }), alg, kid: alg };
  return { jwk, sign(claims, header = { alg, kid: alg }) {
    const parts = [header, claims].map(part => Buffer.from(JSON.stringify(part)).toString('base64url'));
    const signature = crypto.sign('sha256', Buffer.from(parts.join('.')),
      { key: keys.privateKey, ...(alg === 'ES256' ? { dsaEncoding: 'ieee-p1363' } : {}) });
    return [...parts, signature.toString('base64url')].join('.');
  } };
}

const claims = { iss: 'https://idp.ate-system.svc',
  aud: 'https://agent-proxies.proxy-access.svc/session', sub: 'actor/proxy-access/build',
  atespace: 'proxy-access', 'actor.ate.dev': {
    atespace: 'proxy-access', actorName: 'build', actorUID: 'test-actor-uid'
  }, iat: 990, nbf: 990, exp: 1300 };

for (const alg of ['RS256', 'ES256']) {
  test(`${alg}: signature, issuer, audience, actor scope and time validation`, () => {
    const signing = signer(alg);
    const verify = createActorVerifier({ issuer: claims.iss, audience: claims.aud,
      allowedAtespaces: ['proxy-access'], jwks: { keys: [signing.jwk] } },
    () => 1000);
    assert.equal(verify(signing.sign(claims)).sub, claims.sub);
    assert.equal(verify(signing.sign({ ...claims, aud: ['other', claims.aud] })).atespace, 'proxy-access');
    for (const change of [{ iss: 'wrong' }, { aud: 'wrong' }, { aud: [4, claims.aud] },
      { exp: 1000 }, { exp: '1300' }, { exp: undefined }, { nbf: 1001 }, { iat: 1001 },
      { sub: 'actor/other/build' }, { sub: 'user/proxy-access/build' }, { sub: 'actor/proxy-access/a/b' },
      { atespace: 'other' }, { 'actor.ate.dev': undefined }, { 'actor.ate.dev': null },
      { 'actor.ate.dev': { ...claims['actor.ate.dev'], atespace: 'other' } },
      { 'actor.ate.dev': { ...claims['actor.ate.dev'], actorName: 'other' } },
      { 'actor.ate.dev': { ...claims['actor.ate.dev'], actorUID: '' } },
      { 'actor.ate.dev': { ...claims['actor.ate.dev'], actorUID: undefined } }]) {
      assert.throws(() => verify(signing.sign({ ...claims, ...change })));
    }
    assert.throws(() => verify(signing.sign(claims, { alg: 'none' })));
    assert.throws(() => verify(signing.sign(claims, { alg: 'HS256', kid: alg })));
    assert.throws(() => verify(signing.sign(claims, { alg, kid: 'unknown' })));
    assert.throws(() => verify(signing.sign(claims, { alg, kid: alg, crit: ['custom'] })));
    const signed = signing.sign(claims);
    const parts = signed.split('.');
    parts[1] = Buffer.from(JSON.stringify({ ...claims, sub: 'actor/proxy-access/admin' })).toString('base64url');
    assert.throws(() => verify(parts.join('.')));
    const signature = Buffer.from(parts[2], 'base64url');
    signature[0] ^= 1;
    assert.throws(() => verify(`${signed.split('.').slice(0, 2).join('.')}.${signature.toString('base64url')}`));
  });
}

test('literal native actor claim and RSA/EC algorithm confusion', () => {
  const signing = signer('RS256');
  const verify = createActorVerifier({ issuer: claims.iss, audience: claims.aud,
    allowedAtespaces: ['proxy-access'], jwks: { keys: [signing.jwk] } },
  () => 1000);
  assert.equal(verify(signing.sign(claims)).actorUID, 'test-actor-uid');
  assert.throws(() => verify(signing.sign({ ...claims, 'actor.ate.dev': undefined,
    actor: { ate: { dev: claims['actor.ate.dev'] } } })));
  assert.throws(() => verify(signing.sign(claims, { alg: 'ES256', kid: 'RS256' })));
  assert.throws(() => createActorVerifier({ issuer: claims.iss, audience: claims.aud,
    allowedAtespaces: ['proxy-access'], jwks: { keys: [{ kty: 'oct', k: 'c2VjcmV0', alg: 'HS256' }] } }));
});

test('access tokens expire, are signed, are restart-local and bounded by actor expiry', () => {
  let now = 1000;
  const store = createTokenStore({ clock: () => now });
  const token = store.issue({ sub: claims.sub, atespace: 'proxy-access', actorExpiresAt: 1100 });
  assert.equal(token.expires_in, 100);
  assert.equal(store.verify(token.access_token).sub, claims.sub);
  assert.throws(() => createTokenStore({ clock: () => now }).verify(token.access_token));
  const parts = token.access_token.split('.');
  parts[0] = Buffer.from(JSON.stringify({ exp: 9999 })).toString('base64url');
  assert.throws(() => store.verify(parts.join('.')));
  now = 1100;
  assert.throws(() => store.verify(token.access_token));
  const full = store.issue({ sub: claims.sub, atespace: 'proxy-access' });
  assert.equal(full.expires_in, 300);
  now += 300;
  assert.throws(() => store.verify(full.access_token));
});

test('CONNECT accepts DNS HTTPS443 only, not alternate ports, credentials, IP or malformed authorities', () => {
  assert.equal(authority('DEV.AZURE.COM:443'), 'dev.azure.com:443');
  assert.equal(authority('pkgs.dev.azure.com:443'), 'pkgs.dev.azure.com:443');
  for (const value of ['127.0.0.1:443', '[::1]:443', '10.0.0.1:80', 'dev.azure.com:80',
    'dev.azure.com:0443', 'dev.azure.com', 'https://dev.azure.com:443', 'x@y.com:443',
    'dev.azure.com:443/path', 'dev.azure.com:443\r\nX:1', '-bad.example:443',
    'bad..example:443', 'example.com.:443', '0x7f000001:443', '2130706433:443', 'a.123:443']) {
    assert.throws(() => authority(value), value);
  }
});

module.exports = { signer };

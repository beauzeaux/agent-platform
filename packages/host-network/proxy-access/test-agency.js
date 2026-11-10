'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {createTokenStore} = require('./chart/files/auth');
const {brokerAllowed, agencyRoute} = require('./chart/files/agency-transport');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

test('only explicit UID-bound Agency leases authorize broker routes', () => {
  const store = createTokenStore();
  const actor = {sub: 'actor/proxy-access/engsys-agency-coder', atespace: 'proxy-access',
    actorUID: 'pilot-uid'};
  assert.equal(brokerAllowed(store.verify(store.issue(actor).access_token), {actorUID: 'pilot-uid'}), false);
  const lease = store.verify(store.issue({...actor, capabilities: ['agency']}).access_token);
  assert.equal(brokerAllowed(lease, {actorUID: 'pilot-uid'}), true);
  assert.equal(brokerAllowed(lease, {actorUID: 'recreated-uid'}), false);
  assert.equal(brokerAllowed({...lease, sub: 'actor/proxy-access/engsys-coder'}, {actorUID: 'pilot-uid'}), false);
  assert.equal(brokerAllowed(lease, {}), false);
});

test('broker route allowlist does not admit arbitrary paths or methods', () => {
  assert.equal(agencyRoute({method: 'POST', url: '/agency/plugins'}), true);
  assert.equal(agencyRoute({method: 'DELETE', url: '/agency/mcp'}), true);
  for (const [method, url] of [['GET', '/agency/mcp'], ['POST', '/agency/mcp?url=x'],
    ['POST', '/agency/other'], ['DELETE', '/agency/plugins']]) {
    assert.equal(agencyRoute({method, url}), false);
  }
});

test('configured actor subjects cannot borrow another UID profile', () => {
  const configuration = {actors: {'a-uid': {subject: 'actor/proxy-access/a'},
    'b-uid': {subject: 'actor/proxy-access/b'}}};
  assert.equal(brokerAllowed({sub: 'actor/proxy-access/a', actorUID: 'a-uid',
    capabilities: ['agency']}, configuration), true);
  assert.equal(brokerAllowed({sub: 'actor/proxy-access/b', actorUID: 'a-uid',
    capabilities: ['agency']}, configuration), false);
});

test('dynamic bindings update without restart and missing configuration fails closed', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agency-bindings-'));
  const file = path.join(root, 'actors.json');
  const configuration = {dynamicActorsFile: file};
  const actor = {sub:'actor/factory-executions/session-a', actorUID:'a', capabilities:['agency']};
  try {
    fs.writeFileSync(file, JSON.stringify({actors:{a:{subject:actor.sub}}}));
    assert.equal(brokerAllowed(actor, configuration), true);
    fs.writeFileSync(file, JSON.stringify({actors:{a:{subject:'actor/factory-executions/session-b'}}}));
    assert.equal(brokerAllowed(actor, configuration), false);
    fs.writeFileSync(file, '{invalid');
    assert.equal(brokerAllowed(actor, configuration), false);
    fs.unlinkSync(file);
    assert.equal(brokerAllowed(actor, configuration), false);
  } finally { fs.rmSync(root, {recursive:true, force:true}); }
});

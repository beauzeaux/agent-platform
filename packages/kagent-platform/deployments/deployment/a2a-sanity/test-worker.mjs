import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {FIXTURE_TEST, fixtureTools, SanityWorker} from './worker.mjs';

const exec = promisify(execFile);
const command = async (program, args, options) => (await exec(program, args, options)).stdout;

test('fixture tools enforce write scope and execute actual immutable checks', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'a2a-sanity-tools-'));
  try {
    await fs.writeFile(path.join(root, 'fixture-test.mjs'), FIXTURE_TEST);
    await fs.writeFile(path.join(root, 'solution.mjs'), 'export function sum(v) { return v.reduce((a,b) => {' +
      'if (typeof b !== "number" || !Number.isFinite(b)) throw new TypeError(); return a+b; }, 0); }\n');
    let writes = 0;
    const observed = [];
    const tools = fixtureTools(root, [{name: 'repo_write', handler: async () => { writes++; }}], command, observed);
    const write = tools.find(tool => tool.name === 'repo_write');
    await assert.rejects(write.handler({path: 'fixture-test.mjs', content: ''}), /fixture_write_scope/);
    assert.equal(writes, 0);
    assert.deepEqual(JSON.parse(await tools.find(tool => tool.name === 'fixture_test').handler({})),
      {passed: true, checks: 7});
    await fs.writeFile(path.join(root, 'fixture-test.mjs'), 'console.log("SANITY_TESTS_PASSED=7");');
    await assert.rejects(tools.find(tool => tool.name === 'fixture_test').handler({}), /fixture_test_changed/);
  } finally { await fs.rm(root, {recursive: true, force: true}); }
});

test('live opt-in, fixed input, idempotency and recovered tasks do not replay inference', async () => {
  const data = await fs.mkdtemp(path.join(os.tmpdir(), 'a2a-sanity-worker-'));
  let executions = 0;
  const options = {data, model: 'fixture-model', execute: async () => { executions++; return {testsPassed: 7}; }};
  try {
    const worker = new SanityWorker({...options, live: false});
    await worker.initialize();
    const input = {id: 'task-one', prompt: 'sanity-code', allowEdits: true};
    assert.equal((await worker.submit(input)).status, 403);
    worker.live = true;
    assert.equal((await worker.submit({...input, prompt: 'arbitrary'})).status, 400);
    assert.equal((await worker.submit(input)).status, 202);
    await worker.running;
    assert.equal((await worker.submit(input)).status, 200);
    assert.equal(executions, 1);
    assert.equal((await worker.submit({...input, id: 'another'})).status, 409);
    const filename = path.join(data, 'sanity-tasks', 'task-one.json');
    const receipt = JSON.parse(await fs.readFile(filename, 'utf8'));
    receipt.status = 'working';
    await fs.writeFile(filename, JSON.stringify(receipt));
    const restored = new SanityWorker({...options, live: true});
    await restored.initialize();
    assert.equal((await restored.submit(input)).body.status, 'interrupted');
    assert.equal(executions, 1);
  } finally { await fs.rm(data, {recursive: true, force: true}); }
});

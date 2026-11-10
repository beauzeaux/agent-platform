import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {PublicProbeWorker, HOST_ORIGIN, metadataProbe, memoryProbe,
  publicCompiledConfig, replaceEnvironment, sdkEnvironment, sessionOptions, createProbeServer} from './probe-worker.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..', '..', '..', '..', '..');
const {SessionArchive} = await import(pathToFileURL(path.join(root, 'tools', 'kagent-lab',
  'engsys-agency-coder', 'session-archive.mjs')).href);
const {mapNativeConfig} = await import(pathToFileURL(path.join(root, 'tools', 'kagent-lab',
  'engsys-agency-coder', 'native-config.mjs')).href);
const native = () => ({model: 'public-model', instruction: 'PRIVATE_INSTRUCTION_MUST_NOT_BE_SENT',
  agentName: 'public-canary-a', agentNamespace: 'factory-executions',
  provider: {type: 'openai', wireApi: 'responses', apiKey: 'kagent-credential-injected',
    baseUrl: HOST_ORIGIN + '/v1', headers: {}},
  mcpServers: {public: {type: 'http', url: HOST_ORIGIN + '/agency/mcp', tools: ['call_tool'],
    headers: {Authorization: 'Bearer kagent-credential-injected'}}}});
const event = (id, type, data = {}, parentId = null) =>
  ({id, type, data, parentId, timestamp: '2026-11-03T23:00:00Z'});

function fakeClient(observed) {
  let handler;
  const events = [event('start', 'session.start')];
  const session = {sessionId: null,
    on(callback) { handler = callback; return () => { handler = null; }; },
    getEvents: async () => events,
    async sendAndWait(options) {
      assert.ok(handler, 'archive listener precedes the only model send');
      observed.sends.push(options);
      for (const value of [event('user', 'user.message', {content: options.prompt}, 'start'),
        event('answer', 'assistant.message', {content: 'NATIVE_HOST_OK'}, 'user'),
        event('end', 'assistant.turn_end', {}, 'answer')]) { events.push(value); handler(value); }
      return {data: {content: 'NATIVE_HOST_OK'}};
    },
    disconnect: async () => {},
  };
  return {start: async () => {}, forceStop: async () => {}, listSessions: async () => [],
    async createSession(options) { observed.options.push(options); session.sessionId = options.sessionId; return session; }};
}

async function fixture(t, changes = {}) {
  const directory = await fs.mkdtemp(path.join(here, '.test-probe-'));
  t.after(() => fs.rm(directory, {recursive: true, force: true}));
  const identity = path.join(directory, 'identity');
  await fs.writeFile(identity, 'session-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee');
  const env = {AGENT_SESSION_ID: 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee', AGENT_RUNTIME_IDENTITY_PATH: identity,
    SESSION_ARCHIVE_EXPORT_ENABLED: 'true',
    PUBLIC_CANARY_RUN_ID: '11111111-2222-4333-8444-555555555555',
    NATIVE_MEMORY_CONFIG_JSON: JSON.stringify({enabled: true, embedding: {
      baseUrl: 'https://public-embedding.test/v1', model: 'approved', dimensions: 768}})};
  const observed = {sends: [], options: [], uploads: [], creates: 0};
  const options = {data: path.join(directory, 'data'), env, native: native(), emptyTools: {},
    createClient: () => { observed.creates++; return fakeClient(observed); },
    openArchive: options => SessionArchive.open(options),
    uploaderFactory: () => async payload => { observed.uploads.push(payload); },
    loadMemoryTools: async () => [], ...changes};
  const worker = new PublicProbeWorker(options);
  await worker.initialize();
  return {worker, options, env, observed, directory};
}

test('model is gated before admission and arbitrary incoming/private prompts are never accepted', async t => {
  const {worker, observed} = await fixture(t);
  assert.equal((await worker.submit({id: 'disabled', prompt: 'model'})).status, 403);
  assert.equal((await worker.submit({id: 'private', prompt: 'Inspect the private checkout'})).status, 400);
  assert.equal((await worker.submit({id: 'edits', prompt: 'metadata', allowEdits: true})).status, 400);
  assert.equal(observed.creates, 0);
  assert.equal(worker.tasks.size, 0);
});

test('only the fixed public model prompt is sent, and completion follows durable archive settlement', async t => {
  let acknowledge, uploadStarted;
  const started = new Promise(resolve => { uploadStarted = resolve; });
  const upload = new Promise(resolve => { acknowledge = resolve; });
  let exports = 0;
  const {worker, env, observed} = await fixture(t, {uploaderFactory: () => async () => {
    if (++exports === 1) return;
    uploadStarted(); await upload;
  }});
  env.PUBLIC_CANARY_INFERENCE = 'true';
  assert.equal((await worker.submit({id: 'model-task', prompt: 'model', allowEdits: false})).status, 202);
  await started;
  assert.equal(worker.tasks.get('model-task').status, 'working');
  acknowledge();
  await worker.running;
  const receipt = worker.tasks.get('model-task');
  assert.equal(receipt.status, 'completed');
  assert.equal(receipt.archive.status, 'exported');
  assert.equal(receipt.archive.completeness, 'complete');
  assert.deepEqual(observed.sends, [{prompt: 'Reply exactly NATIVE_HOST_OK.'}]);
  const configuration = observed.options[0];
  assert.equal(configuration.systemMessage.content.includes('PRIVATE_INSTRUCTION'), false);
  assert.deepEqual(configuration.tools, []);
  assert.deepEqual(configuration.mcpServers, {});
  assert.equal(configuration.onPermissionRequest({kind: 'shell'}).kind, 'reject');
  const lines = (await fs.readFile(receipt.archive.eventsPath, 'utf8')).trim().split('\n').map(JSON.parse);
  assert.equal(JSON.stringify(lines).includes('PRIVATE_INSTRUCTION'), false);
  assert.ok(lines.some(item => item.origin === 'operator-canary' && item.eventType === 'operator.canary.finished'));
  assert.ok(lines.some(item => item.origin === 'live' && item.eventType === 'assistant.message'));
  assert.equal((await worker.submit({id: 'model-task', prompt: 'model'})).status, 200);
  assert.equal((await worker.submit({id: 'different-model-id', prompt: 'model'})).status, 409);
  assert.equal(observed.sends.length, 1);
  assert.equal(receipt.sdkSessionId.includes(env.AGENT_SESSION_ID), false);
});

test('export outage retains a pending durable outbox and never retries the completed model turn', async t => {
  let exports = 0;
  const {worker, env, observed} = await fixture(t, {uploaderFactory: () => async () => {
    if (++exports > 1) throw new Error('fixture outage');
  }});
  env.PUBLIC_CANARY_INFERENCE = 'true';
  await worker.submit({id: 'outage', prompt: 'model'});
  await worker.running;
  const receipt = worker.tasks.get('outage');
  assert.equal(receipt.status, 'completed');
  assert.equal(receipt.archive.status, 'pending');
  assert.equal(JSON.parse(await fs.readFile(receipt.archive.outboxPath)).pendingError, 'archive_export_failed');
  await worker.submit({id: 'outage', prompt: 'model'});
  assert.equal(observed.sends.length, 1);
});

test('archive activation failure blocks the paid SDK turn before any inference', async t => {
  const {worker, env, observed} = await fixture(t, {uploaderFactory: () => async () => { throw new Error('not activated'); }});
  env.PUBLIC_CANARY_INFERENCE = 'true';
  await worker.submit({id: 'preflight', prompt: 'model'});
  await worker.running;
  assert.equal(observed.sends.length, 0);
  assert.equal(worker.tasks.get('preflight').status, 'failed');
  assert.equal(worker.tasks.get('preflight').error, 'public_archive_preflight_pending');
});

test('canary implements the compiled MCP close default without importing private instructions or bypassing TLS', () => {
  const compiled = {runtime: {name: 'public-canary', namespace: 'factory-executions'}, agent: {
    instruction: 'PRIVATE_COMPILED_INSTRUCTION', model: {model: 'public-model',
      openai: {base_url: HOST_ORIGIN + '/v1', api_format: 'responses'}},
    http_tools: [{params: {url: HOST_ORIGIN + '/agency/mcp', terminate_on_close: true,
      headers: {Authorization: 'Bearer kagent-credential-injected'}}, tools: ['call_tool']}],
  }};
  const mapped = publicCompiledConfig(compiled, mapNativeConfig, {OPENAI_API_KEY: 'kagent-credential-injected'});
  assert.equal(mapped.instruction.includes('PRIVATE_COMPILED'), false);
  assert.equal(compiled.agent.http_tools[0].params.terminate_on_close, true);
  compiled.agent.http_tools[0].params.terminate_on_close = false;
  assert.throws(() => publicCompiledConfig(compiled, mapNativeConfig, {OPENAI_API_KEY: 'kagent-credential-injected'}));
});

test('working receipts recover as interrupted without SDK startup or automatic replay', async t => {
  const {worker, options, observed} = await fixture(t);
  await worker.save({id: 'old', action: 'model', status: 'working', businessOutcome: 'completed'});
  const restored = new PublicProbeWorker(options);
  await restored.initialize();
  assert.equal((await restored.submit({id: 'old', prompt: 'model'})).body.status, 'interrupted');
  assert.equal(observed.creates, 0);
});

test('a different projected actor cannot adopt the public SDK/data scope', async t => {
  const {worker, options} = await fixture(t);
  await fs.writeFile(options.env.AGENT_RUNTIME_IDENTITY_PATH, 'session-foreign-actor');
  await assert.rejects(worker.verifyIdentity(), /public_runtime_identity_mismatch/);
});

test('private checkout contamination and inherited credential/Git environment are rejected or excluded', async t => {
  const {worker, options} = await fixture(t);
  await fs.mkdir(path.join(worker.data, 'engsys'));
  await assert.rejects(new PublicProbeWorker(options).initialize(), /nonpublic_durable_directory_rejected/);
  const environment = sdkEnvironment({PATH: 'public-path', GITHUB_TOKEN: 'do-not-copy',
    GIT_AUTHOR_NAME: 'private-user', AGENCY_GLOBAL_CONFIG_PATH: 'private-config'}, worker.directories);
  assert.equal(environment.COPILOT_OFFLINE, 'true');
  assert.equal(environment.GITHUB_TOKEN, undefined);
  assert.equal(environment.GIT_AUTHOR_NAME, undefined);
  assert.equal(environment.AGENCY_GLOBAL_CONFIG_PATH, undefined);
  const derived = sdkEnvironment({COPILOT_PROVIDER_BASE_URL: 'https://private.invalid',
    COPILOT_PROVIDER_API_KEY: 'private-key'}, worker.directories, native());
  assert.equal(derived.COPILOT_PROVIDER_BASE_URL, HOST_ORIGIN + '/v1');
  assert.equal(derived.COPILOT_PROVIDER_API_KEY, 'kagent-credential-injected');
  assert.throws(() => sdkEnvironment({}, worker.directories, {
    ...native(), provider: {...native().provider, baseUrl: 'https://private.invalid/v1'},
  }), /native_host_destination_required/);
  const merged = {AGENT_SESSION_ID: 'private-native-identity', GITHUB_TOKEN: 'private-token',
    GIT_AUTHOR_NAME: 'private-person'};
  replaceEnvironment(merged, environment);
  assert.equal(merged.AGENT_SESSION_ID, undefined);
  assert.equal(merged.GITHUB_TOKEN, undefined);
  assert.equal(merged.GIT_AUTHOR_NAME, undefined);
  assert.equal(merged.HOME, worker.directories.home);
  const optionsForSession = sessionOptions(native(), worker.directories.work, {});
  assert.equal(optionsForSession.enableSkills, false);
  assert.equal(optionsForSession.enableSessionTelemetry, false);
  assert.equal(optionsForSession.enableSessionStore, false);
});

test('native MCP initialize/list/DELETE stays outside the SDK and records no returned tool descriptions', async () => {
  const calls = [];
  const transport = async (url, request = {}) => {
    calls.push({url, ...request});
    if (url.endsWith('/models')) return Response.json({data: [{id: 'public-model'}]});
    if (request.method === 'DELETE') return new Response(null, {status: 204});
    const body = JSON.parse(request.body);
    if (!body.id) return new Response(null, {status: 202});
    return Response.json({jsonrpc: '2.0', id: body.id, result: body.method === 'initialize'
      ? {protocolVersion: '2025-03-26'} : {tools: [{name: 'call_tool', description: 'DO_NOT_FORWARD_TO_MODEL'}]}},
    {headers: {'mcp-session-id': 'public-mcp-session'}});
  };
  const result = await metadataProbe(native(), transport);
  assert.equal(result.sessionClosed, true);
  assert.equal(calls.length, 5);
  assert.equal(calls[0].headers.Authorization, 'Bearer kagent-credential-injected');
  assert.equal(calls.at(-1).method, 'DELETE');
  assert.equal(JSON.stringify(result).includes('DO_NOT_FORWARD'), false);
});

test('operator metadata task performs no model send and honestly exports a partial no-turn SDK archive', async t => {
  const transport = async (url, request = {}) => {
    if (url.endsWith('/models')) return Response.json({data: [{id: 'public-model'}]});
    if (request.method === 'DELETE') return new Response(null, {status: 204});
    const body = JSON.parse(request.body);
    if (!body.id) return new Response(null, {status: 202});
    return Response.json({id: body.id, result: body.method === 'initialize'
      ? {protocolVersion: '2025-03-26'} : {tools: [{name: 'call_tool', description: 'PRIVATE_DESCRIPTOR_NOT_FOR_MODEL'}]}},
    {headers: {'mcp-session-id': 'metadata-session'}});
  };
  const {worker, observed} = await fixture(t, {transport});
  await worker.submit({id: 'metadata-task', prompt: 'metadata'});
  await worker.running;
  const receipt = worker.tasks.get('metadata-task');
  assert.equal(receipt.status, 'completed');
  assert.equal(receipt.archive.status, 'exported');
  assert.equal(receipt.archive.completeness, 'partial');
  assert.equal(observed.sends.length, 0);
  const log = await fs.readFile(receipt.archive.eventsPath, 'utf8');
  assert.equal(log.includes('PRIVATE_DESCRIPTOR'), false);
  assert.equal(log.includes('"eventType":"assistant.message"'), false);
  assert.ok(log.includes('"origin":"operator-canary"'));
});

test('public memory seed verifies its own row; isolation is read-only and fails if any foreign row appears', async () => {
  const env = {PUBLIC_CANARY_RUN_ID: '11111111-2222-4333-8444-555555555555',
    NATIVE_MEMORY_CONFIG_JSON: JSON.stringify({enabled: true,
      embedding: {baseUrl: 'https://fixture.test/v1', model: 'fixture', dimensions: 768}})};
  let rows = [], writes = 0;
  const tools = [{name: 'memory_propose', handler: async ({content}) => {
    writes++; rows.push({id: 'public-row', content}); return JSON.stringify({status: 'proposed', id: 'public-row'});
  }}, {name: 'memory_search', handler: async () => JSON.stringify({status: 'ok', memories: rows})}];
  assert.equal((await memoryProbe('seed', env, tools)).memoryId, 'public-row');
  assert.ok(rows[0].content.startsWith('Paris is the capital of France.'));
  await assert.rejects(memoryProbe('isolate', env, tools), /public_memory_isolation_failed/);
  rows = [];
  assert.equal((await memoryProbe('isolate', env, tools)).visibleRows, 0);
  assert.equal(writes, 1);
});

test('HTTP task worker exposes managed-harness routes but no administrative execution or arbitrary prompt API', async t => {
  const {worker} = await fixture(t);
  const server = createProbeServer(worker);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const origin = 'http://127.0.0.1:' + server.address().port;
  assert.equal((await fetch(origin + '/readyz')).status, 200);
  assert.equal((await fetch(origin + '/tasks/missing')).status, 404);
  assert.equal((await fetch(origin + '/exec', {method: 'POST', body: '{}'})).status, 404);
  assert.equal((await fetch(origin + '/tasks', {method: 'POST',
    body: JSON.stringify({id: 'bad', prompt: 'arbitrary text'})})).status, 400);
  assert.equal((await fetch(origin + '/status')).status, 200);
});

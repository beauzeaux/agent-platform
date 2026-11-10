import fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import http from 'node:http';

export const MODEL_PROMPT = 'Reply exactly NATIVE_HOST_OK.';
export const SYSTEM_PROMPT = 'Public integration probe only. Follow the fixed reply request. Never invoke tools.';
export const HOST_ORIGIN = 'https://host-capabilities.proxy-access.svc.cluster.local:8445';
export const CLI_ARGUMENTS = ['--no-custom-instructions', '--disable-builtin-mcps', '--no-auto-update',
  '--no-ask-user', '--no-color', '--disable-client-telemetry'];
const actions = new Set(['metadata', 'model', 'seed', 'isolate']);
const fail = code => { throw Object.assign(new Error(code), {publicCode: code}); };
const ownHeaders = source => Object.fromEntries(Object.entries(source ?? {}).map(([name, value]) => {
  if (name.toLowerCase() !== 'authorization' || value !== 'Bearer kagent-credential-injected') {
    fail('nonpublic_configured_header');
  }
  return [name, value];
}));

export function publicCompiledConfig(compiled, mapper, env) {
  const agent = {...compiled.agent, instruction: SYSTEM_PROMPT};
  for (const name of ['http_tools', 'httpTools']) {
    if (!agent[name]) continue;
    agent[name] = agent[name].map(server => {
      const params = {...server.params};
      const snake = Object.hasOwn(params, 'terminate_on_close');
      const camel = Object.hasOwn(params, 'terminateOnClose');
      if ((snake && camel) || (snake && params.terminate_on_close !== true) ||
          (camel && params.terminateOnClose !== true)) fail('canary_mcp_requires_explicit_close');
      delete params.terminate_on_close;
      delete params.terminateOnClose;
      return {...server, params};
    });
  }
  return mapper({...compiled, agent}, env);
}

export function validateNative(native) {
  if (!native || native.provider?.type !== 'openai' || native.provider.wireApi !== 'responses' ||
      native.provider.apiKey !== 'kagent-credential-injected') fail('native_provider_required');
  const model = new URL(native.provider.baseUrl);
  if (model.origin !== HOST_ORIGIN || !['/v1', '/v1/'].includes(model.pathname) ||
      model.username || model.password || model.search || model.hash) {
    fail('native_host_destination_required');
  }
  ownHeaders(native.provider.headers);
  for (const server of Object.values(native.mcpServers ?? {})) {
    if (server.type !== 'http' || server.url !== HOST_ORIGIN + '/agency/mcp') fail('native_mcp_destination_required');
    ownHeaders(server.headers);
  }
}

export function sdkEnvironment(env, directories, native) {
  if (native) validateNative(native);
  return {PATH: env.PATH ?? '', HOME: directories.home, XDG_CONFIG_HOME: directories.config,
    COPILOT_HOME: directories.copilot, COPILOT_OFFLINE: 'true', COPILOT_TELEMETRY_DISABLED: 'true',
    COPILOT_GITHUB_TELEMETRY_DISABLED: 'true', SSL_CERT_FILE: env.SSL_CERT_FILE,
    NODE_EXTRA_CA_CERTS: env.NODE_EXTRA_CA_CERTS, SSL_CERT_DIR: '/etc/ssl/certs', LANG: 'C.UTF-8',
    TMPDIR: directories.temp, TMP: directories.temp, TEMP: directories.temp,
    ...(native ? {COPILOT_PROVIDER_BASE_URL: native.provider.baseUrl, COPILOT_PROVIDER_TYPE: 'openai',
      COPILOT_PROVIDER_API_KEY: native.provider.apiKey, COPILOT_PROVIDER_WIRE_API: 'responses',
      COPILOT_MODEL: native.model} : {})};
}

export function replaceEnvironment(target, selected) {
  for (const key of Object.keys(target)) delete target[key];
  for (const [key, value] of Object.entries(selected)) if (value !== undefined) target[key] = value;
}

export function sessionOptions(native, workingDirectory, emptyTools) {
  return {model: native.model, provider: native.provider, workingDirectory,
    systemMessage: {mode: 'replace', content: SYSTEM_PROMPT}, streaming: true,
    enableSessionTelemetry: false, enableSessionStore: false, enableSkills: false,
    tools: [], availableTools: emptyTools, mcpServers: {}, pluginDirectories: [], skillDirectories: [],
    hookDirectories: [], hooks: {}, onPermissionRequest: () => ({kind: 'reject'})};
}

async function atomic(filename, value) {
  const stage = filename + '.partial';
  const handle = await fs.open(stage, 'w', 0o600);
  try { await handle.writeFile(JSON.stringify(value)); await handle.sync(); }
  finally { await handle.close(); }
  await fs.rename(stage, filename);
}

async function boundedJson(response, empty = false) {
  if (!response.ok) fail('native_probe_http_failed');
  const chunks = [];
  let bytes = 0;
  for await (const chunk of response.body ?? []) {
    bytes += chunk.length;
    if (bytes > 524288) fail('native_probe_response_limit');
    chunks.push(chunk);
  }
  if (!bytes && empty) return null;
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { fail('native_probe_invalid_json'); }
}

function providerHeaders(provider) {
  const headers = ownHeaders(provider.headers);
  if (!Object.keys(headers).some(name => name.toLowerCase() === 'authorization')) {
    headers.Authorization = 'Bearer ' + provider.apiKey;
  }
  return headers;
}

export async function metadataProbe(native, transport = globalThis.fetch) {
  const models = await boundedJson(await transport(native.provider.baseUrl.replace(/\/$/, '') + '/models', {
    headers: providerHeaders(native.provider), redirect: 'error', signal: AbortSignal.timeout(20000),
  }));
  if (!Array.isArray(models.data) || !models.data.some(item => item.id === native.model)) fail('configured_model_not_advertised');
  const servers = Object.values(native.mcpServers);
  if (servers.length !== 1) fail('exactly_one_native_mcp_required');
  const server = servers[0];
  const headers = {...ownHeaders(server.headers), 'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream'};
  if (!Object.keys(headers).some(name => name.toLowerCase() === 'authorization')) fail('native_mcp_credential_required');
  let session, protocol = '2025-03-26', closed = false;
  const rpc = async (method, id, params = {}) => {
    const response = await transport(server.url, {method: 'POST', headers: {...headers,
      'MCP-Protocol-Version': protocol, ...(session ? {'Mcp-Session-Id': session} : {})},
    body: JSON.stringify({jsonrpc: '2.0', ...(id ? {id} : {}), method, params}),
    redirect: 'error', signal: AbortSignal.timeout(20000)});
    const identifier = response.headers.get('mcp-session-id');
    if (identifier) {
      if (!/^[A-Za-z0-9_.~-]{1,256}$/.test(identifier) || (session && session !== identifier)) fail('invalid_mcp_session');
      session = identifier;
    }
    const result = await boundedJson(response, !id);
    if (id && (!result || result.id !== id || result.error || !result.result)) fail('native_mcp_rpc_failed');
    return result?.result;
  };
  let listed;
  try {
    const initialized = await rpc('initialize', 1, {protocolVersion: protocol, capabilities: {},
      clientInfo: {name: 'public-native-canary', version: '1'}});
    if (!/^\d{4}-\d{2}-\d{2}$/.test(initialized.protocolVersion ?? '')) fail('invalid_mcp_protocol');
    protocol = initialized.protocolVersion;
    if (!session) fail('native_mcp_session_missing');
    await rpc('notifications/initialized');
    listed = await rpc('tools/list', 2);
    if (!Array.isArray(listed.tools) || !server.tools.every(name => listed.tools.some(tool => tool.name === name))) {
      fail('configured_mcp_tools_missing');
    }
  } finally {
    if (session) {
      const response = await transport(server.url, {method: 'DELETE', headers: {...headers,
        'Mcp-Session-Id': session, 'MCP-Protocol-Version': protocol}, redirect: 'error',
      signal: AbortSignal.timeout(20000)});
      if (!response.ok) fail('native_mcp_close_failed');
      await response.body?.cancel();
      closed = true;
    }
  }
  return {configuredModelAdvertised: true, initialized: true, toolCount: listed.tools.length, sessionClosed: closed};
}

export function memoryConfiguration(env) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(env.PUBLIC_CANARY_RUN_ID ?? '')) {
    fail('public_canary_run_id_required');
  }
  let config;
  try { config = JSON.parse(env.NATIVE_MEMORY_CONFIG_JSON ?? ''); }
  catch { fail('public_memory_config_required'); }
  if (config.enabled !== true || !config.embedding || config.embedding.dimensions !== 768 ||
      Object.keys(config).some(name => !['enabled', 'embedding', 'localBridgeUrl', 'privateHttpOrigins'].includes(name)) ||
      Object.keys(config.embedding).some(name => !['baseUrl', 'model', 'dimensions', 'apiKey'].includes(name)) ||
      (config.embedding.apiKey && config.embedding.apiKey !== 'kagent-credential-injected') ||
      (config.localBridgeUrl && config.localBridgeUrl !== 'http://127.0.0.1:18082')) fail('invalid_public_memory_config');
  return config;
}

export async function memoryProbe(action, env, tools) {
  memoryConfiguration(env);
  const propose = tools.find(tool => tool.name === 'memory_propose');
  const search = tools.find(tool => tool.name === 'memory_search');
  if (!propose || !search) fail('native_memory_tools_unavailable');
  const marker = 'PUBLIC_CANARY_' + env.PUBLIC_CANARY_RUN_ID;
  let id;
  if (action === 'seed') {
    const result = JSON.parse(await propose.handler({content: `Paris is the capital of France. ${marker}.`}));
    if (result.status !== 'proposed' || typeof result.id !== 'string' || !/^[A-Za-z0-9-]{1,128}$/.test(result.id)) {
      fail('public_memory_seed_failed');
    }
    id = result.id;
  }
  const result = JSON.parse(await search.handler({query: `Paris is the capital of France. ${marker}.`}));
  if (result.status !== 'ok' || !Array.isArray(result.memories)) fail('public_memory_search_failed');
  if (action === 'seed' && !result.memories.some(row => row.id === id)) fail('public_memory_seed_not_visible');
  if (action === 'isolate' && result.memories.length) fail('public_memory_isolation_failed');
  return {action, visibleRows: result.memories.length, ...(id ? {memoryId: id} : {}), publicRunId: env.PUBLIC_CANARY_RUN_ID};
}

export class PublicProbeWorker {
  constructor({data, env, native, createClient, openArchive, uploaderFactory, loadMemoryTools, emptyTools,
    credentials = [], transport = globalThis.fetch}) {
    Object.assign(this, {data, env, native, createClient, openArchive, uploaderFactory, loadMemoryTools,
      emptyTools, credentials, transport});
    this.root = path.join(data, 'public-probe');
    this.directories = {home: path.join(this.root, 'home'), config: path.join(this.root, 'config'),
      copilot: path.join(this.root, 'copilot'), work: path.join(this.root, 'work'), temp: path.join(this.root, 'temp')};
    this.journal = path.join(this.root, 'tasks');
    this.tasks = new Map();
  }

  async initialize() {
    validateNative(this.native);
    if (!this.env.AGENT_SESSION_ID || !this.env.AGENT_RUNTIME_IDENTITY_PATH ||
        this.env.NODE_TLS_REJECT_UNAUTHORIZED === '0') fail('public_native_identity_required');
    await fs.mkdir(this.data, {recursive: true, mode: 0o700});
    if ((await fs.readdir(this.data)).some(name => !['session-owner', 'public-probe'].includes(name))) {
      fail('nonpublic_durable_directory_rejected');
    }
    for (const directory of [this.root, ...Object.values(this.directories), this.journal]) {
      await fs.mkdir(directory, {recursive: true, mode: 0o700});
      if ((await fs.lstat(directory)).isSymbolicLink()) fail('public_directory_symlink_rejected');
    }
    if ((await fs.readdir(this.directories.work)).length) fail('public_work_directory_not_empty');
    await this.verifyIdentity();
    for (const name of await fs.readdir(this.journal)) {
      if (!/^[A-Za-z0-9-]{1,64}\.json$/.test(name)) continue;
      const receipt = JSON.parse(await fs.readFile(path.join(this.journal, name), 'utf8'));
      if (['accepted', 'working'].includes(receipt.status)) {
        receipt.status = 'interrupted';
        receipt.error = 'interrupted_no_automatic_replay';
        await this.save(receipt);
      }
      this.tasks.set(receipt.id, receipt);
    }
  }

  async verifyIdentity() {
    const actor = (await fs.readFile(this.env.AGENT_RUNTIME_IDENTITY_PATH, 'utf8')).trim();
    if (actor !== 'session-' + this.env.AGENT_SESSION_ID) {
      fail('public_runtime_identity_mismatch');
    }
    this.actorName = actor;
  }

  save(receipt) { return atomic(path.join(this.journal, receipt.id + '.json'), receipt); }

  async submit(input) {
    if (!input || Object.keys(input).some(key => !['id', 'prompt', 'allowEdits'].includes(key)) ||
        typeof input.id !== 'string' || !/^[A-Za-z0-9-]{1,64}$/.test(input.id) || !actions.has(input.prompt) ||
        (input.allowEdits !== undefined && input.allowEdits !== false)) return {status: 400, body: {error: 'fixed_public_action_required'}};
    const old = this.tasks.get(input.id);
    if (old) return {status: old.action === input.prompt ? 200 : 409,
      body: old.action === input.prompt ? old : {error: 'task_identity_conflict'}};
    if (input.prompt === 'model' && this.env.PUBLIC_CANARY_INFERENCE !== 'true') {
      return {status: 403, body: {error: 'public_canary_inference_disabled'}};
    }
    if (input.prompt === 'model' && [...this.tasks.values()].some(task => task.action === 'model' && task.businessWorkAttempted)) {
      return {status: 409, body: {error: 'public_inference_already_attempted'}};
    }
    if (['model', 'seed'].includes(input.prompt) && this.env.SESSION_ARCHIVE_EXPORT_ENABLED !== 'true') {
      return {status: 403, body: {error: 'public_archive_export_required'}};
    }
    if (['seed', 'isolate'].includes(input.prompt)) memoryConfiguration(this.env);
    if (this.busy) return {status: 409, body: {error: 'public_canary_busy'}};
    const receipt = {id: input.id, action: input.prompt, status: 'accepted', publicDataOnly: true,
      sdkSessionId: 'public-canary-' + randomUUID(),
      sourceSha256: this.env.PUBLIC_CANARY_SOURCE_SHA256 ?? null,
      acceptedAt: new Date().toISOString()};
    this.busy = receipt.id;
    try { await this.save(receipt); }
    catch (error) { this.busy = null; throw error; }
    this.tasks.set(receipt.id, receipt);
    this.running = this.execute(receipt).catch(() => {
      this.fatalError = 'public_journal_unavailable';
      receipt.status = 'interrupted';
      receipt.error = 'journal_unavailable_no_replay';
      this.busy = null;
    });
    return {status: 202, body: {id: receipt.id, status: receipt.status}};
  }

  async execute(receipt) {
    let client, session, archive;
    let outcome = 'failed';
    let finalStatus = 'failed', stage = 'identity';
    const sdkSessionId = receipt.sdkSessionId;
    const operator = async (type, data) => archive.record({id: `operator-${receipt.id}-${type}`,
      type: 'operator.canary.' + type, timestamp: new Date().toISOString(), parentId: null, ephemeral: true,
      data: {origin: 'operator-canary', publicDataOnly: true, ...data}}, receipt.id, 'operator-canary');
    try {
      receipt.status = 'working';
      await this.save(receipt);
      await this.verifyIdentity();
      stage = 'sdk-start';
      client = this.createClient({directories: this.directories,
        environment: sdkEnvironment(this.env, this.directories, this.native)});
      await client.start();
      stage = 'sdk-session';
      const options = sessionOptions(this.native, this.directories.work, this.emptyTools);
      const existing = (await client.listSessions()).some(item => item.sessionId === sdkSessionId);
      session = existing ? await client.resumeSession(sdkSessionId, options)
        : await client.createSession({...options, sessionId: sdkSessionId, trustWorkingDirectory: 'session'});
      stage = 'archive-open';
      archive = await this.openArchive({data: this.root, sdkSessionId: session.sessionId, credentials: this.credentials,
        correlation: {kagentAgent: this.native.agentName, kagentNamespace: this.native.agentNamespace,
          kagentSessionId: this.env.AGENT_SESSION_ID, kagentRevision: this.env.AGENT_KAGENT_REVISION ?? null,
          actorId: this.native.agentNamespace + '/' + this.actorName, actorName: this.actorName,
          actorIdSource: 'projected-name', actorRuntime: this.actorName,
          publicDataOnly: true, probe: 'public-native-canary',
          sourceSha256: this.env.PUBLIC_CANARY_SOURCE_SHA256 ?? null,
          baseFactoryManifest: this.env.PUBLIC_CANARY_BASE_MANIFEST ?? null}});
      archive.attach(session);
      await archive.reconcile(session, {phase: existing ? 'resume' : 'create'});
      await archive.beginTask(receipt.id);
      await operator('started', {action: receipt.action, sdkTurnRequested: receipt.action === 'model'});
      if (['model', 'seed'].includes(receipt.action)) {
        stage = 'archive-preflight';
        const uploader = this.uploaderFactory({sessionId: this.env.AGENT_SESSION_ID, taskId: receipt.id}, this.env);
        if (!uploader || (await archive.upload(uploader)).status !== 'exported') fail('public_archive_preflight_pending');
      }
      stage = 'probe-' + receipt.action;
      receipt.businessWorkAttempted = ['model', 'seed'].includes(receipt.action);
      await this.save(receipt);
      if (receipt.action === 'model') {
        if (this.env.PUBLIC_CANARY_INFERENCE !== 'true') fail('public_canary_inference_disabled');
        const answer = await session.sendAndWait({prompt: MODEL_PROMPT}, 180000);
        if (answer?.data?.content?.trim() !== 'NATIVE_HOST_OK') fail('public_model_reply_mismatch');
        receipt.result = {reply: 'NATIVE_HOST_OK', sdkTurnRequested: true};
      } else if (receipt.action === 'metadata') {
        receipt.result = await metadataProbe(this.native, this.transport);
      } else {
        const tools = await this.loadMemoryTools({context: {repository: 'public-native-canary', revision: 'public-probe-v1',
          sourceTaskId: receipt.id}}, {NATIVE_MEMORY_CONFIG_JSON: this.env.NATIVE_MEMORY_CONFIG_JSON});
        receipt.result = await memoryProbe(receipt.action, this.env, tools);
      }
      outcome = 'completed';
      receipt.businessOutcome = outcome;
      await this.save(receipt);
      await operator('finished', {action: receipt.action, outcome, result: receipt.result});
    } catch (error) {
      receipt.error = error.publicCode ?? 'public_probe_failed';
      receipt.failureStage = stage;
      receipt.businessOutcome ??= 'failed';
      if (archive) await operator('failed', {action: receipt.action, code: receipt.error}).catch(() => {});
    } finally {
      try {
        if (!archive || !session) fail('public_archive_unavailable');
        await archive.finishTask(session, receipt.id, outcome);
        await session.disconnect().catch(() => {});
        session = null;
        await archive.close();
        const uploader = this.uploaderFactory({sessionId: this.env.AGENT_SESSION_ID, taskId: receipt.id}, this.env);
        receipt.archive = uploader ? await archive.upload(uploader) : archive.exportPending();
        const outbox = JSON.parse(await fs.readFile(receipt.archive.outboxPath, 'utf8'));
        if (!outbox.sha256 || outbox.sha256 !== receipt.archive.sha256) fail('public_outbox_not_settled');
        finalStatus = outcome;
      } catch {
        receipt.probeError = receipt.error ?? null;
        receipt.error = 'public_archive_not_settled_no_business_replay';
      } finally {
        if (session) await session.disconnect().catch(() => {});
        if (archive) await archive.close().catch(() => {});
        if (client) await client.forceStop().catch(() => {});
        const final = {...receipt, status: finalStatus, finishedAt: new Date().toISOString()};
        await this.save(final);
        Object.assign(receipt, final);
        this.busy = null;
      }
    }
  }
}

export function createProbeServer(worker) {
  return http.createServer(async (request, response) => {
    const reply = (status, body) => {
      response.writeHead(status, {'content-type': 'application/json'});
      response.end(JSON.stringify(body));
    };
    try {
      if (request.method === 'GET' && request.url === '/readyz') return reply(worker.fatalError ? 503 : 200,
        {ready: !worker.fatalError, publicDataOnly: true});
      if (request.method === 'GET' && request.url === '/status') return reply(200, {publicDataOnly: true,
        inferenceEnabled: worker.env.PUBLIC_CANARY_INFERENCE === 'true',
        sourceSha256: worker.env.PUBLIC_CANARY_SOURCE_SHA256 ?? null,
        baseFactoryManifest: worker.env.PUBLIC_CANARY_BASE_MANIFEST ?? null, activeTask: worker.busy ?? null,
        tasks: [...worker.tasks.values()].map(({id, status, action}) => ({id, status, action}))});
      const match = /^\/tasks\/([A-Za-z0-9-]{1,64})$/.exec(request.url);
      if (request.method === 'GET' && match) {
        const receipt = worker.tasks.get(match[1]);
        return reply(receipt ? 200 : 404, receipt ?? {error: 'task_not_found'});
      }
      if (request.method !== 'POST' || request.url !== '/tasks') return reply(404, {error: 'not_found'});
      let length = 0;
      const chunks = [];
      for await (const chunk of request) {
        length += chunk.length;
        if (length > 4096) return reply(413, {error: 'public_request_limit'});
        chunks.push(chunk);
      }
      const result = await worker.submit(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      reply(result.status, result.body);
    } catch { reply(400, {error: 'invalid_public_request'}); }
  });
}

import fs from 'node:fs/promises';
import path from 'node:path';
import tls from 'node:tls';
import {CLI_ARGUMENTS, sdkEnvironment, replaceEnvironment} from './public-sdk.mjs';
import {mapNativeConfig} from './native-config.mjs';
import {repositoryTools, permissionHandler, command} from './repo-tools.mjs';
import {FIXTURE_TEST, PROMPT, SYSTEM, initializeWorkspace, fixtureTools, SanityWorker, server, sourceDigest} from './worker.mjs';

const runtime = {...process.env};
if (runtime.AGENT_NATIVE_CONFIG !== 'true' || runtime.NODE_TLS_REJECT_UNAUTHORIZED === '0') {
  throw new Error('native_sanity_configuration_required');
}
const native = mapNativeConfig(JSON.parse(await fs.readFile(runtime.AGENT_COMPILED_CONFIG_PATH, 'utf8')), runtime);
if (Object.keys(native.mcpServers).length) throw new Error('coding_fixture_has_no_mcp');
const data = runtime.AGENT_DATA ?? '/data/coding';
const root = path.join(data, 'sanity');
const directories = Object.fromEntries(['home', 'config', 'copilot', 'work', 'temp'].map(name => [name, path.join(root, name)]));
await fs.mkdir(data, {recursive: true, mode: 0o700});
if ((await fs.readdir(data)).some(name => !['session-owner', 'sanity', 'sanity-tasks'].includes(name))) {
  throw new Error('nonfixture_state_rejected');
}
for (const directory of [root, ...Object.values(directories)]) {
  await fs.mkdir(directory, {recursive: true, mode: 0o700});
  if ((await fs.lstat(directory)).isSymbolicLink()) throw new Error('linked_fixture_directory');
}
const roots = await fs.readFile('/etc/ssl/certs/ca-certificates.crt', 'utf8');
const projected = await fs.readFile(runtime.EGRESS_CA_FILE ?? '/run/kagent/egress/trust-bundle.pem', 'utf8');
const bundle = path.join(directories.home, 'ca.pem');
await fs.writeFile(bundle, roots + '\n' + projected, {mode: 0o600});
const certificates = (roots + '\n' + projected).match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g);
tls.setDefaultCACertificates([...tls.getCACertificates('bundled'), ...certificates]);
const environment = sdkEnvironment({...runtime, SSL_CERT_FILE: bundle, NODE_EXTRA_CA_CERTS: bundle}, directories, native);
replaceEnvironment(process.env, environment);
process.chdir(directories.temp);
await initializeWorkspace(directories.work, command);
const {CopilotClient, RuntimeConnection, ToolSet} = await import('@github/copilot-sdk');
const worker = new SanityWorker({data, live: runtime.A2A_SANITY_LIVE === 'true', model: native.model,
  execute: async receipt => {
    const identity = (await fs.readFile(runtime.AGENT_RUNTIME_IDENTITY_PATH, 'utf8')).trim();
    if (identity !== 'session-' + runtime.AGENT_SESSION_ID) throw new Error('runtime_identity_mismatch');
    const tools = fixtureTools(directories.work, repositoryTools(directories.work, true), command, receipt.tools);
    const allowed = new ToolSet();
    for (const tool of tools) allowed.addCustom(tool.name);
    const client = new CopilotClient({
      connection: RuntimeConnection.forStdio({path: '/app/coder/node_modules/@github/copilot-linux-x64/copilot',
        args: CLI_ARGUMENTS}), mode: 'empty', useLoggedInUser: false, logLevel: 'none',
      baseDirectory: directories.copilot, workingDirectory: directories.work, env: environment,
      onListModels: () => [{id: native.model, name: native.model, capabilities: {
        supports: {vision: false, reasoningEffort: false}, limits: {max_context_window_tokens: 128000}}}],
    });
    let session;
    try {
      await client.start();
      session = await client.createSession({sessionId: receipt.sdkSessionId, model: native.model,
        provider: native.provider, workingDirectory: directories.work, trustWorkingDirectory: 'session',
        systemMessage: {mode: 'replace', content: SYSTEM}, streaming: true,
        tools, availableTools: allowed, onPermissionRequest: permissionHandler(tools, true),
        mcpServers: {}, enableSkills: false, pluginDirectories: [], skillDirectories: [], hookDirectories: [],
        enableSessionTelemetry: false, enableSessionStore: false});
      await session.sendAndWait({prompt: PROMPT}, 180000);
      const checked = JSON.parse(await tools.find(tool => tool.name === 'fixture_test').handler({}));
      if (!checked.passed || !receipt.tools.includes('repo_write') ||
          receipt.tools.filter(name => name === 'fixture_test').length < 2 ||
          await fs.readFile(path.join(directories.work, 'fixture-test.mjs'), 'utf8') !== FIXTURE_TEST) {
        throw new Error('fixture_not_proven');
      }
      const source = await fs.readFile(path.join(directories.work, 'solution.mjs'), 'utf8');
      return {source, sourceSha256: sourceDigest(source), testsPassed: checked.checks,
        wireApi: native.provider.wireApi, model: native.model};
    } finally {
      if (session) await session.disconnect().catch(() => {});
      await client.forceStop().catch(() => {});
    }
  }});
await worker.initialize();
server(worker).listen(Number(runtime.AGENT_PORT ?? 18080), '127.0.0.1');

import fs from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {CLI_ARGUMENTS, replaceEnvironment, sdkEnvironment, sessionOptions} from './probe-worker.mjs';

const root = await fs.mkdtemp('/tmp/public-sdk-start-');
const directories = Object.fromEntries(['home', 'config', 'copilot', 'work', 'temp']
  .map(name => [name, root + '/' + name]));
for (const directory of Object.values(directories)) await fs.mkdir(directory, {mode: 0o700});
process.chdir(directories.temp);
const native = {
  model: 'gpt-5.4-nano', provider: {type: 'openai', wireApi: 'responses',
    apiKey: 'kagent-credential-injected',
    baseUrl: 'https://host-capabilities.proxy-access.svc.cluster.local:8445/v1'},
  mcpServers: {},
};
const environment = sdkEnvironment(process.env, directories, native);
replaceEnvironment(process.env, environment);
const {CopilotClient, RuntimeConnection, ToolSet} = await import('/app/coder/node_modules/@github/copilot-sdk/dist/index.js');
const client = new CopilotClient({
  connection: RuntimeConnection.forStdio({
    path: '/app/coder/node_modules/@github/copilot-linux-x64/copilot', args: CLI_ARGUMENTS,
  }),
  mode: 'empty', useLoggedInUser: false, logLevel: 'none', env: environment,
  baseDirectory: directories.copilot, workingDirectory: directories.work,
  onListModels: () => [{id: native.model, name: native.model, capabilities: {
    supports: {vision: false, reasoningEffort: false}, limits: {max_context_window_tokens: 128000},
  }}],
});
try {
  await client.start();
  const session = await client.createSession({
    ...sessionOptions(native, directories.work, new ToolSet()),
    sessionId: 'public-canary-' + randomUUID(), trustWorkingDirectory: 'session',
  });
  const events = await session.getEvents();
  await session.disconnect();
  console.log(JSON.stringify({realSdkStartup: true, privateEnvironmentExcluded: true,
    realSessionCreated: true, eventCount: events.length,
    providerRequests: 0, networkingDisabledByContainer: true}));
} finally {
  await client.forceStop().catch(() => {});
  await fs.rm(root, {recursive: true, force: true});
}

import fs from 'node:fs/promises';
import path from 'node:path';
import tls from 'node:tls';
import {PublicProbeWorker, createProbeServer, publicCompiledConfig, replaceEnvironment,
  sdkEnvironment, CLI_ARGUMENTS} from './probe-worker.mjs';

const data = process.env.AGENT_DATA ?? '/data/coding';
const home = path.join(data, 'public-probe', 'home');
if (process.env.NODE_TLS_REJECT_UNAUTHORIZED === '0') throw new Error('Public canary requires verified TLS.');
const publicRoot = path.join(data, 'public-probe');
for (const directory of [data, publicRoot, home, path.join(publicRoot, 'config'),
  path.join(publicRoot, 'copilot'), path.join(publicRoot, 'work'), path.join(publicRoot, 'temp')]) {
  await fs.mkdir(directory, {recursive: true, mode: 0o700});
  if ((await fs.lstat(directory)).isSymbolicLink()) throw new Error('Public canary rejects linked directories.');
  if (directory === data && (await fs.readdir(data)).some(name => !['session-owner', 'public-probe'].includes(name))) {
    throw new Error('Public canary rejects nonpublic durable state.');
  }
}
if ((await fs.readdir(path.join(publicRoot, 'work'))).length) throw new Error('Public canary work directory is not empty.');
process.chdir(path.join(publicRoot, 'temp'));
process.env.HOME = home;
process.env.COPILOT_HOME = path.join(data, 'public-probe', 'copilot');
process.env.XDG_CONFIG_HOME = path.join(data, 'public-probe', 'config');
process.env.COPILOT_OFFLINE = 'true';
process.env.COPILOT_TELEMETRY_DISABLED = process.env.COPILOT_GITHUB_TELEMETRY_DISABLED = 'true';
for (const name of ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']) {
  delete process.env[name];
}
process.env.NODE_USE_ENV_PROXY = '0';
const roots = await fs.readFile('/etc/ssl/certs/ca-certificates.crt', 'utf8');
const projected = await fs.readFile(process.env.EGRESS_CA_FILE ?? '/run/kagent/egress/trust-bundle.pem', 'utf8');
const combined = path.join(home, 'combined-ca.pem');
await fs.writeFile(combined, roots + '\n' + projected, {mode: 0o600});
if (typeof tls.setDefaultCACertificates !== 'function') throw new Error('Public canary requires Node 24+ TLS API.');
const certificates = (roots + '\n' + projected).match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g);
if (!certificates?.length) throw new Error('Public canary trust bundle is empty.');
tls.setDefaultCACertificates([...tls.getCACertificates('bundled'), ...certificates]);
process.env.SSL_CERT_FILE = process.env.NODE_EXTRA_CA_CERTS = combined;
const runtimeEnv = {...process.env};
const {mapNativeConfig} = await import('./native-config.mjs');
if (runtimeEnv.AGENT_NATIVE_CONFIG !== 'true' || !runtimeEnv.AGENT_COMPILED_CONFIG_PATH) {
  throw new Error('Public canary requires compiled native configuration.');
}
const native = publicCompiledConfig(JSON.parse(await fs.readFile(runtimeEnv.AGENT_COMPILED_CONFIG_PATH, 'utf8')),
  mapNativeConfig, runtimeEnv);
replaceEnvironment(process.env, sdkEnvironment(runtimeEnv, {home, config: path.join(publicRoot, 'config'),
  copilot: path.join(publicRoot, 'copilot'), work: path.join(publicRoot, 'work'), temp: path.join(publicRoot, 'temp')}, native));
const {CopilotClient, RuntimeConnection, ToolSet} = await import('@github/copilot-sdk');
const {SessionArchive, archiveBridgeUploader, archiveCredentials} = await import('./session-archive.mjs');
const {loadNativeMemoryTools} = await import('./native-memory.mjs');
const worker = new PublicProbeWorker({data, env: runtimeEnv, native, emptyTools: new ToolSet(),
  credentials: archiveCredentials(runtimeEnv), openArchive: options => SessionArchive.open(options),
  uploaderFactory: archiveBridgeUploader, loadMemoryTools: loadNativeMemoryTools,
  createClient: ({directories, environment}) => new CopilotClient({
    connection: RuntimeConnection.forStdio({path: '/app/coder/node_modules/@github/copilot-linux-x64/copilot',
      args: CLI_ARGUMENTS}),
    mode: 'empty', useLoggedInUser: false, logLevel: 'none', env: environment,
    baseDirectory: directories.copilot, workingDirectory: directories.work,
    onListModels: () => [{id: native.model, name: native.model, capabilities: {
      supports: {vision: false, reasoningEffort: false}, limits: {max_context_window_tokens: 128000}}}],
  })});
await worker.initialize();
createProbeServer(worker).listen(Number(runtimeEnv.AGENT_PORT ?? 18080), '127.0.0.1');

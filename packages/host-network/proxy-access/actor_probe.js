'use strict';

const http = require('node:http');
const https = require('node:https');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const bootId = require('node:crypto').randomUUID();

const adapter = spawn(process.execPath, ['/opt/proxy/client.js'], {
  stdio: ['ignore', 'ignore', 'inherit'],
});
adapter.on('exit', () => process.exit(1));

function command(program, args, timeout = 120000, acceptedCodes = [0]) {
  return new Promise((resolve, reject) => {
    const child = spawn(program, args, {env: process.env, stdio: ['ignore', 'pipe', 'pipe']});
    const output = [];
    let size = 0;
    let diagnostic = '';
    const timer = setTimeout(() => child.kill('SIGKILL'), timeout);
    child.stdout.on('data', chunk => {
      size += chunk.length;
      if (size > 4000000) child.kill('SIGKILL');
      else output.push(chunk);
    });
    child.stderr.on('data', chunk => {
      const match = chunk.toString().match(/\b(?:NU\d{4}|EACCES|E401|E403|E404|ENOTFOUND|SELF_SIGNED_CERT_IN_CHAIN)\b/);
      if (match) diagnostic = match[0];
    });
    child.on('error', error => {clearTimeout(timer); reject(error);});
    child.on('exit', code => {
      clearTimeout(timer);
      if (!acceptedCodes.includes(code)) reject(new Error(`${program} failed with exit ${code} ${diagnostic}`.trim()));
      else resolve(Buffer.concat(output).toString());
    });
  });
}

let operation;
async function probe(live) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'proxy-actor-'));
  let stage = 'models';
  const completed = {};
  try {
    const models = JSON.parse(await command('curl', [
      '--silent', '--show-error', '--fail', '--max-time', '45',
      'http://127.0.0.1:14141/v1/models',
    ]));
    if (!Array.isArray(models.data) || !models.data.length) throw new Error('Missing model catalog');
    completed.models = models.data.length;
    stage = 'nuget_index';
    const feed = 'https://pkgs.dev.azure.com/msasg/engineering%20fundamentals/_packaging/EngSys_Consume/';
    const index = JSON.parse(await command('curl', [
      '--silent', '--show-error', '--fail', '--max-time', '60', feed + 'nuget/v3/index.json',
    ]));
    if (!index.resources?.length) throw new Error('Missing NuGet index');
    completed.nuget_resources = index.resources.length;
    stage = 'npm';
    const npm = await command('npm', [
      'view', 'typescript@7.0.2', 'version', '--registry', feed + 'npm/registry/',
      '--cache', path.join(work, 'npm-cache'), '--fetch-retries', '0',
    ]);
    if (npm.trim() !== '7.0.2') throw new Error('Unexpected npm package result');
    completed.npm_version = npm.trim();
    stage = 'nuget_restore';
    fs.writeFileSync(path.join(work, 'Restore.csproj'),
      '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup>' +
      '<ItemGroup><PackageReference Include="Microsoft.Identity.Client" Version="4.91.0" /></ItemGroup></Project>');
    fs.writeFileSync(path.join(work, 'NuGet.Config'),
      '<configuration><packageSources><clear/><add key="EngSys" value="' +
      feed + 'nuget/v3/index.json"/></packageSources></configuration>');
    await command('dotnet', [
      'restore', path.join(work, 'Restore.csproj'), '--configfile', path.join(work, 'NuGet.Config'),
      '--packages', path.join(work, 'nuget-cache'), '--verbosity', 'quiet',
    ], 180000);
    completed.cold_nuget_restore = true;
    const denied = {};
    for (const [name, url] of [
      ['public_npm', 'https://registry.npmjs.org/typescript'],
      ['ado_rest', 'https://dev.azure.com/msasg/_apis/projects?api-version=7.1'],
    ]) {
      stage = name;
      const statuses = (await command('curl', [
        '--silent', '--show-error', '--max-time', '30', '--output', '/dev/null',
        '--write-out', '%{http_code}/%{http_connect}', url,
      ], 40000, [0, 7, 56])).trim().split('/');
      denied[name] = statuses.includes('403') ? '403' : statuses.join('/');
      if (denied[name] !== '403') throw new Error(`${name} was not denied: ${denied[name]}`);
      completed[name] = denied[name];
    }
    let inference = false;
    if (live) {
      stage = 'inference';
      const stream = await command('curl', [
        '--silent', '--show-error', '--fail', '--max-time', '120',
        '-H', 'Content-Type: application/json', '--data',
        JSON.stringify({model: 'gpt-5.6-sol', input: 'Reply with PROXY_OK only.',
          max_output_tokens: 256, store: false, stream: true}),
        'http://127.0.0.1:14141/v1/responses',
      ], 130000);
      if (!stream.includes('response.completed') || !stream.includes('PROXY_OK')) {
        throw new Error('Inference stream did not complete with the fixture marker');
      }
      inference = true;
    }
    return {result: 'PASS', boot_id: bootId, models: models.data.length, nuget_resources: index.resources.length,
      npm_version: npm.trim(), cold_nuget_restore: true, denied, live_inference: inference};
  } catch (error) {
    error.stage = stage;
    error.completed = completed;
    throw error;
  } finally {
    fs.rmSync(work, {recursive: true, force: true});
  }
}

http.createServer((request, response) => {
  if (request.url === '/readyz') {
    response.end('ready\n');
    return;
  }
  if (request.url === '/identity-check') {
    const upstream = https.request(process.env.GATEWAY_SESSION_URL, {
      method: 'POST',
      ca: [fs.readFileSync(process.env.GATEWAY_CA_FILE), fs.readFileSync(process.env.EGRESS_CA_FILE)],
      headers: {Authorization: 'Bearer injected-by-substrate', 'Content-Length': '2',
        'Content-Type': 'application/json'},
    }, result => {
      result.resume();
      response.writeHead(200, {'Content-Type': 'application/json'});
      response.end(JSON.stringify({session_status: result.statusCode, boot_id: bootId}));
    });
    upstream.on('error', () => response.writeHead(502).end());
    upstream.end('{}');
    return;
  }
  if (request.url === '/model-check') {
    command('curl', ['--silent', '--show-error', '--fail', '--max-time', '45',
      'http://127.0.0.1:14141/v1/models']).then(output => {
      response.writeHead(200, {'Content-Type': 'application/json'});
      response.end(JSON.stringify({models: JSON.parse(output).data.length, boot_id: bootId}));
    }).catch(() => response.writeHead(502).end());
    return;
  }
  if (request.url !== '/run' && request.url !== '/run?live=1') {
    response.writeHead(404).end();
    return;
  }
  operation ??= probe(request.url.includes('live=1')).catch(error => ({
    result: 'FAIL', stage: error.stage, error: error.message, completed: error.completed,
  }));
  operation.then(result => {
    response.writeHead(result.result === 'PASS' ? 200 : 500, {'Content-Type': 'application/json'});
    response.end(JSON.stringify(result));
  });
}).listen(80, '0.0.0.0');

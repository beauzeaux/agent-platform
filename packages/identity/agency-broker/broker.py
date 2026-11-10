"""Host-only Agency acquisition and actor-bound MCP execution."""
import base64
import argparse
import contextlib
import hashlib
import http.server
import io
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import secrets
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from prepare_runtime import token

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
PROFILES = ROOT / "profiles.json"
GLOBAL_CONFIG = ROOT / "global-config"
AGENCY = r"C:\.Tools\agency\2026.10.29.2\agency.exe"
POLICY = "agency-profile-v3"
CONFIG_RESOLUTION = "native-cwd-ancestor-v1"
CONFIG_FILENAMES = ("agency.toml", "agency.yaml")
PROJECT = "Engineering Fundamentals"
PROJECT_ID = "b03927a9-4d41-4a29-865d-b1d980f6dee9"
REPOSITORY_ID = "b42806f3-3a50-4b92-918d-9e87c616e79e"
MAX_BODY = 8_000_000
MAX_CONFIG_BYTES = 1_000_000
MAX_CONFIG_FILE_BYTES = 100_000
MAX_CONFIG_SOURCES = 32
PLUGIN_CACHE = {}
PLUGIN_CACHE_LOCK = threading.Lock()
PLUGIN_MARKETS = {"agency-microsoft/playground", "agency-microsoft/curated",
                  "agency-microsoft/.github-private"}
REVIEWED_HOOK_SPEC = "market:AIX-ES-Standards@agency-microsoft/.github-private"
REVIEWED_HOOK_SPECS = {
    REVIEWED_HOOK_SPEC,
    "market:AIX-ES-Standards@agency-microsoft/playground",
}
REVIEWED_HOOK_FILES = {
    "hooks/hooks.json": "3e292aa86f781be247e51fd1df3fd5bd8ee47f26b8d1d821dbc3cdabfe249957",
    "hooks/session-start.cjs": "d1867a6d16ce18c7b75bf162c0bddd48be9a6a98092ce7d424a988a4aacacd5e",
    "plugin.json": "4f5d7ba4df619d38b1b911dfbc18cc1da07f46ada8d507dead7dc187ff9a6c2d",
}
REVIEWED_HOOK_MANIFESTS = {
    "e474d7d65579513d42c988e7e333d596783f2ccd183a8b0fbafebf7cf66b5252",
    "1c4a153b37851b8fca4e804e9e051df564ca4aa27d3ae892a090912a5843c824",
}
ALLOWED_TOOLS = {
    "ado": {"core_list_projects", "core_list_project_teams", "repo_repository",
            "repo_branch", "repo_file", "repo_pull_request", "repo_pull_request_thread",
            "repo_search_commits", "wit_query", "wit_work_item", "search_wiki",
            "search_workitem", "wiki", "repo_create_branch", "repo_pull_request_write",
            "repo_pull_request_thread_write", "wit_work_item_write", "wit_work_item_comment_write",
            "wit_work_item_link_write", "wit_work_item_attachment_link_write"},
    "watch": {"finish_pull_request", "check_pull_request_feedback"},
    "bluebird": {"code_history", "code_search", "code_read", "code_graph", "project_search", "metadata"},
    "enghub": {"search", "fetch", "get_source_link", "resolve_service", "get_service_nodes", "get_node_tree"},
    "workiq": {"ask", "fetch", "fetch_blob", "get_schema", "retrieve", "search_paths", "list_agents"},
}


class Denied(Exception):
    pass


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def ado_write_scope(tool, arguments, repository=None):
    repository = repository or {"organization": "msasg", "project": PROJECT, "projectId": PROJECT_ID,
                                "name": "engsys", "id": REPOSITORY_ID}
    project, project_id = repository["project"], repository["projectId"]
    if not (tool.endswith("_write") or tool == "repo_create_branch"):
        return
    if arguments.get("project") not in {None, project, project_id}:
        raise Denied("ado_project_not_allowed")
    if tool.startswith("repo_"):
        if arguments.get("repositoryId") not in {repository["name"], repository["id"]}:
            raise Denied("ado_repository_not_allowed")
        arguments["repositoryId"] = repository["id"]
        arguments["project"] = project
        targets=repository.get("allowedTargetBranches")
        prefixes=repository.get("allowedSourcePrefixes")
        if tool=="repo_pull_request_write" and targets and prefixes:
            if arguments.get("action")=="create":
                if arguments.get("targetRefName") not in targets or not any(
                    str(arguments.get("sourceRefName","")).startswith(prefix) for prefix in prefixes):
                    raise Denied("pull_request_branch_not_allowed")
            elif arguments.get("pullRequestId") is not None:
                request=urllib.request.Request(
                    "https://dev.azure.com/"+urllib.parse.quote(repository["organization"],safe="")+
                    "/"+urllib.parse.quote(project,safe="")+"/_apis/git/repositories/"+
                    urllib.parse.quote(repository["id"],safe="")+"/pullRequests/"+
                    str(arguments["pullRequestId"])+"?api-version=7.1",
                    headers={"Authorization":"Bearer "+token()})
                with urllib.request.urlopen(request,timeout=30) as response:
                    pull_request=json.load(response)
                if pull_request.get("targetRefName") not in targets or not any(
                    str(pull_request.get("sourceRefName","")).startswith(prefix) for prefix in prefixes):
                    raise Denied("pull_request_branch_not_allowed")
            else:
                raise Denied("pull_request_identity_required")
        return
    if not tool.startswith("wit_"):
        raise Denied("ado_write_scope_not_supported")
    if arguments.get("action") != "update_batch":
        arguments["project"] = project
    ids = {arguments[key] for key in ["id", "workItemId", "parentId"]
           if arguments.get(key) is not None}
    batch = arguments.get("batchUpdates") or []
    if not isinstance(batch, list) or len(batch) > 20:
        raise Denied("work_item_batch_limit")
    ids.update(item.get("id") for item in batch if isinstance(item, dict))
    if any(not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0 for identifier in ids):
        raise Denied("invalid_work_item_id")
    for identifier in ids:
        request = urllib.request.Request(
            f"https://dev.azure.com/{urllib.parse.quote(repository['organization'], safe='')}/_apis/wit/workitems/{identifier}?fields=System.TeamProject&api-version=7.1",
            headers={"Authorization": "Bearer " + token()})
        with urllib.request.urlopen(request, timeout=30) as response:
            item = json.load(response)
        if item["fields"]["System.TeamProject"] != project:
            raise Denied("work_item_project_not_allowed")
    for update in [*(arguments.get("updates") or []), *batch, *(arguments.get("fields") or [])]:
        if not isinstance(update, dict):
            raise Denied("invalid_work_item_update")
        field = update.get("path") or update.get("name")
        if field in {"/fields/System.TeamProject", "System.TeamProject"} and update.get("value") != project:
            raise Denied("work_item_project_not_allowed")


def watch_scope(tool, arguments, repository=None):
    repository = repository or {"organization":"msasg","project":PROJECT,"name":"engsys"}
    if tool not in {"finish_pull_request","check_pull_request_feedback"}:
        raise Denied("watch_tool_not_allowed")
    value = arguments.get("pull_request")
    if not isinstance(value,str):
        raise Denied("explicit_pull_request_url_required")
    url = urllib.parse.urlparse(value)
    if url.scheme != "https" or url.username or url.password or url.port not in {None,443} or url.fragment:
        raise Denied("pull_request_url_not_allowed")
    segments = [urllib.parse.unquote(part) for part in url.path.split("/") if part]
    host = (url.hostname or "").lower()
    if host == "dev.azure.com":
        expected = [repository["organization"],repository["project"],"_git",repository["name"],"pullrequest"]
    elif host == repository["organization"]+".visualstudio.com":
        expected = [repository["project"],"_git",repository["name"],"pullrequest"]
    else:
        raise Denied("pull_request_url_not_allowed")
    if segments[:len(expected)] != expected or len(segments) != len(expected)+1 or not segments[-1].isdigit():
        raise Denied("pull_request_url_not_allowed")
    targets=repository.get("allowedTargetBranches")
    prefixes=repository.get("allowedSourcePrefixes")
    if targets and prefixes:
        request=urllib.request.Request(
            "https://dev.azure.com/"+urllib.parse.quote(repository["organization"],safe="")+
            "/"+urllib.parse.quote(repository["project"],safe="")+"/_apis/git/repositories/"+
            urllib.parse.quote(repository.get("id",repository["name"]),safe="")+"/pullRequests/"+
            segments[-1]+"?api-version=7.1",
            headers={"Authorization":"Bearer "+token()})
        with urllib.request.urlopen(request,timeout=30) as response:
            pull_request=json.load(response)
        if pull_request.get("targetRefName") not in targets or not any(
            str(pull_request.get("sourceRefName","")).startswith(prefix) for prefix in prefixes):
            raise Denied("pull_request_branch_not_allowed")


def isolated_global_config_directory():
    configured = Path(GLOBAL_CONFIG)
    if configured.is_file():
        configured = STATE / "global-config"
    configured.mkdir(parents=True, exist_ok=True)
    if not configured.is_dir():
        raise Denied("invalid_global_config_directory")
    if any(configured.joinpath(name).exists() for name in CONFIG_FILENAMES):
        raise Denied("global_config_must_be_empty")
    return configured


def agency_config_env():
    env = dict(os.environ)
    env["AGENCY_GLOBAL_CONFIG_PATH"] = str(isolated_global_config_directory())
    env["AGENCY_NO_CONFIG_CACHE"] = "true"
    env["AGENCY_NO_UPDATE_CHECK"] = "true"
    env["AGENCY_SESSION_SUBPROCESS"] = "true"
    env["AGENCY_TELEMETRY_FLUSH_CHILD"] = "true"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["NO_COLOR"] = "1"
    for name in ["SESSION_CATEGORIZATION", "SESSION_ANALYSIS", "PROMPT_CATEGORIZATION"]:
        env["AGENCY_" + name + "_DISABLED"] = "true"
    return env


def host_env():
    env = agency_config_env()
    # These tokens exist only in the trusted host process and its Agency children.
    env["AGENCY_ADO_TOKEN"] = token()
    env["AGENCY_TOKEN_ADO_MSASG"] = env["AGENCY_ADO_TOKEN"]
    return env


def agency(args, cwd, env, input_bytes=None):
    result = subprocess.run([AGENCY, *args, "--no-aec"], cwd=cwd, env=env, input=input_bytes,
                            capture_output=True, timeout=180,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or len(result.stdout) > MAX_BODY:
        raise Denied("agency_command_failed")
    return result.stdout


def validate_archive(data, spec=None):
    if len(data) > 4_000_000:
        raise Denied("plugin_archive_limit")
    files = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            name = entry.orig_filename
            parts = PurePosixPath(name).parts
            total += entry.file_size
            mode = entry.external_attr >> 16
            if (not parts or name.startswith("/") or "\\" in name or ":" in name or "\x00" in name
                    or any(part in {".", ".."} for part in name.split("/"))
                    or mode & 0o170000 == 0o120000 or total > 8_000_000
                    or len(files) >= 512 or name.lower() in {n.lower() for n in files}):
                raise Denied("unsafe_plugin_archive")
            if entry.is_dir():
                continue
            if parts[-1].lower() == ".mcp.json":
                raise Denied("unapproved_plugin_execution")
            files[name] = archive.read(entry)
    hook_files = [name for name in files if name.split("/")[0].lower() == "hooks" or name.endswith("hooks.json")]
    approved_hook = (spec in REVIEWED_HOOK_SPECS and
                     set(hook_files) == {"hooks/hooks.json", "hooks/session-start.cjs"} and
                     all(name in files and (hashlib.sha256(files[name]).hexdigest() == digest
                         or name == "plugin.json" and hashlib.sha256(files[name]).hexdigest() in REVIEWED_HOOK_MANIFESTS)
                         for name, digest in REVIEWED_HOOK_FILES.items()))
    if hook_files and not approved_hook:
        raise Denied("unapproved_plugin_execution")
    for name in ["agency.json", "plugin.json", ".claude-plugin/plugin.json", ".copilot-plugin/plugin.json"]:
        if name not in files:
            continue
        metadata = json.loads(files[name])
        if any(metadata.get(key) for key in ["artifacts", "mcpServers", "lspServers", "extensions"]):
            raise Denied("plugin_requires_unapproved_preparation")
        if metadata.get("hooks") and not (approved_hook and name == "plugin.json"):
            raise Denied("unapproved_plugin_execution")
        if metadata.get("source") or metadata.get("mcpAuth"):
            raise Denied("plugin_requires_unapproved_preparation")
        if metadata.get("engines") and "copilot" not in metadata["engines"]:
            raise Denied("plugin_engine_not_supported")
        if metadata.get("platforms") and "linux" not in metadata["platforms"]:
            raise Denied("plugin_platform_not_supported")
    return files


def check_spec(spec, markets=None):
    if not isinstance(spec, str) or not re.fullmatch(
            r"market:[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", spec):
        raise Denied("plugin_source_not_allowed")
    if spec.split("@", 1)[1] not in (markets if markets is not None else PLUGIN_MARKETS):
        raise Denied("plugin_marketplace_not_allowed")


def normalize_working_directory(value):
    if value is None or value == ".":
        return "."
    if (not isinstance(value, str) or not value or len(value) > 512 or
            value.startswith("/") or "\\" in value or ":" in value or "\x00" in value or
            any(ord(character) < 32 or 127 <= ord(character) < 160 for character in value)):
        raise Denied("invalid_working_directory")
    segments = value.split("/")
    if (len(segments) > MAX_CONFIG_SOURCES - 1 or
            any(segment in {"", ".", ".."} or segment.endswith((".", " ")) for segment in segments)):
        raise Denied("invalid_working_directory")
    return "/".join(segments)


def validate_commit(value, error):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{40}", value):
        raise Denied(error)
    return value


def ado_repository_url(repository):
    return ("https://dev.azure.com/" + urllib.parse.quote(repository["organization"], safe="") +
            "/" + urllib.parse.quote(repository["project"], safe="") + "/_apis/git/repositories/" +
            urllib.parse.quote(repository.get("id", repository["name"]), safe=""))


def validate_repository_commit(repository, commit, token_value):
    url = ado_repository_url(repository) + "/commits/" + commit + "?api-version=7.1"
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token_value})
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response)
    if value.get("commitId") != commit:
        raise Denied("repository_revision_mismatch")


def fetch_repository_text(repository, commit, path, token_value):
    url = ado_repository_url(repository) + "/items?" + urllib.parse.urlencode({
        "path": "/" + path, "api-version": "7.1",
        "versionDescriptor.versionType": "commit",
        "versionDescriptor.version": commit, "$format": "text"})
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token_value})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(MAX_CONFIG_FILE_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if len(data) > MAX_CONFIG_FILE_BYTES:
        raise Denied("repository_configuration_limit")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise Denied("invalid_repository_config_encoding") from error


def config_ancestors(working_directory):
    if working_directory == ".":
        return [""]
    segments = working_directory.split("/")
    return [""] + ["/".join(segments[:index]) for index in range(1, len(segments) + 1)]


def config_path(directory, filename):
    return filename if not directory else directory + "/" + filename


def repository_config_sources(repository, commit, working_directory, token_value):
    sources = []
    total = 0
    for directory in config_ancestors(working_directory):
        text = None
        selected_path = None
        for filename in CONFIG_FILENAMES:
            candidate = config_path(directory, filename)
            text = fetch_repository_text(repository, commit, candidate, token_value)
            if text is not None:
                selected_path = candidate
                break
        if selected_path is None:
            continue
        total += len(text.encode())
        if total > MAX_CONFIG_BYTES or len(sources) >= MAX_CONFIG_SOURCES:
            raise Denied("repository_configuration_limit")
        sources.append({
            "type": "file", "path": selected_path,
            "format": "toml" if selected_path.endswith(".toml") else "yaml",
            "commit": commit, "sha256": hashlib.sha256(text.encode()).hexdigest(), "content": text})
    return sources


def materialize_config_sources(stage, working_directory, sources):
    (stage / ".git").mkdir(exist_ok=True)
    cwd = stage if working_directory == "." else stage.joinpath(*working_directory.split("/"))
    cwd.mkdir(parents=True, exist_ok=True)
    for source in sources:
        target = stage.joinpath(*source["path"].split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source["content"], encoding="utf-8")
    return cwd


def agency_json(args, cwd, env, input_value=None):
    input_bytes = canonical_json(input_value) if input_value is not None else None
    output = agency(args, cwd, env, input_bytes=input_bytes)
    try:
        value = json.loads(output.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Denied("invalid_agency_config_response") from error
    if not isinstance(value, dict):
        raise Denied("invalid_agency_config_response")
    return value, output


def native_config_layer(source):
    resolved_ref = source.get("resolvedRef") if source["type"] == "remote_config" else source["commit"]
    if not isinstance(resolved_ref, str) or not resolved_ref or len(resolved_ref) > 512:
        raise Denied("invalid_repository_config_source")
    return {
        "format": source["format"],
        "content": source["content"],
        "source": source.get("sourceLabel", source["path"]),
        "resolved_ref": resolved_ref,
    }


def merge_native_config(layers, cwd, env):
    return agency_json(
        ["service-fn", "merge", "configs"], cwd, env,
        {"layers": [native_config_layer(layer) for layer in layers]})


def validate_remote_pointer(pointer):
    if not isinstance(pointer, dict) or set(pointer) - {"git", "branch", "tag", "rev", "path"}:
        raise Denied("invalid_remote_config_pointer")
    git = pointer.get("git")
    path = pointer.get("path")
    if (not isinstance(git, str) or not git or len(git) > 2048 or
            path is not None and (not isinstance(path, str) or not path or len(path) > 512)):
        raise Denied("invalid_remote_config_pointer")
    refs = [(kind, pointer[kind]) for kind in ("branch", "tag", "rev") if kind in pointer]
    if len(refs) > 1 or refs and (not isinstance(refs[0][1], str) or
                                  not refs[0][1] or len(refs[0][1]) > 512):
        raise Denied("invalid_remote_config_pointer")
    return git, path, refs[0] if refs else (None, None)


def fetch_native_remote_config(pointer, declared_by, cwd, env):
    git, path, (ref_type, requested_ref) = validate_remote_pointer(pointer)
    args = ["service-fn", "fetch", "config", "--git", git]
    if ref_type is not None:
        args.extend(["--" + ref_type, requested_ref])
    if path is not None:
        args.extend(["--path", path])
    fetched, _ = agency_json(args, cwd, env)
    layer = fetched.get("layer")
    additional = fetched.get("additional_configs")
    if (not isinstance(layer, dict) or not isinstance(additional, list) or additional):
        raise Denied("transitive_remote_config_not_allowed")
    config_format = layer.get("format")
    content = layer.get("content")
    resolved_ref = layer.get("resolved_ref")
    if (config_format not in {"toml", "yaml"} or not isinstance(content, str) or
            len(content.encode()) > MAX_CONFIG_FILE_BYTES or not isinstance(resolved_ref, str) or
            not resolved_ref or len(resolved_ref) > 512):
        raise Denied("invalid_remote_config_source")
    actual_path = path.lstrip("/") if path is not None else (
        "agency.toml" if config_format == "toml" else "agency.yaml")
    if not actual_path:
        raise Denied("invalid_remote_config_source")
    return {
        "type": "remote_config",
        "declaredBy": declared_by,
        "git": git,
        "path": actual_path,
        "format": config_format,
        "requestedRef": requested_ref,
        "refType": ref_type,
        "resolvedRef": resolved_ref,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "sourceLabel": git + "@" + resolved_ref + ":/" + actual_path,
        "content": content,
    }


def authoritative_native_layers(sources, cwd, env):
    layers = []
    total_bytes = 0
    for source in sources:
        total_bytes += len(source["content"].encode())
        source_config, _ = merge_native_config([source], cwd, env)
        remote_config = source_config.get("remote_config", [])
        if not isinstance(remote_config, list):
            raise Denied("invalid_remote_config_pointer")
        for pointer in remote_config:
            remote = fetch_native_remote_config(pointer, source["path"], cwd, env)
            total_bytes += len(remote["content"].encode())
            if total_bytes > MAX_CONFIG_BYTES or len(layers) + 1 >= MAX_CONFIG_SOURCES:
                raise Denied("repository_configuration_limit")
            layers.append(remote)
        if len(layers) >= MAX_CONFIG_SOURCES:
            raise Denied("repository_configuration_limit")
        layers.append(source)
    if total_bytes > MAX_CONFIG_BYTES:
        raise Denied("repository_configuration_limit")
    return layers


def resolve_native_config(stage, working_directory, sources, env):
    cwd = materialize_config_sources(stage, working_directory, sources)
    for ancestor in stage.parents:
        if any(ancestor.joinpath(name).is_file() for name in CONFIG_FILENAMES):
            raise Denied("ambient_repository_config_detected")
    effective_text = agency(["config", "list"], cwd, env).decode("utf-8-sig")
    try:
        discovered = tomllib.loads(effective_text)
    except tomllib.TOMLDecodeError as error:
        raise Denied("invalid_effective_repository_config") from error
    layers = authoritative_native_layers(sources, cwd, env)
    effective, merged_text = merge_native_config(layers, cwd, env)
    if effective != discovered:
        raise Denied("agency_native_resolution_mismatch")
    return effective, merged_text.decode("utf-8-sig"), layers


def plugin_request_contract(body):
    if not isinstance(body, dict):
        raise Denied("invalid_plugin_request")
    legacy = "configText" in body or "configCommit" in body
    allowed = {"commit", "configCommit", "configText"} if legacy else {"commit", "workingDirectory"}
    if set(body) - allowed:
        raise Denied("invalid_plugin_request")
    commit = validate_commit(body.get("commit"), "invalid_repository_revision")
    if legacy:
        config_commit = validate_commit(body.get("configCommit", commit), "invalid_configuration_revision")
        text = body.get("configText")
        if not isinstance(text, str) or len(text.encode()) > MAX_CONFIG_FILE_BYTES:
            raise Denied("invalid_repository_config")
        return "legacy-root-v1", commit, config_commit, ".", text
    return CONFIG_RESOLUTION, commit, commit, normalize_working_directory(
        body.get("workingDirectory")), None


def authoritative_config_request(repository, commit, config_commit, working_directory, text, token_value):
    if text is not None:
        authoritative = fetch_repository_text(repository, config_commit, "agency.toml", token_value)
        if authoritative is None or authoritative != text.lstrip("\ufeff"):
            raise Denied("repository_configuration_mismatch")
        return [{
            "type": "file", "path": "agency.toml", "format": "toml", "commit": config_commit,
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "content": text,
        }]
    return repository_config_sources(repository, commit, working_directory, token_value)


def public_config_sources(sources):
    file_keys = ("type", "path", "format", "commit", "sha256")
    remote_keys = ("type", "declaredBy", "git", "path", "format", "requestedRef", "refType",
                   "resolvedRef", "sha256")
    return [{key: source[key] for key in (file_keys if source["type"] == "file" else remote_keys)
             if source.get(key) is not None}
            for source in sources]


def prepare_plugins(body, profile=None):
    profile = profile or json.loads(PROFILES.read_text())["engsys-contributor"]
    repository = profile["repository"]
    resolution, commit, config_commit, working_directory, legacy_text = plugin_request_contract(body)
    env = host_env()
    validate_repository_commit(repository, commit, env["AGENCY_ADO_TOKEN"])
    sources = authoritative_config_request(
        repository, commit, config_commit, working_directory, legacy_text, env["AGENCY_ADO_TOKEN"])
    STATE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="config-", dir=STATE) as temporary:
        stage = Path(temporary)
        effective, effective_text, layers = resolve_native_config(stage, working_directory, sources, env)
        if effective.get("mcps") or effective.get("profiles"):
            raise Denied("effective_configuration_requires_policy_review")
        specs = effective.get("plugins", {}).get("default", [])
        if not isinstance(specs, list) or not all(isinstance(spec, str) for spec in specs):
            raise Denied("invalid_effective_plugin_configuration")
        bundles, active, seen = [], set(), set()

        def acquire(spec):
            check_spec(spec, profile["pluginMarketplaces"])
            if spec in active:
                raise Denied("plugin_dependency_cycle")
            if spec in seen:
                return
            if len(seen) + len(active) >= 16:
                raise Denied("plugin_dependency_limit")
            active.add(spec)
            encoded = agency(["fetch", "--plugin", spec, "--engine", "copilot"], stage, env)
            data = base64.b64decode(encoded, validate=True)
            files = validate_archive(data, spec)
            metadata = json.loads(files.get("agency.json", b"{}"))
            dependencies = metadata.get("dependencies") or []
            if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
                raise Denied("unsupported_dependency_metadata")
            for dependency in dependencies:
                acquire(dependency)
            active.remove(spec)
            seen.add(spec)
            bundles.append({"spec": spec, "sha256": hashlib.sha256(data).hexdigest(),
                            "archive": encoded.decode().strip(), "files": sorted(files)})

        for spec in specs:
            acquire(spec)
    manifest = {"policy": POLICY, "profileSha256": hashlib.sha256(canonical_json(profile)).hexdigest(),
                "agencyVersion": "2026.10.29.2", "repositoryCommit": commit,
                "configResolution": resolution, "workingDirectory": working_directory,
                "configSourceCommit": config_commit, "configSources": public_config_sources(layers),
                "effectiveConfigSha256": hashlib.sha256(effective_text.encode()).hexdigest(),
                "effectiveConfig": effective, "plugins": [
                    {key: value for key, value in bundle.items() if key != "archive"}
                    for bundle in bundles]}
    if legacy_text is not None:
        manifest["configSha256"] = hashlib.sha256(legacy_text.encode()).hexdigest()
    manifest["identity"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    return {"manifest": manifest, "bundles": bundles}


def cacheable_plugin_preparation(prepared):
    sources = prepared.get("manifest", {}).get("configSources", [])
    return all(
        source.get("type") != "remote_config" or
        source.get("refType") == "rev" and
        re.fullmatch("[0-9a-f]{40}", source.get("requestedRef", "")) is not None and
        source.get("resolvedRef") == source.get("requestedRef")
        for source in sources if isinstance(source, dict))


def cached_prepare_plugins(body, profile):
    resolution, commit, config_commit, working_directory, text = plugin_request_contract(body)
    key = hashlib.sha256(canonical_json([
        profile, resolution, commit, config_commit, working_directory, text])).hexdigest()
    with PLUGIN_CACHE_LOCK:
        cached=PLUGIN_CACHE.get(key)
    if cached is not None:
        return cached
    prepared=prepare_plugins(body,profile)
    if cacheable_plugin_preparation(prepared):
        with PLUGIN_CACHE_LOCK:
            if len(PLUGIN_CACHE)>=16:
                PLUGIN_CACHE.pop(next(iter(PLUGIN_CACHE)))
            PLUGIN_CACHE[key]=prepared
    return prepared


def result_json(result):
    if result.get("isError"):
        raise Denied("host_tool_failed")
    if "structuredContent" in result:
        return result["structuredContent"]
    values = []
    for content in result.get("content", []):
        if content.get("type") == "text":
            try:
                values.append(json.loads(content["text"]))
            except ValueError:
                pass
    return values[0] if len(values) == 1 else values


class Gateway:
    def __init__(self, uid=None):
        STATE.mkdir(parents=True, exist_ok=True)
        self.uid = uid
        self.lock = threading.Lock()
        self.pending = {}
        self.counter = 0
        self.instances = {}
        self.last_used = time.monotonic()
        self.process = subprocess.Popen(
            [AGENCY, "mcp", "gateway", "--transport", "stdio", "--no-aec"],
            cwd=STATE, env=host_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self.read, daemon=True).start()
        self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                "clientInfo": {"name": "agency-host-broker", "version": "0.1.0"}})
        with self.lock:
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def read(self):
        try:
            while line := self.process.stdout.readline(MAX_BODY + 1):
                if len(line) > MAX_BODY:
                    break
                message = json.loads(line)
                if "id" in message and ("result" in message or "error" in message):
                    with self.lock:
                        pending = self.pending.get(message["id"])
                    if pending:
                        pending.put_nowait(message)
        except (ValueError, OSError, queue.Full):
            pass
        finally:
            with self.lock:
                for pending in self.pending.values():
                    try:
                        pending.put_nowait({"error": {"code": -32000, "message": "gateway_disconnected"}})
                    except queue.Full:
                        pass

    def send(self, message):
        self.process.stdin.write(canonical_json(message) + b"\n")
        self.process.stdin.flush()

    def rpc(self, method, params, session=None, request_id=None, timeout=180):
        response = queue.Queue(maxsize=1)
        with self.lock:
            self.counter += 1
            identifier = self.counter
            self.pending[identifier] = response
            if session is not None and request_id is not None:
                session.inflight[request_id] = identifier
            self.send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
        try:
            try:
                message = response.get(timeout=timeout)
            except queue.Empty:
                with self.lock:
                    self.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": {"requestId": identifier}})
                raise Denied("gateway_timeout")
            if "error" in message:
                raise Denied("gateway_protocol_error")
            self.last_used = time.monotonic()
            return message["result"]
        finally:
            with self.lock:
                self.pending.pop(identifier, None)
                if session is not None and request_id is not None:
                    session.inflight.pop(request_id, None)

    def cancel(self, session, request_id=None):
        with self.lock:
            identifiers = list(session.inflight.values()) if request_id is None else [session.inflight.get(request_id)]
            for identifier in identifiers:
                if identifier is not None:
                    self.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": {"requestId": identifier}})

    def call(self, name, arguments, session=None, request_id=None):
        instances = session.instances if session else self.instances
        profile = session.profile if session else json.loads(PROFILES.read_text())["engsys-contributor"]
        if not isinstance(arguments, dict):
            raise Denied("invalid_gateway_arguments")
        if name == "load_toolset":
            if set(arguments) - {"toolset_id", "toolset_args"}:
                raise Denied("invalid_gateway_arguments")
            toolset = arguments.get("toolset_id")
            args = arguments.get("toolset_args", {})
            configured = profile["toolsets"].get(toolset)
            if not configured or not isinstance(args, dict):
                raise Denied("toolset_not_allowed")
            if args not in configured["instances"]:
                raise Denied("toolset_arguments_not_allowed")
            result = self.rpc("tools/call", {"name": name, "arguments": arguments}, session, request_id)
            value = result_json(result)
            expected = toolset + ("::" + ",".join(f"{key}={value}" for key, value in sorted(args.items())) if args else "")
            texts = "\n".join(c.get("text", "") for c in result.get("content", []))
            if (re.search(r"toolset_id:\s*" + re.escape(expected) + r"(?:\s|$)", texts)
                    or f'toolset_id "{expected}" is already loaded' in texts):
                instances[expected] = toolset
            if isinstance(value, dict):
                identifier = value.get("toolset_id") or value.get("instance_id") or value.get("toolset_instance_id")
                if identifier:
                    instances[identifier] = toolset
            return result
        if name in {"call_tool", "get_tool_schema"}:
            expected_keys = {"toolset_id", "tool_name", *({"arguments"} if name == "call_tool" else set())}
            if set(arguments) != expected_keys:
                raise Denied("invalid_gateway_arguments")
            identifier = arguments.get("toolset_id")
            toolset = instances.get(identifier)
            tool = arguments.get("tool_name")
            if not toolset or tool not in profile["toolsets"][toolset]["tools"]:
                raise Denied("nested_tool_not_allowed")
            nested = arguments.get("arguments", {})
            if not isinstance(nested, dict):
                raise Denied("invalid_nested_arguments")
            if toolset == "ado" and name == "call_tool":
                organization = profile["repository"]["organization"]
                if any(nested.get(key) not in {None, organization} for key in ["orgName", "organization", "org"]):
                    raise Denied("nested_organization_not_allowed")
                ado_write_scope(tool, nested, profile["repository"])
            if toolset == "watch" and name == "call_tool":
                watch_scope(tool,nested,profile["repository"])
            if toolset == "bluebird" and name == "call_tool":
                if nested.get("organization") not in {None, profile["repository"]["organization"]}:
                    raise Denied("bluebird_organization_not_allowed")
                if tool == "code_graph" and nested.get("method") in {"query", "cypher", "custom"}:
                    raise Denied("custom_graph_query_requires_review")
        elif name not in {"search_tools", "list_toolsets", "get_toolsets", "get_toolset_info"}:
            raise Denied("gateway_tool_not_allowed")
        timeout=1900 if name=="call_tool" and instances.get(arguments.get("toolset_id"))=="watch" else 180
        return self.rpc("tools/call",{"name":name,"arguments":arguments},session,request_id,timeout)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in [self.process.stdin, self.process.stdout]:
            stream.close()


class Broker(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, settings, settings_file=None):
        super().__init__(address, Handler)
        self.settings = settings
        self.settings_file = settings_file
        self.sessions = {}
        self.gateway = None
        self.profiles = json.loads(PROFILES.read_text())
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(4)
        self.plugin_slots = threading.BoundedSemaphore(1)
        self.cancel_slots = threading.BoundedSemaphore(4)
        self.stats = {"pluginRequests": 0, "mcpRequests": 0, "denials": 0,
                      "sessionsOpened": 0, "sessionsClosed": 0, "sessionsExpired": 0}

    def prune_sessions(self):
        with self.lock:
            expired = [key for key, session in self.sessions.items()
                       if not session.inflight and time.monotonic() - session.last_used > 600]
            sessions = [self.sessions.pop(key) for key in expired]
            self.stats["sessionsExpired"] += len(sessions)
        if self.gateway:
            for session in sessions:
                self.gateway.cancel(session)

    def configured_actor(self, uid):
        settings = self.settings
        if self.settings_file:
            try:
                settings = json.loads(self.settings_file.read_text(encoding="utf-8"))
                if not secrets.compare_digest(settings["bridgeKey"], self.settings["bridgeKey"]):
                    return None
            except (OSError, ValueError, KeyError, TypeError):
                return None
        return settings.get("actors", {}).get(uid)

    def server_close(self):
        if self.gateway:
            self.gateway.close()
        super().server_close()

    def shared_gateway(self):
        with self.lock:
            if not self.gateway or self.gateway.process.poll() is not None:
                if self.gateway:
                    self.gateway.close()
                self.gateway = Gateway()
                for session in self.sessions.values():
                    session.instances.clear()
            return self.gateway


class ActorSession:
    def __init__(self, uid, profile):
        self.uid = uid
        self.profile = profile
        self.instances = {}
        self.inflight = {}
        self.last_used = time.monotonic()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, value=None, session=None):
        data = canonical_json(value) if value is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if session:
            self.send_header("Mcp-Session-Id", session)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health" and self.client_address[0] == "127.0.0.1":
            self.server.prune_sessions()
            return self.reply(200, {"ready": not self.server.gateway or self.server.gateway.process.poll() is None,
                                   "policy": POLICY, "brokerPid": os.getpid(),
                                   "gatewayPid": self.server.gateway.process.pid if self.server.gateway else None,
                                   "sessions": len(self.server.sessions), **self.server.stats})
        self.reply(404)

    def do_DELETE(self):
        self.handle_request(delete=True)

    def do_POST(self):
        self.handle_request()

    def handle_request(self, delete=False):
        settings = self.server.settings
        if not secrets.compare_digest(self.headers.get("X-Agency-Bridge-Key", ""), settings["bridgeKey"]):
            return self.reply(401)
        uid = self.headers.get("X-Agency-Actor-Uid", "")
        configured = self.server.configured_actor(uid)
        if not configured or self.headers.get("X-Agency-Actor") != configured["subject"]:
            return self.reply(403)
        profile = {**self.server.profiles[configured["profile"]], "gitIdentity": configured["gitIdentity"]}
        identifier = self.headers.get("Mcp-Session-Id")
        if self.path not in {"/agency/plugins", "/agency/mcp", "/agency/policy"}:
            return self.reply(404)
        cancellation_lane = self.headers.get("X-Host-Cancellation") == "1"
        slots = (self.server.cancel_slots if cancellation_lane else
                 self.server.plugin_slots if self.path == "/agency/plugins" else self.server.slots)
        if not slots.acquire(blocking=False):
            return self.reply(503)
        request = {}
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                raise Denied("request_framing_denied")
            self.connection.settimeout(200)
            request = json.loads(self.rfile.read(length)) if length else {}
            if cancellation_lane and (self.path != "/agency/mcp" or
                                      not delete and request.get("method") != "notifications/cancelled"):
                raise Denied("invalid_cancellation")
            if self.path == "/agency/policy":
                return self.reply(200, {"policy": POLICY, "profileSha256": hashlib.sha256(canonical_json(profile)).hexdigest(),
                                       "toolsets": profile["toolsets"], "gitIdentity": profile["gitIdentity"]})
            if self.path == "/agency/plugins":
                if delete:
                    raise Denied("invalid_method")
                self.server.stats["pluginRequests"] += 1
                return self.reply(200,cached_prepare_plugins(request,profile))
            self.server.stats["mcpRequests"] += 1
            session = self.server.sessions.get(identifier)
            if session and canonical_json(session.profile) != canonical_json(profile):
                raise Denied("session_profile_changed")
            if delete:
                if not session or session.uid != uid:
                    return self.reply(404)
                if self.server.gateway:
                    self.server.gateway.cancel(session)
                del self.server.sessions[identifier]
                self.server.stats["sessionsClosed"] += 1
                return self.reply(200)
            if request.get("method") == "initialize":
                self.server.prune_sessions()
                with self.server.lock:
                    if len(self.server.sessions) >= 64:
                        raise Denied("session_limit")
                    identifier = secrets.token_urlsafe(32)
                    session = ActorSession(uid, profile)
                    self.server.sessions[identifier] = session
                    self.server.stats["sessionsOpened"] += 1
                self.server.shared_gateway()
                result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "host-agency-gateway", "version": POLICY}}
            else:
                if not session or session.uid != uid:
                    return self.reply(404)
                session.last_used = time.monotonic()
                if request.get("method") == "notifications/initialized":
                    return self.reply(202)
                if request.get("method") == "notifications/cancelled":
                    if self.server.gateway:
                        self.server.gateway.cancel(session, request.get("params", {}).get("requestId"))
                    return self.reply(202)
                gateway = self.server.shared_gateway()
                if request.get("method") == "tools/list":
                    result = gateway.rpc("tools/list", {})
                    result["tools"] = [tool for tool in result.get("tools", []) if tool["name"] in {
                        "load_toolset", "search_tools", "get_tool_schema", "call_tool",
                        "list_toolsets", "get_toolsets", "get_toolset_info"}]
                elif request.get("method") == "tools/call":
                    params = request.get("params", {})
                    result = gateway.call(params.get("name"), params.get("arguments", {}), session, request.get("id"))
                elif request.get("method") == "ping":
                    result = {}
                else:
                    raise Denied("mcp_method_not_allowed")
            return self.reply(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": result}, identifier)
        except Denied as error:
            self.server.stats["denials"] += 1
            if self.path == "/agency/mcp":
                return self.reply(200, {"jsonrpc": "2.0", "id": request.get("id"),
                                       "error": {"code": -32001, "message": str(error)}}, identifier)
            self.reply(403, {"error": str(error)})
        except Exception:
            self.reply(502, {"error": "host_agency_operation_failed"})
        finally:
            slots.release()


@contextlib.contextmanager
def state_lock(path):
    with (path / "broker.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def watch_parent(server, pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if handle:
        try:
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        finally:
            kernel.CloseHandle(handle)
    server.shutdown()


def main(argv=None):
    global STATE, PROFILES, AGENCY, GLOBAL_CONFIG
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--profiles", type=Path, default=PROFILES)
    parser.add_argument("--agency", default=AGENCY)
    parser.add_argument("--global-config", type=Path, default=GLOBAL_CONFIG)
    parser.add_argument("--listen-address")
    parser.add_argument("--port", type=int)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args(argv)
    STATE, PROFILES, AGENCY, GLOBAL_CONFIG = args.state, args.profiles, args.agency, args.global_config
    STATE.mkdir(parents=True, exist_ok=True)
    settings_file = args.config or STATE / "broker.json"
    settings = json.loads(settings_file.read_text())
    port = args.port if args.port is not None else settings.get("listenPort", 15442)
    if not 1 <= port <= 65535:
        parser.error("port must be between 1 and 65535")
    with state_lock(STATE):
        server = Broker((args.listen_address or settings.get("listenAddress", "127.0.0.1"), port),
                        settings, settings_file)
        if args.parent_pid:
            threading.Thread(target=watch_parent, args=(server, args.parent_pid), daemon=True).start()
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()

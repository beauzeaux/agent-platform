import contextlib
import io
import hashlib
import copy
import json
import os
import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from unittest.mock import patch
import zipfile

from broker import (
    CONFIG_RESOLUTION, Denied, Gateway, Broker, ActorSession, ROOT, ado_write_scope,
    agency_config_env, authoritative_native_layers, cached_prepare_plugins, check_spec,
    fetch_native_remote_config, merge_native_config, prepare_plugins, public_config_sources,
    resolve_native_config, validate_archive, watch_scope, PLUGIN_CACHE, REVIEWED_HOOK_SPEC,
    PROJECT, REPOSITORY_ID,
)
from prepare_runtime import PackageRedirect
import configure
import urllib.request


class BrokerTests(unittest.TestCase):
    def setUp(self):
        token_guard=patch("broker.token",side_effect=AssertionError(
            "unit test attempted live host token acquisition"))
        network_guard=patch("broker.urllib.request.urlopen",side_effect=AssertionError(
            "unit test attempted live network access"))
        token_guard.start()
        network_guard.start()
        self.addCleanup(token_guard.stop)
        self.addCleanup(network_guard.stop)

    def test_global_config_override_is_an_isolated_directory(self):
        with tempfile.TemporaryDirectory(prefix=".broker-global-",dir=ROOT) as directory:
            root=Path(directory)
            legacy=root/"operator.toml"
            legacy.write_text("[plugins]\ndefault=[]\n",encoding="utf-8")
            with patch("broker.GLOBAL_CONFIG",legacy),patch("broker.STATE",root/"state"):
                environment=agency_config_env()
                isolated=Path(environment["AGENCY_GLOBAL_CONFIG_PATH"])
                self.assertTrue(isolated.is_dir())
                self.assertNotEqual(legacy,isolated)
                self.assertFalse(any(isolated.joinpath(name).exists()
                                     for name in ["agency.toml","agency.yaml"]))
                isolated.joinpath("agency.toml").write_text(
                    "[plugins]\ndefault=[]\n",encoding="utf-8")
                with self.assertRaises(Denied):
                    agency_config_env()

    def test_identical_reviewed_plugin_preparation_reuses_host_cache(self):
        body={"commit":"a"*40,"configCommit":"b"*40,"configText":"[plugins]"}
        profile={"repository":{"name":"engsys"}}
        PLUGIN_CACHE.clear()
        with patch("broker.prepare_plugins",return_value={"manifest":{"identity":"x"},"bundles":[]}) as prepare:
            self.assertEqual(cached_prepare_plugins(body,profile),cached_prepare_plugins(body,profile))
            prepare.assert_called_once_with(body,profile)

    def test_plugin_cache_identity_includes_working_directory_scope(self):
        profile={"repository":{"name":"engsys"}}
        root={"commit":"a"*40,"workingDirectory":"."}
        nested={**root,"workingDirectory":"src/component"}
        PLUGIN_CACHE.clear()
        with patch("broker.prepare_plugins",return_value={"manifest":{"identity":"x"},"bundles":[]}) as prepare:
            cached_prepare_plugins(root,profile)
            cached_prepare_plugins(nested,profile)
            cached_prepare_plugins(root,profile)
            self.assertEqual(2,prepare.call_count)

    def test_plugin_cache_does_not_reuse_branch_resolved_remote_config(self):
        profile={"repository":{"name":"engsys"}}
        body={"commit":"a"*40,"workingDirectory":"."}
        prepared={
            "manifest":{
                "identity":"x",
                "configSources":[{
                    "type":"remote_config","refType":"branch","requestedRef":"main",
                    "resolvedRef":"main","sha256":"a"*64,
                }],
            },
            "bundles":[],
        }
        PLUGIN_CACHE.clear()
        with patch("broker.prepare_plugins",return_value=prepared) as prepare:
            cached_prepare_plugins(body,profile)
            cached_prepare_plugins(body,profile)
            self.assertEqual(2,prepare.call_count)
        pinned=copy.deepcopy(prepared)
        pinned["manifest"]["configSources"][0].update(
            refType="rev",requestedRef="b"*40,resolvedRef="b"*40)
        PLUGIN_CACHE.clear()
        with patch("broker.prepare_plugins",return_value=pinned) as prepare:
            cached_prepare_plugins(body,profile)
            cached_prepare_plugins(body,profile)
            prepare.assert_called_once_with(body,profile)

    def test_native_request_uses_authoritative_ancestor_sources_and_allows_no_config(self):
        profile=json.loads((ROOT/"profiles.json").read_text())["engsys-contributor"]
        commit="a"*40
        configs={
            "agency.toml":"[copilot]\nbackground_compaction_threshold = 0.25\n",
            "src/agency.yaml":"copilot:\n  background_compaction_threshold: 0.75\n",
        }
        environment={"AGENCY_ADO_TOKEN":"test-only"}
        def fetch(_repository, _commit, path, _token):
            return configs.get(path)
        with patch("broker.host_env",return_value=environment), \
                patch("broker.validate_repository_commit"), \
                patch("broker.fetch_repository_text",side_effect=fetch), \
                patch("broker.resolve_native_config",return_value=(
                    {"copilot":{"background_compaction_threshold":0.75}}, "effective", [
                        {"type":"file","path":"agency.toml","format":"toml","commit":commit,
                         "sha256":hashlib.sha256(configs["agency.toml"].encode()).hexdigest(),
                         "content":configs["agency.toml"]},
                        {"type":"file","path":"src/agency.yaml","format":"yaml","commit":commit,
                         "sha256":hashlib.sha256(configs["src/agency.yaml"].encode()).hexdigest(),
                         "content":configs["src/agency.yaml"]},
                    ])):
            prepared=prepare_plugins({"commit":commit,"workingDirectory":"src/component"},profile)
        manifest=prepared["manifest"]
        self.assertEqual(CONFIG_RESOLUTION,manifest["configResolution"])
        self.assertEqual(["agency.toml","src/agency.yaml"],
                         [source["path"] for source in manifest["configSources"]])
        self.assertEqual(commit,manifest["configSources"][0]["commit"])
        self.assertNotIn("configText",manifest)
        with patch("broker.host_env",return_value=environment), \
                patch("broker.validate_repository_commit"), \
                patch("broker.fetch_repository_text",return_value=None), \
                patch("broker.resolve_native_config",return_value=({}, "", [])):
            empty=prepare_plugins({"commit":commit,"workingDirectory":"."},profile)
        self.assertEqual([],empty["manifest"]["configSources"])
        self.assertEqual([],empty["bundles"])

    def test_new_contract_rejects_forged_paths_text_and_scope(self):
        profile=json.loads((ROOT/"profiles.json").read_text())["engsys-contributor"]
        valid={"commit":"a"*40,"workingDirectory":"src/component"}
        for body in [
            {**valid,"configText":"[plugins]"},
            {**valid,"configSources":[]},
            {**valid,"workingDirectory":"../outside"},
            {**valid,"workingDirectory":"/src/component"},
            {**valid,"workingDirectory":"src\\component"},
            {**valid,"workingDirectory":"src./component"},
            {**valid,"workingDirectory":"src/\u0085component"},
        ]:
            with self.subTest(body=body), self.assertRaises(Denied):
                prepare_plugins(body,profile)

    def test_legacy_root_request_remains_authoritative_and_hash_compatible(self):
        profile=json.loads((ROOT/"profiles.json").read_text())["engsys-contributor"]
        commit,config_commit="a"*40,"b"*40
        text="[plugins]\ndefault = []\n"
        with patch("broker.host_env",return_value={"AGENCY_ADO_TOKEN":"test-only"}), \
                patch("broker.validate_repository_commit"), \
                patch("broker.fetch_repository_text",return_value=text), \
                patch("broker.resolve_native_config",return_value=({"plugins":{"default":[]}}, text, [{
                    "type":"file","path":"agency.toml","format":"toml","commit":config_commit,
                    "sha256":hashlib.sha256(text.encode()).hexdigest(),"content":text}])):
            prepared=prepare_plugins({
                "commit":commit,"configCommit":config_commit,"configText":text},profile)
        manifest=prepared["manifest"]
        self.assertEqual("legacy-root-v1",manifest["configResolution"])
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(),manifest["configSha256"])
        self.assertEqual(config_commit,manifest["configSources"][0]["commit"])
        with patch("broker.host_env",return_value={"AGENCY_ADO_TOKEN":"test-only"}), \
                patch("broker.validate_repository_commit"), \
                patch("broker.fetch_repository_text",return_value=text):
            with self.assertRaises(Denied):
                prepare_plugins({
                    "commit":commit,"configCommit":config_commit,"configText":text+"changed"},profile)

    def test_native_resolver_merges_toml_yaml_ancestors_and_excludes_siblings(self):
        executable=os.environ.get("AGENCY_TEST_EXECUTABLE")
        if not executable or not Path(executable).is_file():
            self.skipTest("AGENCY_TEST_EXECUTABLE is not available")
        with tempfile.TemporaryDirectory(prefix=".broker-native-",dir=ROOT) as directory:
            stage=Path(directory)
            global_config=stage/"global"
            global_config.mkdir()
            repository=stage/"repository"
            (repository/"src"/"component"/"child").mkdir(parents=True)
            (repository/"src"/"sibling").mkdir(parents=True)
            (repository/"agency.toml").write_text(
                "[copilot]\nbackground_compaction_threshold = 0.25\n",encoding="utf-8")
            (repository/"agency.yaml").write_text(
                "copilot:\n  background_compaction_threshold: 0.99\n",encoding="utf-8")
            (repository/"src"/"agency.yaml").write_text(
                "copilot:\n  background_compaction_threshold: 0.75\n",encoding="utf-8")
            (repository/"src"/"component"/"agency.toml").write_text(
                "[copilot]\nbackground_compaction_threshold = 0.5\n",encoding="utf-8")
            (repository/"src"/"sibling"/"agency.toml").write_text(
                "[copilot]\nbackground_compaction_threshold = 0.99\n",encoding="utf-8")
            home=stage/"home"
            home.mkdir()
            env={
                "PATH":os.environ.get("PATH",""),
                "HOME":str(home),
                "USERPROFILE":str(home),
                "LOCALAPPDATA":str(home/"local"),
                "APPDATA":str(home/"roaming"),
                "XDG_CACHE_HOME":str(home/"cache"),
                "XDG_CONFIG_HOME":str(home/"config"),
                "XDG_DATA_HOME":str(home/"data"),
                "AGENCY_GLOBAL_CONFIG_PATH":str(global_config),
                "AGENCY_NO_CONFIG_CACHE":"true",
                "AGENCY_NO_UPDATE_CHECK":"1",
                "AGENCY_RING":"Prod",
                "AGENCY_ECS_BASE_URL":"http://127.0.0.1:9",
                "AGENCY_SESSION_SUBPROCESS":"1",
                "AGENCY_TELEMETRY_FLUSH_CHILD":"1",
                "AGENCY_ADO_TOKEN":"offline-test-token",
                "AGENCY_TOKEN_ADO_MSASG":"offline-test-token",
                "AGENCY_GITHUB_TOKEN":"offline-test-token",
                "AGENCY_REMOTE_CONFIG_VIA_ASA":"0",
                "AGENCY_SANDBOX":"1",
                "HTTP_PROXY":"http://127.0.0.1:9",
                "HTTPS_PROXY":"http://127.0.0.1:9",
                "ALL_PROXY":"http://127.0.0.1:9",
                "NO_COLOR":"1",
            }
            for name in ["SystemRoot","WINDIR","COMSPEC","PATHEXT"]:
                if value:=os.environ.get(name):
                    env[name]=value
            sources=[
                {"type":"file","path":"agency.toml","format":"toml","commit":"a"*40,
                 "sha256":"a"*64,"content":(repository/"agency.toml").read_text()},
                {"type":"file","path":"src/agency.yaml","format":"yaml","commit":"a"*40,
                 "sha256":"a"*64,"content":(repository/"src"/"agency.yaml").read_text()},
                {"type":"file","path":"src/component/agency.toml","format":"toml","commit":"a"*40,
                 "sha256":"a"*64,
                 "content":(repository/"src"/"component"/"agency.toml").read_text()},
            ]
            with patch("broker.AGENCY",executable):
                effective,_,layers=resolve_native_config(
                    repository,"src/component/child",sources,env)
            self.assertEqual(0.5,effective["copilot"]["background_compaction_threshold"])
            self.assertEqual(["agency.toml","src/agency.yaml","src/component/agency.toml"],
                             [layer["path"] for layer in layers])
            with patch("broker.AGENCY",executable):
                root_effective,_,root_layers=resolve_native_config(
                    repository,".",sources[:1],env)
            self.assertEqual(0.25,root_effective["copilot"]["background_compaction_threshold"])
            self.assertEqual(["agency.toml"],[layer["path"] for layer in root_layers])
            pointer={
                "git":"https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/shared-config",
                "rev":"b"*40,
                "path":"config/agency.toml",
            }
            remote_source={
                "type":"file","path":"agency.toml","format":"toml","commit":"a"*40,
                "sha256":"a"*64,
                "content":'[[remote_config]]\ngit = "'+pointer["git"]+'"\nrev = "'+
                          pointer["rev"]+'"\npath = "'+pointer["path"]+'"\n',
            }
            with patch("broker.AGENCY",executable):
                pointer_config,_=merge_native_config([remote_source],repository,env)
            self.assertEqual([pointer],pointer_config["remote_config"])
            empty=stage/"empty"
            empty.mkdir()
            with patch("broker.AGENCY",executable):
                no_config,_,no_layers=resolve_native_config(empty,".",[],env)
            self.assertEqual({},no_config)
            self.assertEqual([],no_layers)
            (repository/"src"/"component"/"agency.toml").write_text(
                "[copilot\nbackground_compaction_threshold = 0.5\n",encoding="utf-8")
            sources[-1]["content"]=(repository/"src"/"component"/"agency.toml").read_text()
            with patch("broker.AGENCY",executable), self.assertRaises(Denied):
                resolve_native_config(repository,"src/component/child",sources,env)

    def test_native_remote_config_keeps_pinned_content_provenance_without_exposing_content(self):
        pointer={
            "git":"https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/shared-config",
            "rev":"b"*40,
            "path":"config/agency.toml",
        }
        fetched={
            "layer":{"format":"toml","resolved_ref":"b"*40,
                     "content":"[plugins]\ndefault=[]\n"},
            "additional_configs":[],
        }
        with patch("broker.agency_json",return_value=(fetched,b"{}")) as fetch:
            remote=fetch_native_remote_config(pointer,"src/agency.yaml",ROOT,{})
        self.assertEqual(["service-fn","fetch","config","--git",pointer["git"],
                          "--rev",pointer["rev"],"--path",pointer["path"]],
                         fetch.call_args.args[0])
        source={
            "type":"file","path":"src/agency.yaml","format":"yaml","commit":"a"*40,
            "sha256":"a"*64,
            "content":"remote_config:\n- git: "+pointer["git"]+"\n  rev: "+pointer["rev"]+"\n",
        }
        with patch("broker.merge_native_config",
                   return_value=({"remote_config":[pointer]},b"{}")), \
                patch("broker.fetch_native_remote_config",return_value=remote):
            layers=authoritative_native_layers([source],ROOT,{})
        self.assertEqual(["remote_config","file"],[layer["type"] for layer in layers])
        self.assertEqual(source["path"],layers[0]["declaredBy"])
        public=public_config_sources(layers)
        self.assertEqual(["remote_config","file"],[layer["type"] for layer in public])
        self.assertNotIn("content",public[0])
        self.assertNotIn("content",public[1])
        transitive={**fetched,"additional_configs":[pointer]}
        with patch("broker.agency_json",return_value=(transitive,b"{}")), self.assertRaises(Denied):
            fetch_native_remote_config(pointer,"src/agency.yaml",ROOT,{})

    def test_swarm_profiles_keep_readers_and_triage_bounded(self):
        profiles=json.loads((ROOT/"profiles.json").read_text())
        reader=profiles["engsys-reader"]["toolsets"]
        triage=profiles["slopmop-triage"]["toolsets"]
        contributor=profiles["engsys-contributor"]["toolsets"]
        slopmop=profiles["slopmop-contributor"]
        self.assertNotIn("watch",reader)
        self.assertFalse(any(tool.endswith("_write") for tool in reader["ado"]["tools"]))
        self.assertNotIn("repo_pull_request_write",triage["ado"]["tools"])
        self.assertIn("wit_work_item_write",triage["ado"]["tools"])
        self.assertEqual(contributor["watch"]["tools"],
                         ["finish_pull_request","check_pull_request_feedback"])
        allowed={"action":"create","repositoryId":"engsys",
            "sourceRefName":"refs/heads/users/nibeauli/slopmop-demo-campaign-0",
            "targetRefName":"refs/heads/users/nibeauli/slopmop-devel"}
        ado_write_scope("repo_pull_request_write",allowed,slopmop["repository"])
        with self.assertRaises(Denied):
            ado_write_scope("repo_pull_request_write",{**allowed,
                "targetRefName":"refs/heads/main"},slopmop["repository"])
        pull_request={"sourceRefName":"refs/heads/users/nibeauli/slopmop-demo-campaign-0",
                      "targetRefName":"refs/heads/users/nibeauli/slopmop-devel"}
        response=Mock()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        with patch("broker.token",return_value="test-only"), \
                patch("broker.urllib.request.urlopen",return_value=response), \
                patch("broker.json.load",return_value=pull_request):
            watch_scope("finish_pull_request",{
                "pull_request":"https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/123"},
                slopmop["repository"])
        with patch("broker.token",return_value="test-only"), \
                patch("broker.urllib.request.urlopen",return_value=response), \
                patch("broker.json.load",
                      return_value={**pull_request,"targetRefName":"refs/heads/main"}):
            with self.assertRaises(Denied):
                watch_scope("finish_pull_request",{
                    "pull_request":"https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/123"},
                    slopmop["repository"])

    def test_finish_pr_is_scoped_to_explicit_engsys_pull_request_url(self):
        allowed={"pull_request":"https://dev.azure.com/msasg/Engineering%20Fundamentals/_git/engsys/pullrequest/123"}
        watch_scope("finish_pull_request",allowed)
        watch_scope("check_pull_request_feedback",allowed)
        for value in ["123","https://dev.azure.com/other/project/_git/repo/pullrequest/123",
                      "https://github.com/msasg/engsys/pull/123",None]:
            with self.assertRaises(Denied):
                watch_scope("finish_pull_request",{"pull_request":value})

    def test_actor_binding_hot_reload_and_revocation_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix=".broker-test-",dir=ROOT) as directory:
            path = Path(directory) / "broker.json"
            settings = {"bridgeKey":"test-key", "actors":{"a":{"subject":"actor/factory-executions/a"}}}
            path.write_text(json.dumps(settings))
            server = Broker(("127.0.0.1", 0), settings, path)
            try:
                self.assertEqual(server.configured_actor("a")["subject"], "actor/factory-executions/a")
                settings["actors"] = {}
                path.write_text(json.dumps(settings))
                self.assertIsNone(server.configured_actor("a"))
                path.write_text("{invalid")
                self.assertIsNone(server.configured_actor("a"))
                path.unlink()
                self.assertIsNone(server.configured_actor("a"))
            finally:
                server.server_close()

    def archive(self, files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in files.items():
                entry = zipfile.ZipInfo(name)
                entry.filename = name
                archive.writestr(entry, content)
        return buffer.getvalue()

    def test_source_policy_applies_to_all_dependencies(self):
        check_spec("market:mai-engsys@agency-microsoft/playground")
        for spec in ["local:C:\\secret", "github:other/repo", "market:x@other/playground",
                     "market:x@agency-microsoft/unapproved"]:
            with self.assertRaises(Denied):
                check_spec(spec)

    def test_archive_and_execution_policy(self):
        self.assertIn("skills/read/SKILL.md", validate_archive(self.archive({"skills/read/SKILL.md": b"read"})))
        for files in [{"../secret": b"x"}, {"x\\secret": b"x"}, {".mcp.json": b"{}"},
                      {"hooks/start.sh": b"execute"}, {"agency.json": b'{"artifacts":[{}]}'},
                      {"agency.json": b'{"platforms":["windows"]}'},
                      {"A.txt": b"x", "a.txt": b"collision"}]:
            with self.subTest(files=files), self.assertRaises(Denied):
                validate_archive(self.archive(files))

    def test_package_redirect_drops_authorization_and_rejects_other_destinations(self):
        request = urllib.request.Request("https://pkgs.dev.azure.com/package",
                                         headers={"Authorization": "Bearer test-only"})
        redirect = PackageRedirect()
        safe = redirect.redirect_request(request, None, 302, "", {}, "https://x.vsblob.vsassets.io/package?sig=test")
        self.assertIsNone(safe.get_header("Authorization"))
        for location in ["http://x.vsblob.vsassets.io/a", "https://example.com/a"]:
            with self.assertRaises(RuntimeError):
                redirect.redirect_request(request, None, 302, "", {}, location)

    def test_reviewed_hook_is_bound_to_spec_complete_manifest_and_content_hashes(self):
        files = {"hooks/hooks.json": b"{}", "hooks/session-start.cjs": b"process.stdout.write('context');",
                 "plugin.json": b'{"hooks":"hooks/hooks.json"}'}
        approved = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
        with patch("broker.REVIEWED_HOOK_FILES", approved):
            validate_archive(self.archive(files), REVIEWED_HOOK_SPEC)
            changed_manifest = {**files, "plugin.json": b'{"hooks":"hooks/hooks.json","version":"reviewed"}'}
            with self.assertRaises(Denied):
                validate_archive(self.archive(changed_manifest), REVIEWED_HOOK_SPEC)
            manifest_hash = hashlib.sha256(changed_manifest["plugin.json"]).hexdigest()
            with patch("broker.REVIEWED_HOOK_MANIFESTS", {manifest_hash}):
                validate_archive(self.archive(changed_manifest), REVIEWED_HOOK_SPEC)
                changed_execution = {**changed_manifest, "hooks/session-start.cjs": b"changed executable"}
                with self.assertRaises(Denied):
                    validate_archive(self.archive(changed_execution), REVIEWED_HOOK_SPEC)
            with self.assertRaises(Denied):
                validate_archive(self.archive(files), "market:other@agency-microsoft/playground")
            validate_archive(
                self.archive(files), "market:AIX-ES-Standards@agency-microsoft/playground")
            files["hooks/session-start.cjs"] = b"changed executable"
            with self.assertRaises(Denied):
                validate_archive(self.archive(files), REVIEWED_HOOK_SPEC)
    def gateway(self):
        gateway = Gateway.__new__(Gateway)
        gateway.instances = {"ado::organization=msasg": "ado"}
        gateway.rpc = Mock(return_value={})
        return gateway

    def test_invalid_alias_org_repository_and_unloaded_instance_are_denied_before_rpc(self):
        gateway = self.gateway()
        for arguments in [
            {"toolset_id": "ado", "tool_name": "core_list_projects", "arguments": {}},
            {"toolset_id": "ado::organization=msasg", "tool_name": "repo_pull_request_write", "arguments": {}},
            {"toolset_id": "ado::organization=msasg", "tool_name": "repo_pull_request_write",
             "arguments": {"repositoryId": "other", "action": "create"}},
            {"toolset_id": "ado::organization=msasg", "tool_name": "repo_file", "args": {"operation": "write"}},
            {"toolset_id": "ado::organization=msasg", "tool_name": "repo_file",
             "arguments": {"operation": "get", "organization": "other"}},
        ]:
            with self.assertRaises(Denied):
                gateway.call("call_tool", arguments)
        gateway.rpc.assert_not_called()
        gateway.call("call_tool", {"toolset_id": "ado::organization=msasg",
                                   "tool_name": "core_list_projects", "arguments": {}})
        gateway.rpc.assert_called_once()

    def test_pr_creation_is_enabled_and_bound_to_engsys(self):
        gateway = self.gateway()
        gateway.call("call_tool", {"toolset_id": "ado::organization=msasg",
            "tool_name": "repo_pull_request_write", "arguments": {
                "repositoryId": "engsys", "action": "create", "sourceRefName": "refs/heads/agent/test",
                "targetRefName": "refs/heads/main", "title": "Test"}})
        arguments = gateway.rpc.call_args.args[1]["arguments"]["arguments"]
        self.assertEqual(arguments["repositoryId"], REPOSITORY_ID)
        self.assertEqual(arguments["project"], PROJECT)

    def test_work_item_updates_verify_project_without_disabling_writes(self):
        gateway = self.gateway()
        response = io.BytesIO(b'{"fields":{"System.TeamProject":"Engineering Fundamentals"}}')
        with patch("broker.token", return_value="test-only"), patch("broker.urllib.request.urlopen", return_value=response):
            gateway.call("call_tool", {"toolset_id": "ado::organization=msasg",
                "tool_name": "wit_work_item_write", "arguments": {"action": "update", "id": 42,
                    "updates": [{"op": "Replace", "path": "/fields/System.Title", "value": "Done"}]}})
        gateway.rpc.assert_called_once()
        response = io.BytesIO(b'{"fields":{"System.TeamProject":"other"}}')
        with patch("broker.token", return_value="test-only"), patch("broker.urllib.request.urlopen", return_value=response):
            with self.assertRaises(Denied):
                gateway.call("call_tool", {"toolset_id": "ado::organization=msasg",
                    "tool_name": "wit_work_item_write", "arguments": {"action": "update", "id": 43}})

    def test_unapproved_toolsets_and_organizations_are_denied(self):
        gateway = self.gateway()
        for toolset, args in [("ado", {}), ("ado", {"organization": "other"}),
                              ("kusto", {"service-uri": "https://unapproved"}),
                              ("workiq", {"endpoint": "https://unapproved"})]:
            with self.assertRaises(Denied):
                gateway.call("load_toolset", {"toolset_id": toolset, "toolset_args": args})
        gateway.rpc.assert_not_called()

    def test_agents_share_one_gateway_but_have_separate_instance_authorization(self):
        shared = Mock()
        shared.process.poll.return_value = None
        with patch("broker.Gateway", return_value=shared) as factory:
            server = Broker(("127.0.0.1", 0), {"actors": {}})
            try:
                self.assertIs(server.shared_gateway(), server.shared_gateway())
                factory.assert_called_once()
            finally:
                server.server_close()
        profile = json.loads((ROOT / "profiles.json").read_text())["engsys-contributor"]
        contributor = ActorSession("contributor", profile)
        contributor.instances["ado::organization=msasg"] = "ado"
        other = ActorSession("other", copy.deepcopy(profile))
        gateway = self.gateway()
        with self.assertRaises(Denied):
            gateway.call("get_tool_schema", {"toolset_id": "ado::organization=msasg",
                "tool_name": "repo_pull_request_write"}, other)
        gateway.call("get_tool_schema", {"toolset_id": "ado::organization=msasg",
            "tool_name": "repo_pull_request_write"}, contributor)
        other.profile["toolsets"]["ado"]["tools"].remove("repo_pull_request_write")
        other.instances["ado::organization=msasg"] = "ado"
        with self.assertRaises(Denied):
            gateway.call("get_tool_schema", {"toolset_id": "ado::organization=msasg",
                "tool_name": "repo_pull_request_write"}, other)

    def test_cancellation_translates_only_the_owning_sessions_request_ids(self):
        gateway = self.gateway()
        gateway.lock = threading.Lock()
        gateway.send = Mock()
        first, second = ActorSession("first", {}), ActorSession("second", {})
        first.inflight = {7: 101, 8: 103}
        second.inflight = {7: 102}
        gateway.cancel(first, 7)
        gateway.send.assert_called_once_with({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                              "params": {"requestId": 101}})
        gateway.send.reset_mock()
        gateway.cancel(first)
        self.assertEqual([call.args[0]["params"]["requestId"] for call in gateway.send.call_args_list], [101, 103])
        self.assertEqual(second.inflight, {7: 102})

    def test_registration_preserves_custom_profile_and_approved_author_on_reinstall(self):
        uid = "11111111-1111-1111-1111-111111111111"
        entry = {"subject": "actor/custom/coder", "profile": "custom",
                 "gitIdentity": {"name": "Test Agent", "email": "test@local.invalid"}}
        with tempfile.TemporaryDirectory(prefix=".broker-test-",dir=ROOT) as directory:
            root = Path(directory)
            (root / "state").mkdir()
            (root / "profiles.json").write_text('{"custom":{}}')
            target = root / "state" / "broker.json"
            target.write_text(json.dumps({"bridgeKey": "test-only", "actors": {uid: entry}}))
            with patch.object(configure, "ROOT", root), patch("sys.argv", ["configure", "--actor-uid", uid]), \
                    patch("configure.configuration_lock", return_value=contextlib.nullcontext()), \
                    patch("configure.subprocess.check_output", return_value='"Test","S-1-5-21-123"\n') as output, \
                    patch("configure.subprocess.run"):
                configure.main()
            self.assertEqual(json.loads(target.read_text())["actors"][uid], entry)
            output.assert_called_once()


if __name__ == "__main__":
    unittest.main()

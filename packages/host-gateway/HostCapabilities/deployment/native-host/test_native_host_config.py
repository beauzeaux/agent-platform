import copy
import stat
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from configure_native_host import extended_settings, protected_file, OWNER


class NativeHostConfigTests(unittest.TestCase):
    def settings(self):
        return {"listen_address": "10.82.0.2", "llm_host": "172.24.160.1", "llm_port": 4141,
                "server_certificate": "/protected/legacy.crt", "server_key": "/protected/legacy.key",
                "windows_agency_relay": r"Q:\tools\kagent-lab\agency-broker\windows_relay.py",
                "client_ca": "/protected/ca.crt", "gateway_key_command": ["existing-key-command"],
                "windows_relay": "existing-ado-relay", "unrelated": {"keep": [1, 2]}}

    def test_only_native_frontend_changes_and_default_is_disabled(self):
        original = self.settings()
        untouched = copy.deepcopy(original)
        result = extended_settings(original, Path("/protected/new.crt"), Path("/protected/new.key"))
        self.assertEqual(original, untouched)
        self.assertEqual({key: value for key, value in result.items() if key != "native_frontend"}, original)
        self.assertFalse(result["native_frontend"]["enabled"])
        self.assertEqual(result["native_frontend"]["managed_by"], OWNER)
        enabled = extended_settings(result, Path("/protected/new.crt"), Path("/protected/new.key"), True)
        self.assertTrue(enabled["native_frontend"]["enabled"])
        self.assertEqual(enabled["native_frontend"]["listen_port"], 15445)
        self.assertEqual(extended_settings(enabled, Path("/protected/new.crt"), Path("/protected/new.key"), True), enabled)

    def test_unowned_configs_and_legacy_certificate_reuse_are_not_modified(self):
        for change in [{"listen_address": "0.0.0.0"}, {"llm_host": "other.invalid"},
                       {"native_frontend": {"enabled": True}}, {"windows_agency_relay": "unowned-relay"}]:
            original = {**self.settings(), **change}
            untouched = copy.deepcopy(original)
            with self.subTest(change=change), self.assertRaises(ValueError):
                extended_settings(original, Path("/protected/new.crt"), Path("/protected/new.key"), True)
            self.assertEqual(original, untouched)
        with self.assertRaises(ValueError):
            extended_settings(self.settings(), Path("/protected/legacy.crt"), Path("/protected/new.key"), True)

    def test_shared_nonroot_and_symlink_state_files_cannot_be_modified(self):
        for mode, uid in [(stat.S_IFREG | 0o644, 0), (stat.S_IFREG | 0o600, 1000),
                          (stat.S_IFLNK | 0o600, 0)]:
            path = Mock()
            path.lstat.return_value = SimpleNamespace(st_mode=mode, st_uid=uid)
            with self.subTest(mode=mode, uid=uid), self.assertRaises(ValueError):
                protected_file(path)


if __name__ == "__main__":
    unittest.main()

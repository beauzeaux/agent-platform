import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = pathlib.Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import wireguard_sync


class WireGuardSyncTests(unittest.TestCase):
    def test_configure_interface_preserves_learned_peer_endpoints(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with (
                patch.object(wireguard_sync, "WIREGUARD_DIRECTORY", pathlib.Path(temporary_directory)),
                patch.object(wireguard_sync, "interface_exists", return_value=True),
                patch.object(wireguard_sync, "run") as run,
            ):
                wireguard_sync.configure_interface("wg0", "10.82.0.1/24", "[Interface]\nListenPort = 51820\n")

        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any(command[:3] == ["wg", "syncconf", "wg0"] for command in commands))
        self.assertFalse(any(command[:2] == ["wg", "setconf"] for command in commands))


if __name__ == "__main__":
    unittest.main()

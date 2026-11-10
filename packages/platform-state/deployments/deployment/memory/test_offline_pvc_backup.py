import tarfile
import unittest
from unittest.mock import patch
from offline_pvc_backup import execute, maintenance_copy, node_ready, remote_command, validate_archive


class OfflineMaintenanceBoundaryTests(unittest.TestCase):
    def test_execution_requires_parent_pause_before_any_work(self):
        with patch("offline_pvc_backup.source_identity") as source:
            with self.assertRaises(RuntimeError):
                execute(False)
            source.assert_not_called()

    def test_copy_failure_restores_original_replicas_in_finally(self):
        calls = []
        with patch("offline_pvc_backup.guarded_scale", side_effect=lambda target, current, directory: calls.append((target, current))), \
                patch("offline_pvc_backup.wait_stopped"), patch("offline_pvc_backup.check_xl"), \
                patch("offline_pvc_backup.stream_offline", side_effect=RuntimeError("copy failed")), \
                patch("offline_pvc_backup.Path") as directory:
            with self.assertRaisesRegex(RuntimeError, "copy failed"):
                maintenance_copy(directory, {"replicas": 1})
        self.assertEqual(calls, [(0, 1), (1, 0)])

    def test_node_loss_during_shutdown_restores_original_replicas(self):
        calls = []
        with patch("offline_pvc_backup.guarded_scale", side_effect=lambda target, current, directory: calls.append((target, current))), \
                patch("offline_pvc_backup.wait_stopped", side_effect=RuntimeError("XL NotReady")), \
                patch("offline_pvc_backup.stream_offline") as copy, \
                patch("offline_pvc_backup.Path") as directory:
            with self.assertRaisesRegex(RuntimeError, "XL NotReady"):
                maintenance_copy(directory, {"replicas": 1})
            copy.assert_not_called()
        self.assertEqual(calls, [(0, 1), (1, 0)])

    def test_interruption_restores_original_replicas(self):
        calls = []
        with patch("offline_pvc_backup.guarded_scale", side_effect=lambda target, current, directory: calls.append((target, current))), \
                patch("offline_pvc_backup.wait_stopped"), patch("offline_pvc_backup.check_xl"), \
                patch("offline_pvc_backup.stream_offline", side_effect=KeyboardInterrupt), \
                patch("offline_pvc_backup.Path") as directory:
            with self.assertRaises(KeyboardInterrupt):
                maintenance_copy(directory, {"replicas": 1})
        self.assertEqual(calls, [(0, 1), (1, 0)])

    def test_archive_rejects_traversal_and_linked_data_before_extraction(self):
        for member in [tarfile.TarInfo("../outside"), tarfile.TarInfo("/absolute"),
                       tarfile.TarInfo("pgdata/linked")]:
            if member.name.endswith("linked"):
                member.type = tarfile.SYMTYPE
                member.linkname = "/outside"
            with self.subTest(path=member.name), patch("offline_pvc_backup.tarfile.open") as archive:
                archive.return_value.__enter__.return_value.__iter__.return_value = iter([member])
                with self.assertRaises(ValueError):
                    validate_archive("unused", [])

    def test_notready_is_not_admitted_and_ssh_cannot_accept_new_host_keys(self):
        self.assertFalse(node_ready({"status": {"conditions": [{"type": "Ready", "status": "Unknown"}]}}))
        self.assertIn("StrictHostKeyChecking=yes", remote_command(False))
        self.assertIn("BatchMode=yes", remote_command(False))


if __name__ == "__main__":
    unittest.main()

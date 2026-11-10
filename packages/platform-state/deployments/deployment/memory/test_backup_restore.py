import unittest
from backup_restore import parse_inventory, restore_resources


class RestoreBoundaryTests(unittest.TestCase):
    def test_restore_probe_cannot_mount_business_or_cache_storage_or_listen_publicly(self):
        policy, pod = restore_resources("owned-restore-probe", "private-image@sha256:test")
        self.assertEqual(policy["spec"]["ingress"], [])
        self.assertEqual(policy["spec"]["egress"], [])
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertTrue(all(set(volume) == {"name", "emptyDir"} for volume in pod["spec"]["volumes"]))
        container = pod["spec"]["containers"][0]
        self.assertNotIn("ports", container)
        self.assertEqual(container["command"], ["sleep", "1200"])

    def test_inventory_compares_table_identity_and_counts_without_reading_rows(self):
        inventory = parse_inventory(b'{"schema":"b","table":"second","rows":0}\n'
                                    b'{"schema":"a","table":"first","rows":7}\n')
        self.assertEqual([(r["schema"], r["rows"]) for r in inventory], [("a", 7), ("b", 0)])
        with self.assertRaises(ValueError):
            parse_inventory(b'{"schema":"a","table":"bad","rows":-1}')


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch
from common import apply, remove


class OwnershipBoundaryTests(unittest.TestCase):
    def test_refuses_to_apply_over_unowned_resource_without_a_write(self):
        resource = {"metadata": {"labels": {"app.kubernetes.io/managed-by": "another-owner"}}}
        with patch("common.existing", return_value=resource), patch("common.kube") as write:
            with self.assertRaises(RuntimeError):
                apply({"kind": "Service", "metadata": {"name": "local-embeddings"}})
            write.assert_not_called()

    def test_refuses_to_delete_unowned_resource_without_a_write(self):
        with patch("common.existing", return_value={"metadata": {}}), patch("common.kube") as write:
            with self.assertRaises(RuntimeError):
                remove("deployment", "local-embeddings")
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()

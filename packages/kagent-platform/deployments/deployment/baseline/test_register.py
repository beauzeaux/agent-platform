import copy
import unittest

import register


class RegisterTests(unittest.TestCase):
    def test_namespace_enrollment_preserves_other_watches_and_configuration(self):
        config = {"kubernetes": {"namespace": "factory-executions",
                                "watchNamespaces": ["factory-executions", "other"]},
                  "database": {"schema": "factory"}, "memory": {"enabled": True}}
        original = copy.deepcopy(config)
        changed = register.updated_config(config)
        self.assertEqual(config, original)
        self.assertEqual(changed["database"], config["database"])
        self.assertEqual(changed["memory"], config["memory"])
        self.assertEqual(changed["kubernetes"]["namespace"], "factory-executions")
        self.assertEqual(changed["kubernetes"]["watchNamespaces"],
                         ["factory-executions", "other", "baseline", "cluster-management"])
        self.assertEqual(register.updated_config(changed), changed)

    def test_unrestricted_or_missing_factory_watch_is_not_adopted(self):
        for value in [[], ["*"], ["other"], None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                register.updated_config({"kubernetes": {"watchNamespaces": value}})

    def test_only_namespace_roles_bound_to_existing_controller_are_selected(self):
        values = []
        for namespace in ["baseline", "cluster-management"]:
            for name in ["factory-getter-role", "factory-writer-role"]:
                values.append({"kind": "Role", "metadata": {"name": name, "namespace": namespace}})
                values.append({"kind": "RoleBinding",
                               "metadata": {"name": name + "binding", "namespace": namespace},
                               "subjects": [{"kind": "ServiceAccount", "name": "factory-controller",
                                             "namespace": "factory-executions"}]})
        values.append({"kind": "Deployment", "metadata": {"name": "new-controller", "namespace": "baseline"}})
        values.append({"kind": "Role", "metadata": {"name": "unrelated", "namespace": "factory-executions"}})
        self.assertEqual(len(register.baseline_rbac(values)), 8)
        values[1]["subjects"][0]["namespace"] = "baseline"
        with self.assertRaises(ValueError):
            register.baseline_rbac(values)


if __name__ == "__main__":
    unittest.main()

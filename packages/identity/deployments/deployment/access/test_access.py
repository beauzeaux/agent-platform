import unittest
from manage import ROUTES, configuration


class LoopbackAccessTests(unittest.TestCase):
    def test_retired_pilot_is_not_exposed(self):
        self.assertNotIn("pilot.internal", ROUTES)
        config = configuration({host: "existing:8080" for host in ROUTES})
        self.assertNotIn("pilot", str(config))

    def test_headlamp_subpath_precedes_the_existing_kagent_route_without_rewriting(self):
        routes = configuration({"kagent.internal": "existing:8080"}, "headlamp:80")[
            "apps"]["http"]["servers"]["loopback"]["routes"]
        headlamp = next(route for route in routes if route["match"][0].get("path") == ["/headlamp", "/headlamp/*"])
        self.assertIn("cluster.internal", headlamp["match"][0]["host"])
        self.assertEqual(headlamp["handle"], [{"handler": "reverse_proxy",
                                               "upstreams": [{"dial": "headlamp:80"}]}])
        legacy = next(route for route in routes if route.get("match") == [{"host": ["kagent.internal"]}])
        self.assertEqual(legacy["handle"][0]["upstreams"], [{"dial": "existing:8080"}])

    def test_routes_stay_on_loopback_and_unknown_hosts_cannot_choose_an_upstream(self):
        config = configuration({"kagent.internal": "10.152.183.187:8080"})
        self.assertTrue(config["admin"]["disabled"])
        server = config["apps"]["http"]["servers"]["loopback"]
        self.assertEqual(server["listen"], ["127.0.0.1:80"])
        legacy = next(route for route in server["routes"] if route.get("match") == [{"host": ["kagent.internal"]}])
        self.assertEqual(legacy["handle"][0]["upstreams"], [{"dial": "10.152.183.187:8080"}])
        self.assertEqual(server["routes"][-1]["handle"][0]["status_code"], 404)
        self.assertNotIn("tls", config["apps"])


if __name__ == "__main__":
    unittest.main()

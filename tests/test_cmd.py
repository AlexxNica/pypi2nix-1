import unittest

from pypi2nix.cmd import parse_specline


class TestParseSpecline(unittest.TestCase):
    """Tests specline parsing"""

    default_envs = ["python2.7", "python3.3"]

    def test_simple(self):
        specline = "package==1.2"
        result = parse_specline(specline, self.default_envs)

        self.assertEqual(result, {
            "python2.7": {"name": "package", "spec": "package==1.2"},
            "python3.3": {"name": "package", "spec": "package==1.2"}
        })

    def test_complex_basic(self):
        specline = {"spec": "package==1.2"}
        result = parse_specline(specline, self.default_envs)

        self.assertEqual(result, {
            "python2.7": {
                "name": "package", "spec": "package==1.2",
                "overrides": {}, "versions": []},
            "python3.3": {
                "name": "package", "spec": "package==1.2",
                "overrides": {}, "versions": []}
        })

    def text_complex_nospec(self):
        specline = {}
        with self.assertRaises(Exception):
            parse_specline(specline, self.default_envs)

        specline = {"name": "", "spec": ""}
        with self.assertRaises(Exception):
            parse_specline(specline, self.default_envs)

    def test_complex_versions(self):
        specline = {"spec": "package==1.2", "versions": ["dep1==1.2.3"]}
        result = parse_specline(specline, self.default_envs)
        self.assertEqual(result["python2.7"], {
            "name": "package",
            "spec": "package==1.2",
            "versions": ["dep1==1.2.3"],
            "overrides": {}
        })

        specline = {"spec": "package==1.2", "versions": "dep1==1.2.3"}
        result = parse_specline(specline, self.default_envs)
        self.assertEqual(result["python2.7"], {
            "name": "package",
            "spec": "package==1.2",
            "versions": ["dep1==1.2.3"],
            "overrides": {}
        })

    def test_complex_overides(self):
        """Checks if override gets merged with overrides """
        specline = {
            "spec": "package==1.2",
            "override": {"deps_append": ["dep==1.2"]},
            "overrides": {"depA": {"deps_append": ["dep2==1.2"]}}
        }
        result = parse_specline(specline, self.default_envs)

        self.assertEqual(result["python2.7"], {
            "name": "package",
            "spec": "package==1.2",
            "overrides": {
                "package": {"deps_append": ["dep==1.2"]},
                "depA": {"deps_append": ["dep2==1.2"]}
            },
            "versions": []
        })

    def test_complex_envs(self):
        """Checks if envs overrides what was defined at top level"""
        specline = {
            "name": "name",
            "spec": "package==1.2",
            "versions": ["dep1==1.2.3"],
            "override": {"deps_append": ["dep==1.2"]},
            "overrides": {"depA": {"deps_append": ["dep2==1.2"]}},
            "envs": {
                "*": {},
                "python2.7": {
                    "name": "new_name",
                    "versions": ["dep2==1.2.3"],
                    "override": {"deps_append": ["dep3==1.2"]},
                }
            }
        }
        result = parse_specline(specline, self.default_envs)

        self.assertEqual(result, {
            "python3.3": {
                "name": "name",
                "spec": "package==1.2",
                "versions": ["dep1==1.2.3"],
                "overrides": {
                    "package": {"deps_append": ["dep==1.2"]},
                    "depA": {"deps_append": ["dep2==1.2"]}
                }
            },
            "python2.7": {
                "name": "new_name",
                "spec": "package==1.2",
                "versions": ["dep2==1.2.3"],
                "overrides": {"package": {"deps_append": ["dep3==1.2"]}}
            }
        })

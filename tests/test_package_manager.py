import os
import tempfile
import unittest
import shutil
import textwrap
import mock
import pypi2nix

from mock import patch, Mock
from pypi2nix.package_manager import Package, PackageManager
from pypi2nix.datastructures import Spec
from pypi2nix.caching import hashabledict
from pip.index import Link


class mockPackage(object):
    def __init__(self, indir="", setup="", pkginfo=""):
        self.indir = indir
        self.setup = setup
        self.pkginfo = pkginfo

    def __enter__(self):
        self.tmpdir = d = tempfile.mkdtemp()

        if self.indir:
            d = os.path.join(d, self.indir)
            os.makedirs(d)

        if self.setup:
            f = open(os.path.join(d, "setup.py"), "w")
            f.write(textwrap.dedent(self.setup))
            f.close()

        if self.pkginfo:
            f = open(os.path.join(d, "PKG-INFO"), "w")
            f.write(textwrap.dedent(self.pkginfo))
            f.close()

        return self.tmpdir

    def __exit__(self, type, value, traceback):
        shutil.rmtree(self.tmpdir)


class TestPackage(unittest.TestCase):
    def test_init_from_fullname(self):
        with mockPackage() as m:
            package = Package(fullname="abc-1.2.3", dist_dir=m)
            self.assertEqual(package.name, "abc")
            self.assertEqual(package.version, "1.2.3")
            self.assertEqual(package.dist_dir, m)

    def test_init_from_dir(self):
        with mockPackage(indir="abc-1.2.3") as m:
            package = Package(package_dir=m)
            self.assertEqual(package.name, "abc")
            self.assertEqual(package.version, "1.2.3")
            self.assertEqual(
                package.dist_dir, os.path.join(m, "abc-1.2.3"))

    def test_init_from_setup_arguments(self):
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(name="abc", version="1.2.3")
            """
        ) as m:
            package = Package(dist_dir=m)
            self.assertEqual(package.name, "abc")
            self.assertEqual(package.version, "1.2.3")
            self.assertEqual(package.dist_dir, m)

    def test_get_deps(self):
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(
                name="abc", version="1.2.3",
                install_requires=["setuptools", "pip"]
            )
            """
        ) as m:
            package = Package(fullname="abc-1.2.3", dist_dir=m)
            self.assertEqual(
                package.get_deps(),
                [Spec.from_line("setuptools"), Spec.from_line("pip")]
            )

    def test_get_deps_no_setup(self):
        with mockPackage(indir="abc-1.2.3") as m:
            package = Package(package_dir=m)
            self.assertEqual(package.get_deps(), [])

    def test_get_deps_extra(self):
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(
                name="abc", version="1.2.3",
                extras_require={
                    'test': ['nose'], 'development': ['sphinx']
                }
            )
            """
        ) as m:
            package = Package(fullname="abc-1.2.3", dist_dir=m)
            self.assertEqual(
                package.get_deps(extra=("test", "development")),
                [Spec.from_line("nose", extra=("test",)),
                 Spec.from_line("sphinx", extra=("development",))]
            )

    def test_get_deps_tests_require(self):
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(name="abc", version="1.2.3", tests_require=['nose'])
            """
        ) as m:
            package = Package(fullname="abc-1.2.3", dist_dir=m)
            self.assertEqual(
                package.get_deps(),
                [Spec.from_line("nose", extra=("_tests_require",))]
            )

    def test_get_deps_setup_requires(self):
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(name="abc", version="1.2.3", setup_requires=['setuptools'])
            """
        ) as m:
            package = Package(fullname="abc-1.2.3", dist_dir=m)
            self.assertEqual(
                package.get_deps(),
                [Spec.from_line("setuptools", extra=("_setup_requires",))]
            )

    def test_get_deps_distutils(self):
        with mockPackage(
            setup="""
            from distutils.core import setup
            setup(
                name="abc", version="1.2.3",
                requires=["foo"]
            )
            """
        ) as m:
            package = Package(fullname="abc-1.2.3", dist_dir=m)
            self.assertEqual(package.get_deps(), [Spec.from_line("foo")])

    def test_pkg_info(self):
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(
                name="abc", version="1.2.3",
                long_description="very awesome. much package. wow.",
                license="BSD"
            )
            """
        ) as m:
            pkginfo = Package(fullname="abc-1.2.3", dist_dir=m).get_pkginfo()
            self.assertEqual(pkginfo["Name"], "abc")
            self.assertEqual(pkginfo["Version"], "1.2.3")
            self.assertEqual(
                pkginfo["Description"], "very awesome. much package. wow.")
            self.assertEqual(pkginfo["License"], "BSD")

    def test_pkg_info_distutils(self):
        with mockPackage(
            setup="""
            from distutils.core import setup
            setup(name="abc", version="1.2.3")
            """,
            pkginfo="""
            Name: abc
            Version: 1.2.3
            Description: very awesome. much package. wow.
            License: BSD
            """
        ) as m:
            pkginfo = Package(fullname="abc-1.2.3", dist_dir=m).get_pkginfo()
            self.assertEqual(pkginfo["Name"], "abc")
            self.assertEqual(pkginfo["Version"], "1.2.3")
            self.assertEqual(
                pkginfo["Description"], "very awesome. much package. wow.")
            self.assertEqual(pkginfo["License"], "BSD")

    def test_no_pkg_info(self):
        with mockPackage(indir="abc-1.2.3") as m:
            package = Package(package_dir=m)
            with self.assertRaises(Exception):
                package.get_pkginfo()

    def test_get_dependency_links(self):
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(name="abc", version="1.2.3", dependency_links=["foo"])
            """
        ) as m:
            package = Package(fullname="abc-1.2.3", dist_dir=m)
            self.assertEqual(package.get_dependency_links(), ["foo"])

    def test_get_dependency_links_missing(self):
        with mockPackage(
            setup="""
            from distutils.core import setup
            setup(name="abc", version="1.2.3")
            """
        ) as m:
            package = Package(fullname="abc-1.2.3", dist_dir=m)
            self.assertEqual(package.get_dependency_links(), [])


class TestPackageManager(unittest.TestCase):
    def test_find_best_match(self):
        """Tests if finding best match works as it should"""
        pkgmgr = PackageManager()
        with patch.object(pypi2nix.package_manager.PackageFinder, 'find_requirement') as mock_method:
            mock_method.return_value = Link("http://foo.com/foo-1.1.tar.gz#md5=hash")
            pkgmgr.get_hash = Mock()
            version = pkgmgr.find_best_match(Spec.from_line("foo>0.9"))

            self.assertFalse(pkgmgr.get_hash.called)
            self.assertEqual(version, "1.1")

    def test_find_best_match_nohash(self):
        """Tests if links without hash get hash calculated"""
        pkgmgr = PackageManager()
        link = Link("http://foo.com/foo#egg=foo-1.0")

        with patch.object(pypi2nix.package_manager.PackageFinder, 'find_requirement') as mock_method:
            pkgmgr.get_hash = Mock()
            pkgmgr.get_hash.return_value = ("md5", "somehash")
            mock_method.return_value = link
            version = pkgmgr.find_best_match(Spec.from_line("foo>0.9"))

            self.assertEqual(version, "1.0")

    def test_find_best_match_cache(self):
        """Tests if link cache work"""
        pkgmgr = PackageManager()
        link = Link("http://foo.com/foo-1.0.tar.gz#md5=somehash")

        with patch.object(pypi2nix.package_manager.PackageFinder, 'find_requirement') as mock_method:
            mock_method.return_value = link
            pkgmgr.find_best_match(Spec.from_line("foo>0.9"))
            self.assertEqual(
                pkgmgr._link_cache[(Spec.from_line("foo>0.9"), None)], link)
            self.assertEqual(
                pkgmgr._link_cache[Spec.from_line("foo==1.0")],
                Link("http://foo.com/foo-1.0.tar.gz#md5=somehash"))

        with patch.object(pypi2nix.package_manager.PackageFinder, 'find_requirement') as mock_method:
            mock_method.return_value = link
            pkgmgr.find_best_match(Spec.from_line("foo>0.9"))
            self.assertFalse(mock_method.called)

    def test_find_best_match_link_hook(self):
        """Tests if link hook gets called"""
        link_hook = Mock()
        link_hook.return_value = Link("http://bar.com/bar#egg=bar-1.0")
        overrides = {"foo": hashabledict({"link": "somelink"})}
        pkgmgr = PackageManager(link_hook=link_hook, overrides=overrides)

        with patch.object(pypi2nix.package_manager.PackageFinder, 'find_requirement') as mock_method:
            pkgmgr.get_hash = Mock()
            pkgmgr.get_hash.return_value = ("md5", "somehash")

            mock_method.return_value = Link("http://foo.com/foo-1.1.tar.gz#md5=hash")
            version = pkgmgr.find_best_match(Spec.from_line("foo>0.9"))

            self.assertTrue(pkgmgr.get_hash.called)
            self.assertTrue(link_hook.called)
            self.assertEqual(version, "1.0")

            self.assertEqual(
                pkgmgr._link_cache[(Spec.from_line("foo>0.9"), overrides["foo"])],
                link_hook.return_value
            )

    def test_get_dependencies(self):
        """Tests if getting dependencies works"""
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(
                name="abc", version="1.2.3",
                install_requires=["setuptools", "pip"]
            )
            """
        ) as m:
            spec = Spec.from_line("abc==1.2.3")
            package = Package(fullname=spec.fullname, dist_dir=m)

            pkgmgr = PackageManager()
            pkgmgr.get_package = Mock(return_value=package)

            self.assertEqual(
                pkgmgr.get_dependencies(spec.name, spec.pinned),
                [Spec.from_line("setuptools"), Spec.from_line("pip")]
            )
            pkgmgr.get_package.assert_called_with(spec)

    def test_get_dependencies_cache(self):
        """Tests if dependency cache works"""
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(
                name="abc", version="1.2.3",
                install_requires=["setuptools", "pip"]
            )
            """
        ) as m:
            spec = Spec.from_line("abc==1.2.3")
            package = Package(fullname=spec.fullname, dist_dir=m)

            pkgmgr = PackageManager()
            pkgmgr.get_package = Mock(return_value=package)
            pkgmgr.get_dependencies(spec.name, spec.pinned)
            self.assertEqual(
                pkgmgr._dep_cache[(spec, None)],
                [Spec.from_line("setuptools"), Spec.from_line("pip")]
            )

            pkgmgr.get_package = Mock(return_value=package)
            pkgmgr.get_dependencies(spec.name, spec.pinned)
            self.assertFalse(pkgmgr.get_package.called)

    def test_get_dependencies_hook(self):
        """Tests if dependency hook works"""
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(
                name="abc", version="1.2.3",
                install_requires=["setuptools", "pip"]
            )
            """
        ) as m:
            spec = Spec.from_line("abc==1.2.3")
            dependency_hook = Mock(side_effect=lambda o, s, d, p: d)
            package = Package(fullname=spec.fullname, dist_dir=m)

            override = hashabledict({"spec": "somespec"})
            pkgmgr = PackageManager(
                dependency_hook=dependency_hook, overrides={"abc": override})
            pkgmgr.get_package = Mock(return_value=package)
            deps = pkgmgr.get_dependencies(spec.name, spec.pinned)
            dependency_hook.assert_called_with(override, spec, deps, package)

    def test_get_versions(self):
        """Tests if getting picked versions works"""
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(name="abc", version="1.2.3")
            """
        ) as m:
            spec = Spec.from_line("abc==1.2.3")
            spec_version = Spec.from_line("def==2.3.4")
            version_hook = Mock(
                side_effect=lambda o, s, p: [spec_version])
            package = Package(fullname=spec.fullname, dist_dir=m)

            override = hashabledict({"spec": "somespec"})
            pkgmgr = PackageManager(
                version_hook=version_hook, overrides={"abc": override})
            pkgmgr.get_package = Mock(return_value=package)
            vers = pkgmgr.get_versions(spec.name, spec.pinned)

            version_hook.assert_called_with(override, spec, package)
            self.assertEqual(vers, [spec_version])
            self.assertEqual(pkgmgr._version_cache, {(spec, override): vers})

            version_hook.reset_mock()
            vers = pkgmgr.get_versions(spec.name, spec.pinned)
            self.assertFalse(version_hook.called)

    def test_get_pkg_info(self):
        """Tests if getting pkg_info works"""
        with mockPackage(
            setup="""
            from setuptools import setup
            setup(
                name="abc", version="1.2.3",
                long_description="very awesome. much package. wow.",
                license="BSD",
                test_suite="test"
            )
            """
        ) as m:
            spec = Spec.from_line("abc==1.2.3")
            package = Package(fullname=spec.fullname, dist_dir=m)
            pkgmgr = PackageManager()
            pkgmgr.get_package = Mock(return_value=package)
            pkginfo = pkgmgr.get_pkg_info(spec.name, spec.pinned)

            self.assertEqual(pkginfo["Name"], "abc")
            self.assertEqual(pkginfo["has_tests"], True)
            self.assertEqual(pkgmgr._pkg_info_cache, {spec: pkginfo})

            pkgmgr.get_package.reset_mock()
            pkgmgr.get_pkg_info(spec.name, spec.pinned)
            self.assertFalse(pkgmgr.get_package.called)

    def test_get_link(self):
        """Tests if getting links work"""
        pkgmgr = PackageManager()
        with patch.object(pypi2nix.package_manager.PackageFinder, 'find_requirement') as mock_method:
            mock_method.return_value = Link("http://foo.com/foo-1.1.tar.gz#md5=hash")
            pkgmgr.get_hash = Mock()
            link = pkgmgr.get_link("foo", "1.1")

            self.assertEqual(mock_method.return_value, link)

import json
import os
import shutil
import atexit
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import hashlib

try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote  # noqa


from pip.exceptions import DistributionNotFound
#from pip.backwardcompat import ConfigParser
from pip.download import _download_url, _get_response_from_url
from pip.index import Link, PackageFinder
#from pip.locations import default_config_file
from pip.req import InstallRequirement
from pip.util import splitext
from email.parser import FeedParser
from collections import defaultdict

from .log import logger
from .datastructures import Spec, first


class NoPackageMatch(Exception):
    pass


class Package(object):
    """Interface to local extracted package"""

    def __init__(
        self,
        fullname=None, dist_dir=None, package_dir=None,
        exe=sys.executable, python_path=":".join(sys.path)
    ):
        """
        Initializes package, you must specify any of the following arguments:

            - fullname and dist_dir
            - package_dir
            - dist_dir (will mock setup.py to get name and version)
        """

        self.exe = exe
        self.python_path = python_path

        fullname = fullname or (package_dir and os.listdir(package_dir)[0])
        self.dist_dir = dist_dir or os.path.join(package_dir, fullname)
        self.name, self.version = self._get_name_version(fullname)

        self.name = self.name.lower()

    def get_deps(self, extra=()):
        """
        Get package dependencies from egg info or from by intercepting setup
        arguments
        """

        name, version = self.name, self.version
        deps = self._extract_egginfo(extra)

        # distutils does not provide egg_info and tests_require is not
        # written out
        to_list = lambda x: x if isinstance(x, list) else [x]
        flatten = lambda lst: \
            sum(([x] if not isinstance(x, list) else flatten(x) for x in lst), [])

        setup_args = self._get_package_setup_arguments() or {}
        if not deps:
            deps += [
                (str(p), None) for p in
                flatten(to_list(setup_args.get("install_requires") or []) +
                        to_list(setup_args.get("requires") or []))
                if p
            ]

        # This should be written by egg_info, but it's not
        if "_tests_require" in extra:
            deps += [
                (str(p), '_tests_require') for p in
                flatten(to_list(setup_args.get("tests_require") or [])) if p
            ]
        # Native dependencies
        if "_setup_requires" in extra:
            deps += [
                (str(p), "_setup_requires") for p in
                flatten(to_list(setup_args.get("setup_requires") or [])) if p
            ]
        if "_test_suite" in extra:
            # Hardcoded nose collector test suite fix
            if (
                "nose.collector" in (setup_args.get("test_suite") or "")
                and name != "nose"
            ):
                deps += [('nose', '_test_suite')]

        #logger.debug('Found: %s' % (deps,))

        return [(Spec.from_line(dep), src) for dep, src in deps if "#" not in dep]

    def get_pkginfo(self):
        """Gets package info by reading PKG-INFO file"""

        egg_info_dir = self._get_package_egg_info_path()
        pkg_info_path = os.path.join(egg_info_dir or self.dist_dir, "PKG-INFO")

        if not os.path.exists(pkg_info_path):
            raise Exception("PKG-INFO not found %s" % self.name)

        with open(pkg_info_path, 'r') as pkg_info:
            data = pkg_info.read()
            p = FeedParser()
            p.feed(data.strip())

        return p.close()

    def get_dependency_links(self):
        """
        Gets package dependency links by reading egg-info
        `dependency-links.txt` file if there is one
        """

        egg_info_dir = self._get_package_egg_info_path()

        dependency_links_path = os.path.join(
            egg_info_dir, "dependency_links.txt")

        if os.path.exists(dependency_links_path):
            with open(dependency_links_path, 'r') as dependency_links:
                return [
                    line.strip() for line in dependency_links.readlines()
                    if line.strip()
                ]

        return []

    def has_tests(self):
        """
        Checks if package has tests by running `setup.py --help-commands` and
        checking if `test` is present in result
        """

        try:
            if self.python_path:
                os.environ["PYTHONPATH"] = self.python_path
            out = subprocess.check_output(
                [self.exe, 'setup.py', '--help-commands'],
                cwd=self.dist_dir)
        except subprocess.CalledProcessError:
            logger.warn("!! test info extract failed for %s", self.name)
            return True

        return True if "test" in out else False

    def read_file(self, path):
        """Reads file from package"""

        dist_dir = self.dist_dir
        path = os.path.join(dist_dir, path)

        if not os.path.exists(path):
            raise Exception("File %s not found in package %s" % (path, self.name))

        return open(path, 'r').read()

    def _get_name_version(self, fullname):
        """
        By providing full package name, gets it's name and version by:

            - By rspliting of name by `-` and getting the last part
            - By getting package setup arguments and extracting name and
              version from there
        """

        args = self._get_package_setup_arguments() or {}
        if args.get("name") and args.get("version"):
            return (args["name"].lower(), args["version"])

        # This is unreliable, but use as a fallback
        if fullname and "-" in fullname:
            return fullname.rsplit("-", 1)

        raise Exception("Name or version of %s not found!" % self.dist_dir)

    def _extract_egginfo(self, extra=()):
        """
        Returns a list of string representations of dependencies for
        a given distribution.
        """

        deps = self._read_package_requires_file(extra)

        return deps

    def _read_package_requires_file(self, extra=()):
        """Returns a list of dependencies for an unpacked package dir."""

        egg_info_dir = self._get_package_egg_info_path()
        if egg_info_dir and \
                os.path.exists(os.path.join(egg_info_dir, "requires.txt")):
            requires = os.path.join(egg_info_dir, 'requires.txt')
        else:  # requires.txt not found
            return []

        deps = []
        with open(requires, 'r') as requirements:
            skip_section = False
            section = None
            for requirement in requirements.readlines():
                dep = requirement.strip()
                if not dep:
                    continue
                elif dep[0] == "[":
                    section = dep[1:-1]
                    skip_section = not section in extra
                    continue
                if not skip_section:
                    deps.append((dep, section))

        return deps

    def _get_package_egg_info_path(self):
        """Gets package egginfo path"""

        dist_dir = self.dist_dir
        name, version = self.name, self.version

        def _get_egg_info_path():
            egg_info_dir = '{0}.egg-info'.format(name.replace('-', '_'))
            for dirpath, dirnames, _ in os.walk(dist_dir):
                for directory in dirnames:
                    if egg_info_dir == directory.lower():
                        requires = os.path.join(dirpath, directory, 'PKG-INFO')
                        if os.path.exists(requires):
                            return os.path.join(dirpath, directory)

        if not self._has_egg_info():
            return ""

        return _get_egg_info_path()

    def _has_egg_info(self):
        if hasattr(self, "_egg_info_call_cache"):
            return True

        logger.debug('- Running egg_info in %s' % (self.dist_dir,))
        try:
            if self.python_path:
                os.environ["PYTHONPATH"] = self.python_path
            subprocess.check_output(
                [self.exe, "setup.py", "egg_info"],
                cwd=self.dist_dir)
        except subprocess.CalledProcessError:
            logger.warn(
                "!! egg_info failed for %s", self.dist_dir.rsplit('/', 1)[-1])
            return False

        self._egg_info_call_cache = True

        return True

    def _get_package_setup_arguments(self):
        """Mocks setuptools and distutils to get setup arguments"""

        if hasattr(self, "_pkg_setup_arguments_call_cache"):
            return self._pkg_setup_arguments_call_cache

        if not os.path.exists(os.path.join(self.dist_dir, "setup.py")):
            return {}

        logger.debug('- Running setup.py in %s' % (self.dist_dir,))
        try:
            if self.python_path:
                os.environ["PYTHONPATH"] = self.python_path
            out = subprocess.check_output([
                self.exe, '-c',
                'import setuptools, distutils, json, sys, runpy;'
                'dump = lambda **args: sys.stdout.write("#**#"+json.dumps({'
                '"name": args.get("name"), "version": args.get("version"),'
                '"install_requires": args.get("install_requires"),'
                '"setup_requires": args.get("setup_requires"),'
                '"tests_require": args.get("tests_require"),'
                '"test_suite": args.get("test_suite"),'
                '"requires": args.get("requires")}) + "#**#");'
                'setuptools.setup=dump; distutils.core.setup=dump;'
                'runpy.run_module("setup", run_name="__main__")'
            ], cwd=self.dist_dir)
            parsed = json.loads(out.partition('#**#')[-1].rpartition('#**#')[0])
        except subprocess.CalledProcessError:
            logger.warn("!! setup extract failed for %s", getattr(self, "name", "noname"))
            return None
        except ValueError:
            logger.warn("!! setup extract failed for %s, parse error", getattr(self, "name", "noname"))
            logger.warn(out)
            return None
        self._pkg_setup_arguments_call_cache = parsed
        return parsed


class PackageManager(object):
    """Interface to packages."""

    def __init__(
        self, overrides={}, versions=[], extra=(), dependency_links=[],
        exe=sys.executable, python_path="",
        download_cache_root="", cache=None,
        link_hook=lambda overrides, spec, link: (link, None),
        dependency_hook=lambda overrides, spec, deps, package: deps,
        spec_hook=lambda overrides, spec: spec
    ):
        self.extra = extra
        self.exe, self.python_path = exe, python_path
        self.overrides = overrides or {}
        self.versions = versions or []

        self._dependency_hook = dependency_hook
        self._link_hook = link_hook
        self._spec_hook = spec_hook

        self.finder = PackageFinder(
            find_links=[],
            index_urls=['https://pypi.python.org/simple/'],
            use_mirrors=True,
            mirrors=[],
            allow_all_external=True,
            allow_all_insecure=True
        )
        self.finder.add_dependency_links(dependency_links)

        self.download_cache_root = download_cache_root
        cache = cache or defaultdict(dict)
        self._link_cache = cache["link_cache"]
        self._dep_cache = cache["dep_cache"]
        self._pkg_info_cache = cache["pkg_info_cache"]
        self._extract_cache = cache["extract_cache"]
        self._best_match_call_cache = {}
        self._dep_call_cache = {}
        self._pkg_info_call_cache = {}

    def find_best_match(self, spec):
        def _find_cached_match(spec):
            #if spec.is_pinned:
                ## If this is a pinned spec, we can take a shortcut: if it is
                ## found in the dependency cache, we can safely assume it has
                ## been downloaded before, and thus must exist.  We can know
                ## this without every reaching out to PyPI and avoid the
                ## network overhead.
                #name, version = spec.name, first(spec.preds)[1]
                #if (name, version) in self._dep_cache:
                    #source = 'dependency cache'
                    #return version, source
            version = None
            overrides = self.overrides.get(spec.name)

            ## Try the link cache, and otherwise, try PyPI
            if (spec.no_extra, overrides) in self._link_cache:
                link, version = self._link_cache[(spec.no_extra, overrides)]
                source = 'link cache'
            else:
                try:
                    requirement = InstallRequirement.from_line(specline)
                    link = self.finder.find_requirement(requirement, False)
                except DistributionNotFound:
                    requirement = InstallRequirement.from_line(
                        specline, prereleases=True)
                    link = self.finder.find_requirement(requirement, False)

                link, version = self._link_hook(overrides, spec, link)

                # Hack to make pickle work
                link.comes_from = None
                source = 'PyPI'

                if link.egg_fragment:
                    version = link.egg_fragment.rsplit('-', 1)[1]
                    link = Link(
                        link.url_without_fragment + "#%s=%s" % self.get_hash(link)
                    )
                elif not version:
                    _, version = splitext(link.filename)[0].rsplit('-', 1)

                # It's more reliable to get version from pinned spec then filename
                if spec.is_pinned:
                    version = spec.pinned

                assert version, "Version must be set!"
                self._link_cache[(spec.no_extra, overrides)] = (link, version)

                # Take this moment to smartly insert the pinned variant of this
                # spec into the link_cache, too
                pinned_spec = Spec.from_pinned(spec.name, version)
                self._link_cache[pinned_spec.fullname] = (link, version)

            return version, source

        version = next((v for v in self.versions if v.name == spec.name), None)
        if version:
            spec.pinned = version.pinned

        specline = spec.no_extra
        if '==' not in specline or specline not in self._best_match_call_cache:
            logger.debug('- Finding best package matching %s' % spec)
        version = next((v for v in self.versions if v.name == spec.name), None)
        with logger.indent():
            version, source = _find_cached_match(spec)
        if '==' not in specline or specline not in self._best_match_call_cache:
            logger.debug('  Found best match: %s (from %s)' % (version, source))
        self._best_match_call_cache[specline] = True

        return version

    def get_dependencies(self, name, version, extra=()):
        """Gets list of dependencies from package"""
        spec = Spec.from_pinned(name, version, extra=extra)
        overrides = self.overrides.get(spec.name)
        extra = self.extra + extra

        if spec not in self._dep_call_cache:
            logger.debug('- Getting dependencies for %s-%s' % (name, version))
        with logger.indent():
            deps = self._dep_cache.get((spec, overrides))
            links = self._dep_cache.get((spec, overrides, "links"))
            if deps is not None and links is not None:
                source = 'dependency cache'
            else:
                package = self.get_package(spec)

                deps = package.get_deps(extra=extra)
                deps = self._dependency_hook(overrides, spec, deps, package)
                self._dep_cache[(spec, overrides)] = deps

                links = package.get_dependency_links()
                self._dep_cache[(spec, overrides, "links")] = links

                source = 'package archive'

        # Run spec hook
        deps = [
            (self._spec_hook(self.overrides.get(dep.name), dep), src)
            if self.overrides.get(dep.name) else (dep, src)
            for dep, src in deps
        ]

        if spec not in self._dep_call_cache:
            logger.debug('  Found: %s (from %s)' % (deps, source))

            # At this point do not forget to add dependency links
            self.finder.add_dependency_links(links)

        self._dep_call_cache[spec] = True
        return deps

    def get_pkg_info(self, name, version):
        spec = Spec.from_pinned(name, version)

        if spec.no_extra not in self._pkg_info_call_cache:
            logger.debug('- Getting pkginfo for %s-%s' % (name, version))
        with logger.indent():
            pkg_info = self._pkg_info_cache.get(spec.no_extra)
            if pkg_info is not None:
                source = 'pkg_info cache'
            else:
                package = self.get_package(spec)

                pkg_info = package.get_pkginfo()
                pkg_info["has_tests"] = package.has_tests()
                self._pkg_info_cache[spec.no_extra] = pkg_info
                source = 'package archive'

        if spec.no_extra not in self._pkg_info_call_cache:
            logger.debug('  Found pkg_info (from %s)' % (source,))

        self._pkg_info_call_cache[spec.no_extra] = True
        return pkg_info

    def get_link(self, name, version):
        logger.debug('- Getting link for %s-%s' % (name, version))
        spec = Spec.from_pinned(name, version)
        self.find_best_match(spec)
        return self._link_cache[spec.fullname]

    def get_hash(self, link):
        if link.hash and link.hash_name:
            return (link.hash_name, link.hash)

        def md5hash(path):
            return ("md5",  hashlib.md5(open(path, 'rb').read()).hexdigest())

        url = link.url_without_fragment
        logger.info('- Hashing package on url %s' % (url,))

        with logger.indent():
            fullpath = self._get_local_package_path(link.url_without_fragment)

            if os.path.exists(fullpath):
                logger.info('  Archive cache hit: {0}'.format(link.filename))
                return md5hash(fullpath)

            return md5hash(self._download_package(link))

    def get_package(self, spec):
        path = self._get_or_download_package(spec.fullname)
        return Package(
            package_dir=self._extract(path),
            exe=self.exe, python_path=self.python_path
        )

    # Helper methods
    def _get_local_package_path(self, url):  # noqa
        """Returns the full local path name for a given URL.  This
        does not require the package archive to exist locally.  In fact, this
        can be used to calculate the destination path for a download.
        """
        cache_key = quote(url, '')
        fullpath = os.path.join(self.download_cache_root, cache_key)
        return fullpath

    def _get_or_download_package(self, specline):
        """Returns the local path from the package cache, downloading as
        needed.
        """
        logger.debug('- Getting package location for %s' % (specline,))
        with logger.indent():
            link, version = self._link_cache[specline]
            fullpath = self._get_local_package_path(link.url_without_fragment)

            if os.path.exists(fullpath):
                logger.info('  Archive cache hit: {0}'.format(link.filename))
                return fullpath

            logger.info('  Archive cache miss, downloading {0}...'.format(
                link.filename
            ))
            return self._download_package(link)

    def _download_package(self, link):
        """Downloads the given package link contents to the local
        package cache. Overwrites anything that's in the cache already.
        """
        url = link.url_without_fragment
        logger.info('- Downloading package from %s' % (url,))
        with logger.indent():
            fullpath = self._get_local_package_path(url)
            response = _get_response_from_url(url, link)
            _download_url(response, link, fullpath)
            return fullpath

    def _unpack_archive(self, path, target_directory):
        logger.debug('- Unpacking %s' % (path,))
        with logger.indent():
            if path.endswith('.zip'):
                archive = zipfile.ZipFile(path)
            else:
                archive = tarfile.open(path)

            try:
                archive.extractall(target_directory)
            except IOError:
                logger.error("Error extracting %s" % (path,))
                raise
            finally:
                archive.close()

    def _extract(self, path):
        if path in self._extract_cache:
            return self._extract_cache[path]

        logger.info('- Extracting package %s' % (path,))

        build_dir = tempfile.mkdtemp()
        atexit.register(shutil.rmtree, build_dir)
        unpack_dir = os.path.join(build_dir, 'build')
        self._unpack_archive(path, unpack_dir)

        # Cache unpack
        self._extract_cache[path] = unpack_dir

        return unpack_dir

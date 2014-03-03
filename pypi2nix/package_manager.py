import json
import operator
import os
import shutil
import atexit
import subprocess
import sys
import tarfile
import tempfile
import zipfile

try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote  # noqa

try:
    import cPickle as pickle
except ImportError:
    import pickle as pickle  # noqa

from functools import partial
import urlparse

from pip.exceptions import DistributionNotFound
#from pip.backwardcompat import ConfigParser
from pip.download import _download_url, _get_response_from_url
from pip.index import Link, PackageFinder
#from pip.locations import default_config_file
from pip.req import InstallRequirement
from pip.util import splitext
from email.parser import FeedParser

from .log import logger
from .datastructures import Spec, first
from .version import NormalizedVersion  # PEP386 compatible version numbers


def url_without_fragment(link):
    """Included here for compatibility reasons with pip<1.2, which does not
    have the Link.url_without_fragment() method.
    """
    assert isinstance(link, Link), 'Argument should be a pip.index.Link instance.'
    try:
        return link.url_without_fragment
    except AttributeError:
        scheme, netloc, path, query, fragment = urlparse.urlsplit(link.url)
        return urlparse.urlunsplit((scheme, netloc, path, query, None))


class NoPackageMatch(Exception):
    pass


class BasePackageManager(object):
    def find_best_match(self, spec):
        """Return a version string that indicates the best match for the given
        Spec.
        """
        raise NotImplementedError('Implement this in a subclass.')

    def get_dependencies(self, name, version, extra=()):
        """Return a list of Spec instances, representing the dependencies of
        the specific package version indicated by the args.  This method only
        returns the direct (next-level) dependencies of the package.
        The Spec instances don't require sources to be set by this method.
        """
        raise NotImplementedError('Implement this in a subclass.')


class FakePackageManager(BasePackageManager):
    def __init__(self, fake_contents):
        """Creates a fake package manager index, for easy testing.  The
        fake_contents argument is a dictionary containing 'name-version' keys
        and lists-of-specs values.

        Example:

            {
                'foo-0.1': ['bar', 'qux'],
                'bar-0.2': ['qux>0.1'],
                'qux-0.1': [],
                'qux-0.2': [],
            }
        """
        # Sanity check (parsing will return errors if content is wrongly
        # formatted)
        for pkg_key, list_of_specs in fake_contents.items():
            try:
                _, _ = self.parse_package_key(pkg_key)
            except ValueError:
                raise ValueError('Invalid index entry: %s' % (pkg_key,))
            assert isinstance(list_of_specs, list)

        self._contents = fake_contents

    def parse_package_key(self, pkg_key):
        try:
            return pkg_key.rsplit('-', 1)
        except ValueError:
            raise ValueError('Invalid package key: %s (required format: "name-version")' % (pkg_key,))

    def iter_package_versions(self):
        """Iters over all package versions, returning key-value pairs."""
        for key in self._contents:
            yield self.parse_package_key(key)

    def iter_versions(self, given_name):
        """Will return all versions available for the current package name."""
        for name, version in self.iter_package_versions():
            if name == given_name:
                yield version

    def matches_pred(self, version, pred):
        """Returns whether version matches the given predicate."""
        qual, value = pred
        ops = {
            '==': operator.eq,
            '!=': operator.ne,
            '<': operator.lt,
            '>': operator.gt,
            '<=': operator.le,
            '>=': operator.ge,
        }
        return ops[qual](NormalizedVersion(version), NormalizedVersion(value))

    def pick_highest(self, list_of_versions):
        """Picks the highest version from a list, according to PEP386 logic."""
        return str(max(map(NormalizedVersion, list_of_versions)))

    def find_best_match(self, spec):
        """This requires a bit of reverse engineering of PyPI's logic that
        finds a pacakge for a given spec, but it's not too hard.
        """
        versions = list(self.iter_versions(spec.name))
        for pred in spec.preds:
            is_version_match = partial(self.matches_pred, pred=pred)
            versions = filter(is_version_match, versions)
        if len(versions) == 0:
            raise NoPackageMatch('No package found for %s' % (spec,))
        return self.pick_highest(versions)

    def get_dependencies(self, name, version, extra=()):
        pkg_key = '%s-%s' % (name, version)
        specs = []
        for specline in self._contents[pkg_key]:
            specs.append(Spec.from_line(specline))
        return specs


class PersistentCache(object):
    def __init__(self, cache_file):
        """Creates a new persistent cache, retrieving/storing cached key-value
        pairs from/to the given filename.
        """
        self._cache_file = cache_file
        self._cache = None

    @property
    def cache(self):
        """The dictionary that is the actual in-memory cache.  This property
        lazily loads the cache from disk.
        """
        if self._cache is None:
            self.read_cache()
        return self._cache

    def read_cache(self):
        """Reads the cached contents into memory."""
        if os.path.exists(self._cache_file):
            with open(self._cache_file, 'r') as f:
                self._cache = pickle.load(f)
        else:
            # Create a new, empty cache otherwise (store a __format__ field
            # that can be used to version the file, should we need to make
            # changes to its internals)
            self._cache = {'__format__': 1}

    def write_cache(self):
        """Writes (pickles) the cache to disk."""
        with open(self._cache_file, 'w') as f:
            pickle.dump(self._cache, f)

    def __contains__(self, item):
        return item in self.cache

    def __getitem__(self, key):
        return self.cache[key]

    def __setitem__(self, key, value):
        self.cache[key] = value
        self.write_cache()

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class PackageManager(BasePackageManager):
    """The default package manager that goes to PyPI and caches locally."""
    dep_cache_file = lambda _, env: os.path.join(os.path.expanduser('~'), '.pip-tools', '%s-dependencies.pickle' %env)
    pkg_info_cache_file = lambda _, env: os.path.join(os.path.expanduser('~'), '.pip-tools', '%s-pkginfo.pickle' %env)
    download_cache_root = os.path.join(os.path.expanduser('~'), '.pip-tools', 'cache')

    def __init__(
        self, extra=(),
        exe=sys.executable, env=sys.version_info.major+sys.version_info.minor,
        python_path = ""
    ):
        # TODO: provide options for pip, such as index URL or use-mirrors
        if not os.path.exists(self.download_cache_root):
            os.makedirs(self.download_cache_root)
        self._link_cache = {}
        self._dep_cache = PersistentCache(self.dep_cache_file(env))
        self._pkg_info_cache = PersistentCache(self.pkg_info_cache_file(env))
        self._extract_cache = {}
        self._dep_call_cache = {}
        self._best_match_call_cache = {}
        self._pkg_info_call_cache = {}

        self.extra = extra
        self.env = env
        self.exe = exe
        self.python_path = python_path

    # BasePackageManager interface
    def find_best_match(self, spec):
        # TODO: if the spec is pinned, we might be able to go straight to the
        # local cache without having to use the PackageFinder. Cached file
        # names look like this:
        # https%3A%2F%2Fpypi.python.org%2Fpackages%2Fsource%2Fs%2Fsix%2Fsix-1.2.0.tar.gz
        # This is easy to guess from a package==version spec but requires the
        # package to be actually hosted on pypi, which is not the case for
        # everything (e.g. redis).
        #
        # Option 1: make this work for packages hosted on PyPI and accept
        # external packages to be slower.
        #
        # Option 2: only use the last part of the URL as a file name
        # (six-1.2.0.tar.gz). This makes it easy to check the local cache for
        # any pinned spec but *might* lead to inconsistencies for people
        # maintaining their own PyPI servers and adding their modified
        # packages as the same names/versions as the originals on the
        # canonical PyPI. The shouldn't do it, and this is probably an edge
        # case but it's still worth making a decision.

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

            ## Try the link cache, and otherwise, try PyPI
            if specline in self._link_cache:
                link = self._link_cache[specline]
                source = 'link cache'
            else:
                finder = PackageFinder(
                    find_links=[],
                    index_urls=['https://pypi.python.org/simple/'],
                    use_mirrors=True,
                    mirrors=[],
                    allow_all_external=True,
                    allow_all_insecure=True
                )

                try:
                    requirement = InstallRequirement.from_line(specline)
                    link = finder.find_requirement(requirement, False)
                except DistributionNotFound:
                    requirement = InstallRequirement.from_line(
                        specline, prereleases=True)
                    link = finder.find_requirement(requirement, False)

                self._link_cache[specline] = link
                source = 'PyPI'
            _, version = splitext(link.filename)[0].rsplit('-', 1)

            # Take this moment to smartly insert the pinned variant of this
            # spec into the link_cache, too
            pinned_spec = Spec.from_pinned(spec.name, version)
            if pinned_spec not in self._link_cache:
                self._link_cache[str(pinned_spec)] = link
            return version, source

        specline = str(spec)
        if '==' not in specline or specline not in self._best_match_call_cache:
            logger.debug('- Finding best package matching %s' % [specline])
        with logger.indent():
            version, source = _find_cached_match(spec)
        if '==' not in specline or specline not in self._best_match_call_cache:
            logger.debug('  Found best match: %s (from %s)' % (version, source))
        self._best_match_call_cache[specline] = True
        return version

    def extract(self, path):
        if path in self._extract_cache:
            return self._extract_cache[path]

        logger.debug('- Extracting package %s' % (path,))

        build_dir = tempfile.mkdtemp()
        atexit.register(shutil.rmtree, build_dir)
        unpack_dir = os.path.join(build_dir, 'build')
        self.unpack_archive(path, unpack_dir)

        # Cache unpack
        self._extract_cache[path] = unpack_dir

        return unpack_dir

    def get_dependencies(self, name, version, extra=()):
        key = '{0}-{1}-{2}'.format(name, version, sorted(extra))
        if key not in self._dep_call_cache:
            logger.debug('- Getting dependencies for %s-%s' % (name, version))
        with logger.indent():
            deps = self._dep_cache.get((name, version, extra))
            if deps is not None:
                source = 'dependency cache'
            else:
                spec = Spec.from_pinned(name, version)
                path = self.get_or_download_package(str(spec))
                package_dir = self.extract(path)
                deps = self.extract_egginfo(package_dir, extra)

                # distutils does not provide egg_info and tests_require is not
                # written out
                to_list = lambda x: x if isinstance(x, list) else [x]
                setup_args = self.get_package_setup_arguments(package_dir) or {}
                if not deps:
                    deps += [
                        (str(p), None) for p in
                        to_list(setup_args.get("install_requires") or []) +
                        to_list(setup_args.get("requires") or [])
                        if p
                    ]
                # This should be written by egg_info, but it's not
                deps += [
                    (str(p), 'tests_require')
                    for p in to_list(setup_args.get("tests_require") or []) if p
                ]
                # Hardcoded nose collector test suite fix
                if (
                    "nose.collector" in (setup_args.get("test_suite") or "")
                    and name != "nose"
                ):
                    deps += [('nose', 'tests')]

                self._dep_cache[(name, version, extra)] = deps
                source = 'package archive'
        if key not in self._dep_call_cache:
            logger.debug('  Found: %s (from %s)' % (deps, source))
        self._dep_call_cache[key] = True
        return [(Spec.from_line(dep), section) for dep, section in deps]

    def get_pkg_info(self, name, version):
        key = '{0}-{1}'.format(name, version)
        if key not in self._pkg_info_call_cache:
            logger.debug('- Getting pkginfo for %s-%s' % (name, version))
        with logger.indent():
            pkg_info = self._pkg_info_cache.get((name, version))
            if pkg_info is not None:
                source = 'pkg_info cache'
            else:
                spec = Spec.from_pinned(name, version)
                path = self.get_or_download_package(str(spec))
                pkg_info = self.extract_pkginfo(self.extract(path))
                self._pkg_info_cache[(name, version)] = pkg_info
                source = 'package archive'
        if key not in self._pkg_info_call_cache:
            logger.debug('  Found pkg_info (from %s)' % (source,))
        self._pkg_info_call_cache[key] = True
        return pkg_info

    def get_hash(self, name, version):
        logger.debug('- Getting hash for %s-%s' % (name, version))
        spec = Spec.from_pinned(name, version)
        link = self._link_cache[str(spec)]

        return (link.hash_name, link.hash)

    def get_url(self, name, version):
        logger.debug('- Getting url for %s-%s' % (name, version))
        spec = Spec.from_pinned(name, version)
        link = self._link_cache[str(spec)]

        return link.url

    # Helper methods
    def get_local_package_path(self, url):  # noqa
        """Returns the full local path name for a given URL.  This
        does not require the package archive to exist locally.  In fact, this
        can be used to calculate the destination path for a download.
        """
        cache_key = quote(url, '')
        fullpath = os.path.join(self.download_cache_root, cache_key)
        return fullpath

    def get_or_download_package(self, specline):
        """Returns the local path from the package cache, downloading as
        needed.
        """
        logger.debug('- Getting package location for %s' % (specline,))
        with logger.indent():
            link = self._link_cache[str(specline)]
            fullpath = self.get_local_package_path(url_without_fragment(link))

            if os.path.exists(fullpath):
                logger.debug('  Archive cache hit: {0}'.format(link.filename))
                return fullpath

            logger.debug('  Archive cache miss, downloading {0}...'.format(
                link.filename
            ))
            return self.download_package(link)

    def extract_pkginfo(self, package_dir):
        with logger.indent():
            name = os.listdir(package_dir)[0]
            egg_info_dir = self.get_package_egg_info_path(package_dir)
            pkg_info_path = os.path.join(
                egg_info_dir or os.path.join(package_dir, name), "PKG-INFO")

            if not os.path.exists(pkg_info_path):
                raise Exception("PKG-INFO not found %s" % name)

            with open(pkg_info_path, 'r') as pkg_info:
                data = pkg_info.read()
                p = FeedParser()
                p.feed(data)

            return p.close()

    # def get_pip_cache_root():
    #     """Returns pip's cache root, or None if no such cache root is
    #     configured.
    #     """
    #     pip_config = ConfigParser.RawConfigParser()
    #     pip_config.read([default_config_file])
    #     download_cache = None
    #     try:
    #         for key, value in pip_config.items('global'):
    #             if key == 'download-cache':
    #                 download_cache = value
    #                 break
    #     except ConfigParser.NoSectionError:
    #         pass
    #     if download_cache is not None:
    #         download_cache = os.path.expanduser(download_cache)
    #     return download_cache

    def download_package(self, link):
        """Downloads the given package link contents to the local
        package cache. Overwrites anything that's in the cache already.
        """
        # TODO integrate pip's download-cache
        #pip_cache_root = self.get_pip_cache_root()
        #if pip_cache_root:
        #    cache_path = os.path.join(pip_cache_root, cache_key)
        #    if os.path.exists(cache_path):
        #        # pip has a cached version, copy it
        #        shutil.copyfile(cache_path, fullpath)
        #else:
        #    actually download the requirement
        url = url_without_fragment(link)
        logger.debug('- Downloading package from %s' % (url,))
        with logger.indent():
            fullpath = self.get_local_package_path(url)
            response = _get_response_from_url(url, link)
            _download_url(response, link, fullpath)
            return fullpath

    def unpack_archive(self, path, target_directory):
        logger.debug('- Unpacking %s' % (path,))
        with logger.indent():
            if (path.endswith('.tar.gz') or
                path.endswith('.tar') or
                path.endswith('.tar.bz2') or
                path.endswith('.tgz')):

                archive = tarfile.open(path)
            elif path.endswith('.zip'):
                archive = zipfile.ZipFile(path)
            else:
                assert False, "Unsupported archive file: {}".format(path)

            try:
                archive.extractall(target_directory)
            except IOError:
                logger.error("Error extracting %s" % (path,))
                raise
            finally:
                archive.close()

    def has_egg_info(self, dist_dir):
        logger.debug('- Running egg_info in %s' % (dist_dir,))
        logger.debug('  (This can take a while.)')
        with logger.indent():
            try:
                if self.python_path:
                    os.environ["PYTHONPATH"] = self.python_path
                subprocess.check_call(
                    [self.exe, "setup.py", "egg_info"],
                    cwd=dist_dir
                )
            except subprocess.CalledProcessError:
                logger.debug("  egg_info failed for {0}".format(
                    dist_dir.rsplit('/', 1)[-1]
                ))
                return False
            return True

    def get_package_egg_info_path(self, package_dir):
        """Gets package egginfo path"""
        name = os.listdir(package_dir)[0]
        dist_dir = os.path.join(package_dir, name)
        name, version = name.rsplit('-', 1)

        def _get_egg_info_path():
            egg_info_dir = '{0}.egg-info'.format(name.replace('-', '_'))
            for dirpath, dirnames, _ in os.walk(dist_dir):
                if egg_info_dir in dirnames:
                    requires = os.path.join(dirpath, egg_info_dir,
                                            'requires.txt')
                    if os.path.exists(requires):
                        return os.path.join(dirpath, egg_info_dir)

        egg_info_path = _get_egg_info_path()
        if egg_info_path:
            return egg_info_path
        else:
            if not self.has_egg_info(dist_dir):
                return ""

            return _get_egg_info_path()

    def get_package_setup_arguments(self, package_dir):
        """Mocks setuptools and distutils to get setup arguments"""
        name = os.listdir(package_dir)[0]
        dist_dir = os.path.join(package_dir, name)

        logger.debug('- Running setup.py in %s' % (dist_dir,))
        logger.debug('  (This can take a while.)')
        with logger.indent():
            try:
                if self.python_path:
                    os.environ["PYTHONPATH"] = self.python_path
                out = subprocess.check_output([
                    self.exe, '-c',
                    'import setuptools, distutils, json, sys;'
                    'dump = lambda **args: sys.stdout.write("#**#"+json.dumps({'
                    '"install_requires": args.get("install_requires"),'
                    '"tests_require": args.get("tests_require"),'
                    '"test_suite": args.get("test_suite"),'
                    '"requires": args.get("requires")}) + "#**#");'
                    'setuptools.setup=dump; distutils.core.setup=dump; import setup'
                ],
                cwd=dist_dir)
                out = json.loads(out.partition('#**#')[-1].rpartition('#**#')[0])
            except subprocess.CalledProcessError:
                logger.debug("  setup extract failed for {0}".format(
                    dist_dir.rsplit('/', 1)[-1]
                ))
                return None
            except ValueError:
                logger.debug("  setup extract failed for {0}".format(
                    dist_dir.rsplit('/', 1)[-1]
                ))
                return None
            return out

    def read_package_requires_file(self, package_dir, extra=()):
        """Returns a list of dependencies for an unpacked package dir."""
        egg_info_dir = self.get_package_egg_info_path(package_dir)
        if egg_info_dir:
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
                    skip_section = not section in (extra + self.extra)
                    continue
                if not skip_section:
                    deps.append((dep, section))
        return deps

    def extract_egginfo(self, unpack_dir, extra=()):
        """Returns a list of string representations of dependencies for
        a given distribution.
        """
        with logger.indent():
            deps = self.read_package_requires_file(unpack_dir, extra)

        logger.debug('Found: %s' % (deps,))
        return deps


if __name__ == '__main__':
    pass

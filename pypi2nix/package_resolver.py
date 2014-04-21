import os
import sys
import requests

from collections import defaultdict
from functools import partial
from urlparse import urlparse
from jinja2 import Environment
from pip.index import Link
from pip.util import splitext

from .log import logger
from .datastructures import Spec, SpecSet
from .package_manager import PackageManager
from .dependency_resolver import DependencyResolver

env = Environment()


class PackageResolver(object):
    def __init__(
        self, env,
        exe=sys.executable, python_path=":".join(sys.path),
        download_cache_root="/tmp", cache=defaultdict(dict),
        overrides={}, extra=("test", "tests", "testing")
    ):

        # Create cache root and download cache root if it does not exist
        #if not os.path.exists(self.cache_root):
        #    os.makedirs(self.cache_root)
        #if not os.path.exists(self.download_cache_root):
        #    os.makedirs(self.download_cache_root)
        #
        self.package_manager = partial(
            PackageManager,
            exe=exe, python_path=python_path,
            cache=cache, download_cache_root=download_cache_root,
            link_hook=self._link_hook,
            dependency_hook=self._dependency_hook,
            version_hook=self.version_hook
        )

        # This options can be additionaly defined by each package
        self.env = env
        self.extra = extra
        self.overrides = overrides

    def _parse_buildout(content):
        return [], []

    def _parse_requirements(content):
        versions = []
        for line in content.split():
            if line[0] == "#":
                continue
            versions.append(Spec.from_line(line, source="requirements.txt"))

        return versions, []

    def _link_hook(self, overrides, spec, link):
        if overrides.get("src"):
            _, version = splitext(link.filename)[0].rsplit('-', 1)
            spec = Spec.from_pinned(name=spec.name, version=version)
            src = env.from_string(
                overrides.get("src")).render({"spec": spec})
            link = Link(src)

        # Hack to make pickle work
        link.comes_from = None

        return link

    def _parse_versions(self, versions, spec, package):
        versions = []
        links = []
        for line in versions:
            # Pass line through template and parse as url
            url = urlparse(env.from_string(line).render({"spec": spec}))
            if not url.scheme:
                versions.append(Spec.from_line(line, source="overrides"))
            elif url.scheme == "file":
                content = package.read_file(url.netloc + url.path)
            elif url.scheme == "http" or url.scheme == "https":
                content = requests.get(url).read()

            if content:
                extension = os.path.splitext(url.path)[1]
                if "txt" in extension:
                    _versions, _links = self._parse_requirements(content)
                elif "cfg" in extension:
                    _versions, _links = self._parse_buildout(content)

                versions += _versions
                links += _links

        return versions, links

    def _version_hook(self, overrides, spec, package):
        if overrides.get("versions"):
            versions, links = self._parse_versions(overrides.get("versions"))
            return versions, links
        else:
            return [], []

    def _dependency_hook(self, overrides, spec, deps, package):
        """Hook for adding or replacing dependencies"""
        new_deps = []

        for dep in overrides.get("append_deps", []) + overrides.get("deps", []):
            new_deps.append(Spec.from_line(dep, source="dependency_hook"))

        if overrides.get("deps"):
            return new_deps
        else:
            return deps + new_deps

        return new_deps

    def _spec_hook(self, overrides, spec):
        """Hook which can replace speciffications"""
        if spec.name in overrides.get("spec"):
            return Spec.from_line(overrides.get("spec"), source="spec_hook")

    def _resolve(self, specs, versions, package_manager):
        logger.info('===> Collecting requirements')

        spec_set = SpecSet()
        for spec in specs + versions:
            spec_set.add_spec(spec)

        logger.info('===> Normalizing requirements')
        with logger.indent():
            spec_set = spec_set.normalize()
            for spec in spec_set:
                logger.info('- %s' % (spec,))

        logger.info('===> Resolving full tree')

        with logger.indent():
            resolver = DependencyResolver(
                spec_set, package_manager=package_manager,
                overrides=self.overrides, spec_hook=self._spec_hook
            )
            pinned_spec_set = resolver.resolve()

        logger.info('===> Pinned spec set resolved')
        with logger.indent():
            for spec in pinned_spec_set:
                logger.info('- %s' % (spec,))

        return pinned_spec_set

    def resolve(
        self, specs,
        versions=[], overrides={}, extra=set(), dependency_links=[]
    ):
        _overrides = {}
        _overrides.update(self.overrides.get("*", {}))
        _overrides.update(self.overrides.get(self.env, {}))
        _overrides.update(overrides)

        package_manager = self.package_manager(
            extra=self.extra + extra, overrides=_overrides,
            dependency_links=dependency_links,
        )

        target_specs = [Spec.from_line(spec) for spec in specs]
        versions = [Spec.from_line(spec) for spec in versions]
        pinned = self._resolve(target_specs, versions, package_manager)

        logger.info('===> Generating output dict')

        with logger.indent():
            result = {}
            for spec in pinned:
                package_manager.find_best_match(spec)
                pkg_info = package_manager.get_pkg_info(spec.name, spec.pinned)
                link = package_manager.get_link(spec.name, spec.pinned)
                hash = package_manager.get_hash(link)
                pkg = {
                    "name": spec.name,
                    "version": spec.pinned,
                    "src": {
                        "url": link.url, "algo": hash[0], "sum": hash[1]
                    },
                    "has_tests": pkg_info["has_tests"],
                    "deps": [], "extra": {},
                    "meta": {
                        "homepage": pkg_info["Home-page"]
                    } if pkg_info else {}
                }

                deps = package_manager.get_dependencies(
                    spec.name, spec.pinned, spec.extra)

                for dep, section in deps:
                    pinned_dep = first(pinned._byname[dep.name])

                    # skip dependencies pointing to ourself (recursive dependencies)
                    if spec.fullname == pinned_dep.fullname:
                        continue

                    if not section:
                        pkg["deps"].append(pinned_dep.fullname)
                    else:
                        if section not in pkg["extra"]:
                            pkg["extra"][section] = [pinned_dep.fullname]
                        else:
                            pkg["extra"][section].append(pinned_dep.fullname)

                result[spec.fullname] = pkg

        get_pinned = lambda name: \
            next((s.fullname for s in pinned if s.name == name))

        return (
            result, {
                target_spec.name:
                get_pinned(target_spec.name) for target_spec in target_specs
            }
        )



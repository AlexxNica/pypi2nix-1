import os
import sys
import requests
import ConfigParser
import StringIO

from collections import defaultdict
from functools import partial
from urlparse import urlparse
from jinja2 import Environment
from pip.index import Link
from pip.util import splitext

from .log import logger
from .datastructures import Spec, SpecSet, first
from .package_manager import PackageManager
from .dependency_resolver import DependencyResolver
from .caching import hashabledict

env = Environment()


class PackageResolver(object):
    def __init__(
        self,
        exe=sys.executable, python_path=":".join(sys.path),
        download_cache_root="/tmp", cache=defaultdict(dict),
        overrides={}, test_profile="top_level", remove_circular_deps=True,

        # Additional internal extra used
        extra=("_setup_requires",),
        test_extra=(
            "test", "tests", "testing", "_tests_require", "_test_suite"
        )
    ):

        self.package_manager = partial(
            PackageManager,
            exe=exe, python_path=python_path,
            cache=cache, download_cache_root=download_cache_root,
            link_hook=self._link_hook,
            dependency_hook=self._dependency_hook,
            spec_hook=self._spec_hook
        )

        self.extra = extra
        self.test_extra = test_extra
        self.test_profile = test_profile
        self.overrides = overrides
        self.remove_circular_deps = remove_circular_deps

    def _parse_buildout(self, content):

        def parse(text):
            versions = dict()

            fp = StringIO.StringIO(text)
            parser = ConfigParser.ConfigParser()
            parser.readfp(fp)

            if parser.has_section('buildout') and \
               parser.has_option('buildout', 'extends'):

                for url in parser.get('buildout', 'extends').split():
                    logger.info('===> Getting version from ' + url)
                    request = requests.get(url)
                    versions.update(parse(request.text))

            if parser.has_section('versions'):
                versions.update({
                    package: version
                    for package, version in parser.items('versions')
                })

            return versions

        return set([
            Spec.from_pinned(package, version)
            for package, version in parse(content).items()
        ]), set()

    def _parse_requirements(self, content, extra):
        versions = set()
        for line in content.split():
            if line[0] == "#":
                continue
            versions.update([
                Spec.from_line(line, extra=extra, source="requirements.txt")
            ])

        return versions, set()

    def _parse_versions(self, overrided_versions, spec=None, package=None):
        versions = set()
        links = set()
        content = None
        for line in overrided_versions:
            if isinstance(line, basestring):
                extra = tuple()
            else:
                line, extra = line[0], (line[1],)

            # Pass line through template and parse as url
            url = urlparse(
                env.from_string(line).render({"spec": spec} if spec else {})
            )
            if not url.scheme:
                versions.update([Spec.from_line(line, extra=extra, source="overrides")])
            elif url.scheme == "file" and package:
                logger.info('===> Getting version from ' + url.geturl())
                content = package.read_file(url.netloc + url.path)
            elif url.scheme == "http" or url.scheme == "https":
                logger.info('===> Getting version from ' + url.geturl())
                content = requests.get(url.geturl()).content

            if content:
                extension = os.path.splitext(url.path)[1]
                if "txt" in extension:
                    _versions, _links = self._parse_requirements(content, extra)
                elif "cfg" in extension:
                    _versions, _links = self._parse_buildout(content)

                versions.update(_versions)
                links.update(_links)

        return versions

    def _link_hook(self, overrides, spec, link):
        overrides = overrides or {}
        if overrides.get("src"):
            logger.info(
                '===> Link override %s found for package %s',
                overrides, spec)

            _, version = splitext(link.filename)[0].rsplit('-', 1)
            spec = Spec.from_pinned(name=spec.name, version=version)
            src = env.from_string(
                overrides.get("src")).render({"spec": spec})
            link = Link(src)

            # Hack to make pickle work
            link.comes_from = None

            return link, spec.pinned

        return link, None

    def _dependency_hook(self, overrides, spec, deps, package):
        """Hook for adding or replacing dependencies"""
        new_deps = []
        overrides = overrides or {}

        if any(k in overrides for k in ("append_deps", "new_deps", "replace_deps")):
            logger.info(
                '===> Dependency overrides %s found for package %s',
                overrides, spec)

        for dep in self._parse_versions(
            overrides.get("append_deps", tuple()) +
            overrides.get("new_deps", tuple()),
            spec, package
        ):
            new_deps.append((dep, None))

        # If we are not replacing all dependencies, just append then to deps
        if not overrides.get("new_deps"):
            new_deps = deps + new_deps

        # Replace defined dependencies
        if overrides.get("replace_deps"):
            # Override dependencies for package
            new_deps = set([
                (Spec.from_line(override, source="dependency_hook"), src)
                if dep.name == name else (dep, src)
                for dep, src in new_deps
                for name, override in
                overrides.get("replace_deps").iteritems()
            ])

        # Remove dependencies
        if overrides.get("remove_deps"):
            new_deps = [
                (dep, src) for dep, src in new_deps
                if dep.name not in overrides.get("remove_deps")
            ]

        # If testing profile is top_level and it is top_level package,
        # or testing_profile is none, then remove testing dependencies
        if not (self.test_profile == "top_level" and overrides.get("tlp")) or \
                self.test_profile == "none":
            new_deps = [
                (dep, src) for dep, src in new_deps
                if (src and src not in self.test_extra) or not src
            ]

        return new_deps

    def _spec_hook(self, overrides, spec):
        """Hook which can replace speciffications"""
        overrides = overrides or {}
        if overrides.get("spec"):
            logger.info(
                '===> Spec overrides %s found for package %s',
                overrides, spec)

            new_spec = Spec.from_line(overrides.get("spec"), source="spec_hook")
            if not new_spec.extra and spec.extra:
                new_spec._extra = spec._extra
            if not new_spec.preds and spec.preds:
                new_spec._preds = spec._preds

            return new_spec

        return spec

    def resolve(
        self, specs,
        versions=set(), overrides={}, extra=(), dependency_links=[]
    ):
        _overrides = {}
        _overrides.update(self.overrides)
        _overrides.update(overrides)

        target_specs = specs

        logger.info('===> Collecting requirements')

        spec_set = SpecSet()
        tlp = []  # Top level packages

        # Add specs to spec_set and add override for spec as top level packages
        for spec, source in target_specs:
            spec_set.add_spec(spec)
            _overrides[spec.name] = _overrides.get(spec.name, hashabledict())
            _overrides[spec.name].update(hashabledict(tlp=True))
            tlp.append(spec.name)

        # Parses versions
        versions = self._parse_versions(versions)

        # Create package manager
        package_manager = self.package_manager(
            extra=tuple(set(self.extra + self.test_extra + extra)),
            overrides=_overrides,
            versions=versions,
            dependency_links=dependency_links,
        )

        logger.info('===> Normalizing requirements')
        with logger.indent():
            spec_set = spec_set.normalize()
            for spec in spec_set:
                logger.info('- %s' % (spec,))

        logger.info('===> Resolving full tree')

        with logger.indent():
            resolver = DependencyResolver(
                spec_set, package_manager=package_manager)
            pinned = resolver.resolve()

        logger.info('===> Pinned spec set resolved')
        with logger.indent():
            for spec in pinned:
                logger.info('- %s' % (spec,))


        logger.info('===> Generating output dict')

        with logger.indent():
            result = {}
            for spec in pinned:
                package_manager.find_best_match(spec)
                pkg_info = package_manager.get_pkg_info(spec.name, spec.pinned)
                link, _ = package_manager.get_link(spec.name, spec.pinned)
                hash = package_manager.get_hash(link)
                pkg = {
                    "name": spec.name,
                    "fullname": spec.fullname,
                    "version": spec.pinned,
                    "extra": spec.extra,
                    "src": {
                        "url": link.url, "algo": hash[0], "sum": hash[1]
                    },
                    "has_tests":
                    (pkg_info["has_tests"] and self.test_profile == "all") or
                    (pkg_info["has_tests"] and spec.name in tlp
                     and self.test_profile == "top_level"),
                    "deps": [], "extra": {},
                    "meta": {
                        "homepage": pkg_info["Home-page"]
                    } if pkg_info else {},
                    "has_circular_deps": False, "checked": False
                }

                deps = package_manager.get_dependencies(
                    spec.name, spec.pinned, spec.extra)

                for dep, section in deps:
                    pinned_dep = first(pinned._byname[dep.name])

                    # skip dependencies pointing to ourself (recursive dependencies)
                    if spec.fullname == pinned_dep.fullname:
                        continue

                    full_dep = (pinned_dep.fullname, dep.extra)
                    if not section:
                        pkg["deps"].append(full_dep)
                    else:
                        if section not in pkg["extra"]:
                            pkg["extra"][section] = [full_dep]
                        else:
                            pkg["extra"][section].append(full_dep)

                result[spec.fullname] = pkg

        def _remove_circular_deps(pkg, visited=[]):
            if pkg["checked"]:
                return pkg

            new_deps = [
                (_remove_circular_deps(
                    result[dep], visited + [pkg["fullname"]]
                )["fullname"], extra)
                for dep, extra in pkg["deps"] if not dep in visited
            ]
            if new_deps != pkg["deps"]:
                logger.info('- Circular deps detected in package %s' %pkg["fullname"])
                pkg["has_circular_deps"] = True
                pkg["deps"] = new_deps

            for section in pkg["extra"]:
                new_deps = [
                    (_remove_circular_deps(
                        result[dep], visited + [pkg["fullname"]]
                    )["fullname"], extra)
                    for dep, extra in pkg["extra"][section] if not dep in visited
                ]
                if new_deps != pkg["extra"][section]:
                    logger.info(
                        '- Circular deps detected in package %s for extra %s'
                        % (pkg["fullname"], section))
                    pkg["has_circula_deps"] = True
                    pkg["extra"][section] = new_deps

            pkg["checked"] = True
            return pkg

        logger.info('===> Removing circular dependencies')

        def get_pinned(name):
            return next((s for s in pinned if s.name == name))

        if self.remove_circular_deps:
            for target_spec, source in target_specs:
                _remove_circular_deps(
                    result[get_pinned(target_spec.name).fullname])

        return (
            result, {
                spec.name:
                (get_pinned(spec.name), src) for spec, src in target_specs
            }
        )

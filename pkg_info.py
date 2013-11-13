import logging

from piptools.logging import logger
from piptools.package_manager import PackageManager
from piptools.datastructures import Spec, SpecSet, first
from piptools.resolver import Resolver

#spec = Spec.from_line("sentry")
#package_manager = PackageManager()
#version = package_manager.find_best_match(spec)
#pkg_info = package_manager.get_pkg_info(spec.name, version)
#hash = package_manager.get_hash(spec.name, version)


def setup_logging(verbose):
    if verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(message)s', None)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)


class PyNixResolver(object):
    def __init__(self):
        self.package_manager = PackageManager()

    def _resolve(self, name, versions={}):
        logger.debug('===> Collecting requirements')

        spec = Spec.from_line(name)
        spec_set = SpecSet()
        spec_set.add_spec(spec)

        for name, version in versions.iteritems():
            spec_set.add_spec(Spec.from_pinned(name, version))

        logger.debug('')
        logger.debug('===> Normalizing requirements')
        spec_set = spec_set.normalize()
        logger.debug('%s' % (spec_set,))

        logger.debug('')
        logger.debug('===> Resolving full tree')

        resolver = Resolver(spec_set, package_manager=self.package_manager)
        pinned_spec_set = resolver.resolve()

        logger.debug('')
        logger.debug('===> Pinned spec set resolved')
        for spec in pinned_spec_set:
            logger.debug('- %s' % (spec,))

        return pinned_spec_set

    def resolve(self, name, version={}, extra=("test", "tests")):
        self.package_manager.extra = extra

        pinned = self._resolve(name, version)

        logger.debug('')
        logger.debug('===> Generating output dict')

        result = {}
        for spec in pinned:
            self.package_manager.find_best_match(spec)
            pkg_info = self.package_manager.get_pkg_info(spec.name, spec.pinned)
            hash = self.package_manager.get_hash(spec.name, spec.pinned)
            pkg = result[spec.fullname] = {
                "src": {
                    "url": self.package_manager.get_url(spec.name, spec.pinned),
                    hash[0]: hash[1]
                },
                "deps": [],
                "extra": {},
                "meta": {
                    "homepage": pkg_info["Home-page"]
                } if pkg_info else {}
            }

            deps = self.package_manager.get_dependencies(
                spec.name, spec.pinned, spec.extra)
            for dep, section in deps:
                pinned_dep = first(pinned._byname[dep.name])
                if not section:
                    pkg["deps"].append(pinned_dep.fullname)
                else:
                    if section not in pkg["extra"]:
                        pkg["extra"][section] = [pinned_dep.fullname]
                    else:
                        pkg["extra"][section].append(pinned_dep.fullname)

        return result

setup_logging(True)
resolver = PyNixResolver()
resolved = resolver.resolve("sentry[postgres]", {"Django": "1.5.4"})

import pdb; pdb.set_trace()


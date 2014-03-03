import logging

from .log import logger
from .package_manager import PackageManager
from .datastructures import Spec, SpecSet, first
from .resolver import Resolver

from jinja2 import Environment, PackageLoader

env = Environment(loader=PackageLoader('pypi2nix', 'templates'))
pypi2nix_template = env.get_template('python-packages-generated.nix.jinja2')
env.globals['toset'] = lambda x: set(x)


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
    def __init__(self, **kwargs):
        self.package_manager = PackageManager(**kwargs)

    def _resolve(self, spec, versions=[]):
        logger.debug('===> Collecting requirements')

        spec_set = SpecSet()
        spec_set.add_spec(spec)

        for line in versions:
            spec_set.add_spec(Spec.from_line(line))

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

    def resolve(self, spec, versions=[], extra=("test", "tests", "testing")):
        self.package_manager.extra = extra

        target_spec = Spec.from_line(spec)
        pinned = self._resolve(
            target_spec,
            [(v if isinstance(v, basestring) else v[1]) for v in versions]
        )

        logger.debug('')
        logger.debug('===> Generating output dict')

        result = {}
        for spec in pinned:
            self.package_manager.find_best_match(spec)
            pkg_info = self.package_manager.get_pkg_info(spec.name, spec.pinned)
            hash = self.package_manager.get_hash(spec.name, spec.pinned)
            pkg = {
                "name": spec.name,
                "version": spec.pinned,
                "src": {
                    "url": self.package_manager.get_url(
                        spec.name, spec.pinned),
                    "algo": hash[0],
                    "sum": hash[1]
                },
                "deps": [],
                "extra": {},
                "meta": {
                    "homepage": pkg_info["Home-page"]
                } if pkg_info else {}
            }

            deps = self.package_manager.get_dependencies(
                spec.name, spec.pinned, spec.extra)

            # Add dependencies
            matched = next((line for line in versions if (
                not isinstance(line, basestring)) and line[0] == spec.name
            ), None)
            if matched:
                deps += [
                    (line, matched[2] if len(matched) > 2 else None)
                    for line in pinned if matched[1] == line.name
                ]

            for dep, section in deps:
                pinned_dep = first(pinned._byname[dep.name])
                if not section:
                    pkg["deps"].append(pinned_dep.fullname)
                else:
                    if section not in pkg["extra"]:
                        pkg["extra"][section] = [pinned_dep.fullname]
                    else:
                        pkg["extra"][section].append(pinned_dep.fullname)

            result[spec.fullname] = pkg

        return (
            result,
            next((s.fullname for s in pinned if s.name == target_spec.name))
        )


def main():
    setup_logging(True)
    spec_pkgs = {
        "sentry": {
            "python2.7": {
                "spec": "sentry[postgres]",
                "versions": ["Django==1.5.5", ("sentry", "pysqlite")],
            },
            "pypy": {
                "spec": "sentry[postgres_pypy]",
                "versions": ["Django==1.5.4"],
            }
        },
        "pyramid": {
            "python3.3m": {"spec": "pyramid"}
        }
    }
    envs = {
        "python2.7": PyNixResolver(exe="/home/offlinehacker/projects/pip-tools/result/python27/bin/python", env="python27", python_path="/home/offlinehacker/projects/pip-tools/result/python27/lib/python2.7/site-packages"),
        "python3.3m": PyNixResolver(exe="/home/offlinehacker/projects/pip-tools/result/python33/bin/python3", env="python33", python_path="/home/offlinehacker/projects/pip-tools/result/python33/lib/python3.3/site-packages"),
        "pypy": PyNixResolver(exe="/home/offlinehacker/projects/pip-tools/bin/python", env="pypy"),
    }
    enabled_envs = ["python2.7"]

    # For every specified package for each python environment resolve its
    # dependencies, then merge dependencies per env. Put pinned speciffied
    # packages per environment in resolved_pkgs and dependencies of all
    # packages per environment in resolved_envs
    resolved_envs = {}
    resolved_pkgs = {}
    for name, penvs in spec_pkgs.iteritems():
        for env, info in {
            env: (penvs.get(env) or penvs.get("*"))
            for env in envs
            if ("*" in penvs or env in penvs) and env in enabled_envs
        }.iteritems():
            logger.debug("=> %s for env \"%s\" in progress..." % (name, env))
            logger.debug('')

            resolver = envs[env]  # Gets correct resolver
            resolved = resolved_envs[env] = resolved_envs.get(env) or {}
            resolved_pkgs[env] = resolved_pkgs.get(env) or {}
            pkgs, resolved_pkgs[env][name] = resolver.resolve(
                spec=info.get("spec") or name,
                versions=info.get("versions") or {}
            )
            for res_name, res_info in pkgs.iteritems():
                # if package already in resoved just merge extra
                if res_name in resolved:
                    resolved["packages"] += [name]
                    for k, v in res_info["extra"].iteritems():
                        resolved[res_name]["extra"][k] = list(
                            set(resolved[res_name]["extra"][k] + v))
                else:
                    res_info["packages"] = [name]
                    resolved.update({res_name: res_info})

    result = pypi2nix_template.render(
        resolved_pkgs=resolved_pkgs, resolved_envs=resolved_envs)

    with open("python-packages-generated.nix", "w") as fh:
        fh.write(result)

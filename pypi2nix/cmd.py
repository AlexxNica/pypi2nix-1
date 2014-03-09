import os
import sys
import logging
import argparse
import json
import requests

from jinja2 import Environment, PackageLoader
from pip.index import Link

from .log import logger
from .package_manager import PackageManager, PersistentCache
from .datastructures import Spec, SpecSet, first
from .resolver import Resolver

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
    def __init__(self, envs, cache_root, download_cache_root, overrides={}, update=False):
        self.overrides = overrides
        self.package_manager = {}

        # Shared betwene instances
        self.download_cache_root = download_cache_root
        self.cache_root = cache_root
        self.link_cache = PersistentCache(
            os.path.join(self.cache_root, "link_cache.pickle"))
        self.extract_cache = {}
        self._dependency_hook_call_cache = {}

        if update:
            self.link_cache.empty_cache()

        # Create cache root and download cache root if it does not exist
        if not os.path.exists(self.cache_root):
            os.makedirs(self.cache_root)
        if not os.path.exists(self.download_cache_root):
            os.makedirs(self.download_cache_root)

        # Use multiple instances of package managers for different environments
        for env, info in envs.iteritems():
            self.package_manager[env] = PackageManager(
                exe=info["exe"], python_path=info["python_path"],

                download_cache_root=self.download_cache_root,
                dep_cache=PersistentCache(
                    os.path.join(self.cache_root, "%s-deps.pickle" % env)),
                pkg_info_cache=PersistentCache(
                    os.path.join(self.cache_root, "%s-pkginfo.pickle" % env)),
                link_cache=self.link_cache,
                extract_cache=self.extract_cache,

                dependency_hook=self._dependency_hook,
                link_hook=self._link_hook
            )

    def _link_hook(self, spec, link):
        package_manager = self.package_manager[self.current_env]

        dep_override = self._get_override(spec) or {}
        if dep_override.get("src") and spec.is_pinned:
            import pdb; pdb.set_trace()
            logger.info(
                '===> Source override %s found for package %s',
                dep_override.get("src"), spec)
            src = env.from_string(
                dep_override.get("src")).render({"spec": spec})
            link = Link(src + "#%s=%s" % package_manager.get_hash(Link(src)))

        # Hack to make pickle work
        link.comes_from = None

        return link

    def _dependency_hook(self, spec, deps):
        _dependency_hook_call_cache = \
            self._dependency_hook_call_cache[self.current_env] = \
            self._dependency_hook_call_cache or {}

        if spec in _dependency_hook_call_cache:
            return _dependency_hook_call_cache[spec]

        new_deps = []

        # Dependency overrides for package
        dep_override = self._get_override(spec) or {}
        if dep_override.get("deps"):
            logger.info(
                '===> Dependency override %s found for package %s',
                dep_override, spec)

            for dep in dep_override.get("deps"):
                if isinstance(dep, basestring):
                    new_deps.append((Spec.from_line(dep), None))
                else:
                    new_deps.append((Spec.from_line(dep[0]), dep[1]))

        # Requirement overrides, where you add requirements from different
        # source files like requirements.txt of versions.cfg in builout
        if dep_override.get("requirements"):
            def render_url(url):
                return env.from_string(url).render({"spec": spec})

            logger.info(
                '===> Requirements override %s found for package %s',
                dep_override.get("requirements"), spec)

            # Handle list of requirements or a single one
            if isinstance(dep_override.get("requirements"), basestring):
                lines = [dep_override.get("requirements")]
            else:
                lines = dep_override.get("requirements")

            for line in lines:
                # By default we handle requirements.txt format
                # You can also specify extra
                if isinstance(line, basestring) or len(line) == 2:
                    url = line if isinstance(line, basestring) else line[0]
                    url = render_url(url)
                    extra = None if isinstance(line, basestring) else line[1]
                    for req in requests.get(url).iter_lines():
                        new_deps.append((Spec.from_line(req), extra))

        # Dependency override for dependencies themselves
        for dep, extra in deps:
            dep_override = self._get_override(dep) or {}
            if dep_override.get("spec"):
                logger.info(
                    '===> Dependency override %s found for dependency %s',
                    dep_override, dep)

                new_deps.append(
                    (Spec.from_line(dep_override["spec"]), extra))
            else:
                new_deps.append((dep, extra))

        _dependency_hook_call_cache[spec] = new_deps
        return new_deps

    def _get_override(self, spec):
        pkg_override = None
        if spec.name in (self.overrides.get(self.current_env, {})).keys():
            pkg_override = self.overrides[self.current_env][spec.name]
        elif spec.name in (self.overrides.get("*", {})).keys():
            pkg_override = self.overrides["*"][spec.name]

        return pkg_override

    def _resolve(self, spec, package_manager, versions=[]):
        logger.info('===> Collecting requirements')

        spec_set = SpecSet()
        spec_set.add_spec(spec)

        for line in versions:
            spec_set.add_spec(Spec.from_line(line))

        logger.info('===> Normalizing requirements')
        with logger.indent():
            spec_set = spec_set.normalize()
            for spec in spec_set:
                logger.info('- %s' % (spec,))

        logger.info('===> Resolving full tree')

        with logger.indent():
            resolver = Resolver(spec_set, package_manager=package_manager)
            pinned_spec_set = resolver.resolve()

        logger.info('===> Pinned spec set resolved')
        with logger.indent():
            for spec in pinned_spec_set:
                logger.info('- %s' % (spec,))

        return pinned_spec_set

    def resolve(self, env, spec, versions=[], extra=("test", "tests", "testing")):
        self.current_env = env

        package_manager = self.package_manager[env]
        package_manager.extra = extra

        target_spec = Spec.from_line(spec)
        pinned = self._resolve(target_spec, package_manager, versions)

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

        return (
            result,
            next((s.fullname for s in pinned if s.name == target_spec.name))
        )


def _decode_list(data):
    rv = []
    for item in data:
        if isinstance(item, unicode):
            item = item.encode('utf-8')
        elif isinstance(item, list):
            item = _decode_list(item)
        elif isinstance(item, dict):
            item = _decode_dict(item)
        rv.append(item)
    return rv


def _decode_dict(data):
    rv = {}
    for key, value in data.iteritems():
        if isinstance(key, unicode):
            key = key.encode('utf-8')
        if isinstance(value, unicode):
            value = value.encode('utf-8')
        elif isinstance(value, list):
            value = _decode_list(value)
        elif isinstance(value, dict):
            value = _decode_dict(value)
        rv[key] = value
    return rv


def main():
    if hasattr(sys, "pypy_version_info"):
        vers = "pypy"
    else:
        vers = "python%s.%s" % (sys.version_info.major, sys.version_info.minor)

    parser = argparse.ArgumentParser(description='pypi2nix, dont write them by hand :)')
    parser.add_argument(
        "--update", action="store_true",
        help='''Ignores cache and updates all packages''',
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help='''Be verbose'''
    )
    parser.add_argument(
        "--envs",
        help='''Comma separated list of environments in format:
                name|path|python_path
                (default: PYTHON_ENVS or current python)''',
        default=(
            os.environ.get("PYTHON_ENVS") or
            vers+"|"+sys.executable+"|"+":".join(sys.path)
        )
    )
    parser.add_argument(
        "--enabledenvs",
        help='''Comma separated names of list of enabled environments
                (default: ENABLED_ENVS or all avalible environments)''',
        default=os.environ.get("ENABLED_ENVS")
    )
    parser.add_argument(
        "--cache-root",
        help='''Root of the cache (default: ~/.pip-tools)''',
        default=os.path.join(os.path.expanduser('~'), '.pip-tools')
    )
    parser.add_argument(
        "--download-cache-root",
        help='''Root of the download cache (default: ~/.pip-tools/cache)''',
        default=os.path.join(os.path.expanduser('~'), '.pip-tools', 'cache')
    )
    parser.add_argument(
        "input", help="Input json file (default stdin)",
        type=argparse.FileType('r'), default=sys.stdin
    )
    parser.add_argument(
        "output", help="Output nix file (default stdout)",
        type=argparse.FileType('w'), default=sys.stdout
    )
    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    # Load input file
    try:
        logger.info("=> Parsing input json")
        input_spec = json.loads(args.input.read(), object_hook=_decode_dict)
    except:
        raise Exception("Cannot parse input package speciffication")

    # Sanity check input json
    assert isinstance(input_spec, dict), \
        "Input speciffication is not a dict"
    assert isinstance(input_spec.get("pkgs") or [], list), \
        "Package speciffication is not a list"
    assert isinstance(input_spec.get("overrides") or {}, dict), \
        "Overrides speciffication is not a dict"

    # Parse environments from provided comma separated string
    envs = {}
    for env in args.envs.split(","):
        name, path, python_path = (env.split("|") + [None, None, ""])[:3]
        if not name or not path:
            logger.warn("Problem parsing environemnt %s", env)
            continue

        logger.info("=> Environment: %s %s %s", name, path, python_path)
        envs[name] = {"exe": path, "python_path": python_path}

    resolver = PyNixResolver(
        envs,
        args.cache_root, args.download_cache_root,
        overrides=input_spec.get("overrides") or {},
        update=args.update
    )

    enabled_envs = envs.keys() \
        if not args.enabledenvs else args.enabledenvs.split(",")
    logger.info("=> Enabled envs: %s", enabled_envs)

    default_envs = ["python2.7"]

    logger.info('')
    logger.info("=> Processing speciffications")
    # For every specified package for each python environment resolve its
    # dependencies, then merge dependencies per env. Put pinned speciffied

    # packages per environment in resolved_pkgs and dependencies of all
    # packages per environment in resolved_envs
    resolved_envs = {}
    resolved_pkgs = {}
    for specline in input_spec.get("pkgs") or []:

        # Handle different shortucts of speciffing packages and write then 
        # common format:
        # name=name envs = {"env_name or *": {"spec": "name", "versions": []}}
        if isinstance(specline, basestring):
            penvs = {e: {"name": specline} for e in default_envs}
        elif (
            isinstance(specline, dict) and
            any((env in specline for env in enabled_envs + ["*"]))
        ):
            penvs = specline
        elif isinstance(specline, dict) and "envs" in specline:
            if isinstance(specline["envs"], list):
                penvs = {
                    e: specattrs.pop("envs") and specattrs
                    for e, specattrs in {
                        x: specline.copy() for x in specline["envs"]
                    }.iteritems()
                }
            elif isinstance(specline["envs"], dict):
                penvs = {
                    n: specattrs.pop("envs") and specattrs.update(e) or specattrs
                    for n, (e, specattrs) in {
                        k: (v, specline.copy()) for k, v in specline["envs"].iteritems()
                    }.iteritems()
                }
            else:
                logger.warn("Incorrect format for specline %s", specline)
                continue
        elif isinstance(specline, dict):
            penvs = {e: specline for e in default_envs}
        else:
            logger.warn("Incorrect format for specline %s", specline)
            continue

        with logger.indent():
            logger.info('')
            logger.info("=> Unified speciffications for specline %s", specline)
            logger.info("%s", penvs)

        # Process package for each environment
        for env, info in {
            env: (penvs.get(env) or penvs.get("*"))
            for env in envs
            if ("*" in penvs or env in penvs) and env in enabled_envs
        }.iteritems():
            # Spec can be speciffied in spec or provided with name
            spec = Spec.from_line(info.get("spec") or info.get("name"))
            # Name can be speciffied in name or provided by spec
            name = info.get("name") or spec.name

            if not name or not spec:
                logger.warn(
                    "No name and/or spec provided by speciffication %s", info)
                continue

            logger.info('')
            logger.info("~> %s for env \"%s\" in progress..." % (name, env))
            logger.info('~> ################################\n')

            resolved = resolved_envs[env] = resolved_envs.get(env) or {}
            resolved_pkgs[env] = resolved_pkgs.get(env) or {}
            pkgs, resolved_pkgs[env][name] = resolver.resolve(
                env, spec=str(spec), versions=info.get("versions") or {}
            )
            for res_name, res_info in pkgs.iteritems():
                # if package already in resoved just merge extra
                if res_name in resolved:
                    resolved[res_name]["packages"] += [name]
                    for k, v in res_info["extra"].iteritems():
                        resolved[res_name]["extra"][k] = list(
                            set(resolved[res_name]["extra"][k] + v))
                else:
                    res_info["packages"] = [name]
                    resolved.update({res_name: res_info})

    logger.info('')
    logger.info("=> Rendering template")
    result = pypi2nix_template.render(
        resolved_pkgs=resolved_pkgs, resolved_envs=resolved_envs)

    logger.info("=> Generating output file")
    args.output.write(result)

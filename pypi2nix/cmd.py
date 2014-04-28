import os
import sys
import logging
import argparse
import json
import collections

from jinja2 import Environment, PackageLoader

from .log import logger
from .package_resolver import PackageResolver
from .package_manager import Package
from .caching import PersistentCache, hashabledict
from .datastructures import Spec, SpecSet, first

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



def _decode_list(data):
    rv = []
    for item in data:
        if isinstance(item, unicode):
            item = item.encode('utf-8')
        elif isinstance(item, list):
            item = tuple(_decode_list(item))
        elif isinstance(item, dict):
            item = hashabledict(_decode_dict(item))
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
            value = tuple(_decode_list(value))
        elif isinstance(value, dict):
            value = hashabledict(_decode_dict(value))
        rv[key] = value
    return rv


def parse_specline(specline, default_envs):
    """
    Handle different shortucts of speciffing packages and write them in
    common format:

        `name=name envs = {"env_name or *": {"spec": "name", "versions": []}}`
    """

    def _parse_scope(specline, parent={}):
        parsed = {}

        spec = specline.get("spec") or parent.get("spec") or \
            parent.get("name") or specline.get("name")
        name = specline.get("name") or parent.get("name") or \
            (spec and Spec.from_line(spec).name)

        overrides = specline.get("overrides", {})
        if "override" in specline:
            overrides.update(
                {Spec.from_line(spec).name: specline.get("override")})

        versions = [specline.get("versions")] \
            if isinstance(specline.get("versions"), basestring) \
            else specline.get("versions", [])

        parsed.update({
            "name": name, "spec": spec,
            "overrides": {
                k: v for k, v in
                (overrides or parent.get("overrides", {})).iteritems()
            },
            "versions": versions or parent.get("versions", [])
        })

        return parsed

    if isinstance(specline, basestring):
        spec = Spec.from_line(specline)
        penvs = {e: {"name": spec.name, "spec": specline} for e in default_envs}
    elif isinstance(specline, dict):
        penvs = {}
        top_level = _parse_scope(specline)

        if isinstance(specline.get("envs"), list):
            envs = {name: {} for name in specline.get("envs")}
        elif isinstance(specline.get("envs"), dict):
            envs = specline.get("envs").copy()
            if "*" in envs:
                envs.update(
                    {name: {} for name in set(default_envs) - set(envs.keys())}
                )
                envs.pop("*")
        else:
            envs = {k: {} for k in default_envs}

        for name, env in envs.iteritems():
            local = _parse_scope(env, parent=top_level)

            if not "spec" in local:
                raise Exception("Spec not provided by specline %s" % specline)
            if not "name" in local:
                raise Exception("Name not provided by specline %s" % specline)

            penvs.update({name: local})
    else:
        raise Exception("Incorrect format for specline %s", specline)

    return penvs


def main():
    if hasattr(sys, "pypy_version_info"):
        vers = "pypy"
    else:
        vers = "python%s.%s" % (sys.version_info.major, sys.version_info.minor)

    parser = argparse.ArgumentParser(
        description='pypi2nix, dont write them by hand :)')
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
            vers + "|" + sys.executable + "|" + ":".join(sys.path)
        )
    )
    parser.add_argument(
        "--enabled-envs",
        help='''Comma separated names of list of enabled environments
                (default: ENABLED_ENVS or all avalible environments)''',
        default=os.environ.get("ENABLED_ENVS")
    )
    parser.add_argument(
        "--extra",
        help='''Comma separated list of additional extra''',
        default=""
    )
    parser.add_argument(
        "--name",
        help='''Comma separated list of additional extra''',
        default=""
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
        "--overrides",
        help='''Package overrides (default: ''',
        default=""
    )
    parser.add_argument("input", help="Input json or setup.py file")
    parser.add_argument(
        "output", help="Output nix file (default stdout)",
        type=argparse.FileType('w'), default=sys.stdout
    )
    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    # Create basic cache dict
    cache = collections.defaultdict(dict)
    cache["link_cache"] = PersistentCache(
        os.path.join(args.cache_root, "link_cache.pickle"))
    cache["pkg_info_cache"] = PersistentCache(
        os.path.join(args.cache_root, "pkginfo.pickle"))

    # Parse environments from provided comma separated string
    envs = {}
    for env in args.envs.split(","):
        name, path, python_path = (env.split("|") + [None, None, ""])[:3]
        if not name or not path:
            logger.warn("Problem parsing environemnt %s", env)
            continue

        logger.info("=> Environment: %s %s %s", name, path, python_path)

        # Create environment cache
        env_cache = cache.copy()
        env_cache["dep_cache"] = PersistentCache(
            os.path.join(args.cache_root, "%s-deps.pickle" % name))
        env_cache["version_cache"] = PersistentCache(
            os.path.join(args.cache_root, "%s-versions.pickle" % name))

        # Create reslvers for each enviroment
        envs[name] = PackageResolver(
            download_cache_root=args.download_cache_root, cache=env_cache,
            exe=path, python_path=python_path
        )

    # Parse enabled environemnts
    enabled_envs = envs.keys() \
        if not args.enabled_envs else args.enabled_envs.split(",")
    logger.info("=> Enabled envs: %s", enabled_envs)

    default_envs = ["python27"]

    # Load overrides
    logger.info('')
    logger.info("=> Loading overrides")

    overrides = {}
    overrides_path = args.overrides or ".pypi2nix.json"
    if os.path.exists(overrides_path):
        logger.info("- Overrides found %s", overrides_path)
        overrides = json.loads(
            open(overrides_path).read(), object_hook=_decode_dict)
        assert isinstance(overrides, dict), "Package overrides are not dict"

    # Process speciffications
    logger.info('')
    logger.info("=> Processing speciffications")

    resolved_pkgs = {}
    resolved_alias = {}

    path = os.path.abspath(args.input)
    if os.path.isdir(path):
        logger.info("- Using package on path %s", path)

        package = Package(
            dist_dir=path,exe=sys.executable, python_path=":".join(sys.path))

        input_specs = []
        for dep in package.get_deps(extra=args.extra.split(",")):
            input_specs += [str(dep)]

        for env in enabled_envs:
            logger.info('')
            logger.info("~> %s for env \"%s\" in progress..." % (input_specs, env))
            logger.info('~> ################################\n')

            local_overrides = {}
            local_overrides.update(overrides.get("*", {}))
            local_overrides.update(overrides.get(env, {}))

            resolved_pkgs[env] = resolved_pkgs.get(env) or {}
            resolved_alias[env] = resolved_alias.get(env) or {}

            resolved_pkgs[env], resolved_alias[env] = \
                envs[env].resolve(
                    specs=input_specs,
                    overrides=local_overrides,
                    dependency_links=package.get_dependency_links()
                )
    else:
        # Load input file
        try:
            logger.info("- Parsing input json %s", path)
            input_spec = json.loads(open(path).read(), object_hook=_decode_dict)
        except:
            raise Exception("Cannot parse input package speciffication")

        # Sanity check input json
        assert isinstance(input_spec, list), \
            "Input speciffication is not a dict"

        # For every specified package for each python environment resolve its
        # dependencies, then merge dependencies per env.
        for specline in input_spec or []:

            penvs = parse_specline(specline, default_envs)
            with logger.indent():
                logger.info('')
                logger.info("=> Unified speciffications for specline %s", specline)
                logger.info("%s", penvs)

            # Process package for each environment
            for env, info in {
                env: penvs.get(env)
                for env in envs if env in enabled_envs and env in penvs
            }.iteritems():
                spec = Spec.from_line(info.get("spec"))
                name = info.get("name")

                logger.info('')
                logger.info("~> %s for env \"%s\" in progress..." % (name, env))
                logger.info('~> ################################\n')

                resolved = resolved_pkgs[env] = resolved_pkgs.get(env) or {}
                resolved_alias[env] = resolved_alias.get(env) or {}

                local_overrides = {}
                local_overrides.update(overrides.get("*", {}))
                local_overrides.update(overrides.get(env, {}))
                local_overrides.update(info.get("overrides"))

                pkgs, alias = envs[env].resolve(
                    specs=[str(spec)],
                    versions=info.get("versions"), overrides=local_overrides
                )
                resolved_alias[env].update(alias)
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
        resolved_alias=resolved_alias, resolved_pkgs=resolved_pkgs)

    logger.info("=> Generating output file")
    args.output.write(result)

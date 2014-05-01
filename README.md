pypi2nix = pypi + magic + nix
==============================

A tool that generates nix python packages, so you don't have to.

[Don't write them by hand :)][0]


Installation
============

pypi2nix was created as standalone tool, so you don't need nix, you just
need the right interpreters installed, so they are used when generating
packages.

To install, simply:

```console
$ python setup.py install
```

If you are using nix:

```console
$ nix-env -i pypi2nix
```

Usage
=====

```
usage: pypi2nix [-h] [--update] [--verbose] [--envs ENVS]
                [--enabled-envs ENABLED_ENVS] [--extra EXTRA] [--name NAME]
                [--cache-root CACHE_ROOT]
                [--download-cache-root DOWNLOAD_CACHE_ROOT]
                [--overrides OVERRIDES]
                input output

pypi2nix, dont write them by hand :)

positional arguments:
  input                 Input json or setup.py file
  output                Output nix file (default stdout)

optional arguments:
  -h, --help            show this help message and exit
  --update              Ignores cache and updates all packages
  --verbose             Be verbose
  --envs ENVS           Comma separated list of environments in format:
                        name|path|python_path (default: PYTHON_ENVS or current
                        python)
  --enabled-envs ENABLED_ENVS
                        Comma separated names of list of enabled environments
                        (default: ENABLED_ENVS or all avalible environments)
  --extra EXTRA         Comma separated list of additional extra
  --cache-root CACHE_ROOT
                        Root of the cache (default: ~/.pip-tools)
  --download-cache-root DOWNLOAD_CACHE_ROOT
                        Root of the download cache (default: ~/.pip-
                        tools/cache)
  --overrides OVERRIDES
                        Package overrides (default:
```

Input format
============

Pypi2nix format speciffication:

```
[
 
  - alias name and package specification -
  "simple-package",
      |--> { name: "simple-package", spec: "simple-package", envs: [ "python2.7" ] }

  - or -

  {
    - alias name and package specification if spec not set -
    "name": "complex-package",

    - packages speciffication (optional, default takes name as spec) -
    "spec": "complex-package==1.0",

    - picks dependencies (optional) -
    "versions": "complex-package-dep-A==1.0.0", <- requirements versions
    - or -
    "versions": "(file|http|https)_://<url>.txt", <- requirements file
    - or -
    "versions": ["(file|http|https)_://<url>.txt", extra] <- requirements file + extra
    - or -
    "versions": "(file|http|https)_://<url>.cfg", <- buildout file
    - or -
    "versions": [
      "complex-package-dep==1.0.0", <- requirements versions
      "(file|http|https)_://<url>.txt", <- requirements file
      ["(file|http|https)_://<url>.txt", "extra"], <- requirements file + extra
      "(file|http|https)_://<url>.cfg", <- buildout file
    ],

    - overrides this package (optional) -
    "override": {
      "src": "https://github.com/complex/package/archive/{{ spec.pinned }}.tar.gz", <- override src

      "deps": [ "package-dep-B==2.0", ... ], <- redefine dependencies
      - or (by default takes deps) -
      "append_deps": [  "package-dep-B==2.0", ... ], <- append dependencies

      "override_deps": { <- override dependencies
        "package-dep-C": "package-dep-B[extra]"
      }
    },

    - overrides this package or dependant packages (optional) -
    "overrides": {
      "complex-package-dep-A": <override> ++ {
        "spec": "<spec>" <- replace this dependency
      }
    },

    - defines interpreters to use and per environment options (optional) -
    "envs": [ "python2.7", "pypy" ],
    - or -
    "envs": {
      "python2.7": "complex-package-A",
      - or -
      "pypy": {
        "spec": "complex-package[pypy]", <- define extra
        "versions": <versions>,
        "override": <override>,
        "overrides": <overrides>
      }
      ...
    },
  }
 
]
```

This input format was designed to support both, compactness and expressability,
because you will need it.

Internal format:

```
{
    "python2.7": [
        {
            "name": "simple-package",
            "spec": "simple-package"
        },
        {
            "name": "complex-package",
            "spec": "complex-package",
            "versions": "complex-package-dep-A==1.0.0"
        }
    ],
    "pypy": [
        {
            "name": "complex-package",[ "Plone-5.0", "zope.interface-4.4", ... ]
            "spec": "complex-package[pypy]",
            "versions": "complex-package-dep-A==1.0.0"
        }
    ]
}
```

Testing
=======

```
$ python setup.py test
```

TODO
====

- Buildout verion parsing support
- Differential package generation (suggestion from @garbas)
- Better test coverage
- Detection and repair of dependency cycles
- Better caching support using something like dogpile.cache
- Parallel package generation support

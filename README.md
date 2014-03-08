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
usage: pypi2nix [-h] [--update] [--envs ENVS] [--enabledenvs ENABLEDENVS]
                [--cache-root CACHE_ROOT]
                [--download-cache-root DOWNLOAD_CACHE_ROOT]
                input output

pypi2nix, dont write them by hand :)

positional arguments:
  input                 Input json file (default stdin)
  output                Output nix file (default stdout)

optional arguments:
  -h, --help            show this help message and exit
  --update              Ignores cache and updates all packages
  --envs ENVS           Comma separated list of environments in format:
                        name|path|python_path (default: PYTHON_ENVS or current
                        python)
  --enabledenvs ENABLEDENVS
                        Comma separated names of list of enabled environments
                        (default: ENABLED_ENVS or all avalible environments)
  --cache-root CACHE_ROOT
                        Root of the cache (default: ~/.pip-tools)
  --download-cache-root DOWNLOAD_CACHE_ROOT
                        Root of the download cache (default: ~/.pip-
                        tools/cache)
```

Input format
============

This input format was designed to support both, compactness and expressability,
because you will need it. There's no simple way, just the hard way.

```
{
  # List of pacakges you want to generate
  "pkgs": [

    # Simple package generated for default environments (python2.7)
    "unittest2",

    # Generate pyramid for python2.7 and python3.3
    {
      "name": "pyramid", # output package name also name of specification
      "envs": ["python2.7", "python3.3"] # generate for python2.7 and python3.3
    },
    
    # Generate sentry for python2.7 and pypy
    {
      "name": "sentry", # output package name
      "versions": ["sentry==6.4.4", "django==1.5.5"], # List of picked versions
      "envs": { # List of environments
        "python2.7": {"spec": "sentry[postgres,mysql]"}, # For python2.7 use extra postgres and mysql
        "pypy": {"spec": "sentry[postgres_pypy]"} # For pypy use extra postgres_pypy
      }
    }
  ],

  # List of global overrides for packages per environments
  "overrides": {

    # For all environments
    "*": {
      #Override sentry
      "sentry": {"deps": ["pysqlite"]}, # Add pysqlite as dependency to sentry

      # Override django-celery
      "django-celery": { # Add pysqlite as dependency to django-celery
        "deps": ["pysqlite"], 
        "requirements": [ # Pick requirements for package from different sources
          "https://raw.github.com/celery/django-celery/v{{ spec.pinned }}/requirements/default.txt", # Just a simple requirements file
          ["https://raw.github.com/celery/django-celery/v{{ spec.pinned }}/requirements/test.txt", "test"]] # Requirements file for test extra
      }
    },

    # For python3.3 environment
    "python3.3": {
      # Change specified dependency from unittest2 to unittest2py3k
      "unittest2": {"spec": "unittest2py3k"}
    },

    # For pypy environemnt
    "pypy": {
      # Change speciffied dependancy from psycopg2 to psycopg2cffi
      "psycopg2": {"spec": "psycopg2cffi"}
    }
  }
}
```


Testing
=======

- You serious, this was a hackup of pip-tools, someday, someday,
  after i rewrite this :)

TODO
====

- Rewrite packagemanager.py
- Write basic tests
- Add support for non pypi sources
- Add support for per package overrides
- Add buildout versions support

{ }:

with import <nixpkgs> {};

let

  pypi = import ./generated.nix {
    inherit pkgs python buildPythonPackage;
    self = pypi;
    overrides = import ./overrides.nix {
      inherit python pkgs;
      self = pypi;
    };
  };

in pkgs.buildEnv {
  name = "pypi2nix-examples";
  paths = [
    pypi."graph-explorer"
    pypi."numpy"
    pypi."scipy"
    pypi."django"
    pypi."django-celery"
    pypi."celery"
    pypi."pyramid"
    pypi."almir"
    pypi."graphite-api"
    pypi."sentry"
    pypi."plone"
    pypi."relstorage"
  ];
}

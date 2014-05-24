{ localsettings ? "./intranet/settings/local.py.example" }:

with import <nixpkgs> {};

let

  python26 = pkgs.buildEnv {
    name = "python26";
    paths = [
      pkgs.python26
      pkgs.python26Packages.setuptools
    ];
  };

  python27 = pkgs.buildEnv {
    name = "python27";
    paths = [
      pkgs.python27
      pkgs.python27Packages.setuptools
    ];
  };

  python33 = pkgs.buildEnv {
    name = "python33";
    paths = [
      pkgs.python33
      pkgs.python33Packages.setuptools
    ];
  };

  pypy = pkgs.buildEnv {
    name = "pypy";
    paths = [
      pkgs.pypy
      pkgs.pypyPackages.setuptools
    ];
  };

  pip14 = buildPythonPackage rec {
    version = "1.4";
    name = "pip-${version}";
    src = fetchurl {
      url = "http://pypi.python.org/packages/source/p/pip/pip-${version}.tar.gz";
      sha256 = "15qvm9jsfnja51hpd9ml3dqxngabcyhrfp3rpgndqpfr0yzkrm0z";
    };
    doCheck = false;
    buildInputs = with python27Packages; [ mock scripttest virtualenv pytest ];
  };

  envToString = env: let python = builtins.head env.paths; in
    env.name + "|" +
    env + "/bin/" + python.executable + "|" +
    env + "/lib/" + python.libPrefix + "/site-packages";

in buildPythonPackage rec {
  name = "pypi2nix";

  src = ./.;

  propagatedBuildInputs = with python27Packages; [
    jinja2
    requests
    setuptools
  ] ++ [ pip14 ];

  doCheck = false;

  postShellHook = ''
    export PYTHON_ENVS='${envToString python26},${envToString python27},${envToString python33},${envToString pypy}'
  '';

  passthru = {
  };
}

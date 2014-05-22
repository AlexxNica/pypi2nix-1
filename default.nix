{ localsettings ? "./intranet/settings/local.py.example" }:

with import <nixpkgs> {};

let

  pip_14 = buildPythonPackage rec {
    version = "1.4";
    name = "pip-${version}";
    src = fetchurl {
      url = "http://pypi.python.org/packages/source/p/pip/pip-${version}.tar.gz";
      sha256 = "15qvm9jsfnja51hpd9ml3dqxngabcyhrfp3rpgndqpfr0yzkrm0z";
    };
    doCheck = false;
    buildInputs = with python27Packages; [ mock scripttest virtualenv pytest ];
  };

in buildPythonPackage rec {
  name = "pypi2nix";

  src = ./.;

  propagatedBuildInputs = with python27Packages; [
    jinja2
    requests
  ] ++ [ pip_14 ];

  postInstall = ''
    echo "$PYTHONPATH:`pwd`" > $out/nix-support/PYTHONPATH
  '';

  doCheck = false;

  passthru = {
  };
}

{pkgs ? import <nixpkgs> {}}:
  with pkgs.lib;
rec {

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

  pypi2nix = pkgs.stdenv.mkDerivation rec {
    name = "pypi2nix";
    phases = ["installPhase"];

    installPhase = ''
      ensureDir $out
      ln -s ${python27} $out/python27
      ln -s ${python33} $out/python33
      ln -s ${pypy} $out/pypy
    '';
  };

}

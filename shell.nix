{ pkgs ? import <nixpkgs> { } }:
pkgs.mkShell {
  nativeBuildInputs = with pkgs.buildPackages; [
    python312
  ];
  shellHook = ''
    python -m venv venv
    source venv/bin/activate
  '';
}
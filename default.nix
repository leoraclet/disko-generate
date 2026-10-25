{ pkgs, ... }:

let
  pythonBin = "${pkgs.python3}/bin/python3";

  # Simple wrapper script
  disko-generate = pkgs.writeScriptBin "disko-generate" ''
    #!/bin/sh

    # Find the Python interpreter
    PYTHON=${pythonBin}

    # Find the script directory
    SCRIPT_DIR=$(dirname "$(readlink -f "$0")")

    # Set PYTHONPATH to include our package
    # The package should be in the same directory as this script
    # or in a src subdirectory
    if [ -d "$SCRIPT_DIR/src" ]; then
        PYTHONPATH="$SCRIPT_DIR/src"
    elif [ -d "$SCRIPT_DIR/../src" ]; then
        PYTHONPATH="$SCRIPT_DIR/../src"
    elif [ -d "$SCRIPT_DIR/../../src" ]; then
        PYTHONPATH="$SCRIPT_DIR/../../src"
    else
        PYTHONPATH="$SCRIPT_DIR"
    fi

    # Add to existing PYTHONPATH
    if [ -n "$PYTHONPATH" ]; then
        PYTHONPATH="$PYTHONPATH:${"PYTHONPATH:-"}"
    fi

    export PYTHONPATH

    # Run the Python script
    exec "$PYTHON" -m disko_generate.main "$@"
  '';

  # Flake-compatible outputs
  app = {
    programs.default = disko-generate;
  };

  # For use in NixOS configurations
  nixosConfig = { ... }: {
    environment.systemPackages = [ disko-generate ];
  };

  # Overlay for easy import
  overlay = final: prev: {
    disko-generate = disko-generate;
  };

in {
  inherit disko-generate app nixosConfig overlay;

  # Default outputs
  defaultPackage = disko-generate;
  packages = {
    default = disko-generate;
    disko-generate = disko-generate;
  };

  apps = {
    default = {
      type = "app";
      program = "${disko-generate}/bin/disko-generate";
    };
  };
}

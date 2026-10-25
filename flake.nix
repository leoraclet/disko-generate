{ description = "Disko Configuration Generator - Analyzes system storage and generates valid NixOS disko configurations";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs, ... }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      forEachSupportedSystem = f: nixpkgs.lib.genAttrs supportedSystems f;
    in {
      packages = forEachSupportedSystem (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in {
          default = self.packages.${system}.disko-generate;
          disko-generate = pkgs.stdenv.mkDerivation {
            name = "disko-generate";
            version = "0.1.0";
            
            # Copy only the files we need
            src = ./.;
            
            buildInputs = [ pkgs.python3 ];
            
            phases = "installPhase";
            
            installPhase = ''
              mkdir -p $out/bin
              # In the build environment, $src points to the source directory
              # and we're already in the build directory which has the source
              cp $src/main.py $out/bin/main.py
              chmod +x $out/bin/main.py
              
              # Create a wrapper script that ensures we use the right python
              # Use relative path to main.py
              cat > $out/bin/disko-generate << 'WRAPPER_EOF'
#!/bin/sh
exec ${pkgs.python3}/bin/python3 "$(dirname "$0")/main.py" "$@"
WRAPPER_EOF
              chmod +x $out/bin/disko-generate
            '';
          };
        }
      );

      apps = forEachSupportedSystem (system:
        let
          pkg = self.packages.${system}.disko-generate;
        in {
          default = {
            type = "app";
            program = "${pkg}/bin/disko-generate";
          };
        }
      );
    };
}

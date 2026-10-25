# Disko Configuration Generator

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A tool for generating NixOS [disko](https://github.com/nix-community/disko) configurations from existing system partitions.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Installation](#installation)
  - [From Source (Python)](#from-source-python)
  - [With Nix (Recommended)](#with-nix-recommended)
- [Usage](#usage)
  - [Basic Usage](#basic-usage)
  - [With Sudo](#with-sudo)
  - [LUKS Key Configuration](#luks-key-configuration)
  - [Skip Prompts](#skip-prompts)
- [Example Output](#example-output)
- [Customization](#customization)
- [Requirements](#requirements)
  - [Runtime Dependencies](#runtime-dependencies)
  - [Optional (for full detection)](#optional-for-full-detection)
- [Project Structure](#project-structure)
- [Development](#development)
  - [Running from Source](#running-from-source)
  - [Building with Nix](#building-with-nix)
- [License](#license)
- [Acknowledgments](#acknowledgments)

## Overview

This tool analyzes your system's storage configuration and generates a valid NixOS disko configuration that mirrors your current setup. This is particularly useful for:

- Migrating existing systems to NixOS with disko
- Documenting your current disk layout
- Creating a baseline configuration to modify
- Understanding complex storage setups (LUKS + LVM + filesystem, etc.)

## Features

✅ **Comprehensive Detection**

- Disk partitions (GPT and MBR/legacy tables)
- Filesystems (ext4, btrfs, xfs, f2fs, vfat, ntfs, swap, etc.)
- Encryption (LUKS with cipher, key size, UUID detection)
- LVM (Physical Volumes, Volume Groups, Logical Volumes)
- MDADM/RAID arrays
- ZFS pools and datasets
- bcachefs filesystems

✅ **Smart Features**

- Automatic sudo elevation when needed
- Interactive LUKS key configuration
- Handles complex nested structures (LUKS → LVM → filesystem)
- Preserves metadata (UUIDs, labels, partition types)
- Pretty-printed or compact output
- JSON output for programmatic use

✅ **Output Formats**

- Nix configuration (default)
- JSON (for integration with other tools)

## Installation

### From Source (Python)

```bash
# Clone or download the repository
cd disko-generate

# Run directly with Python 3.12+
python3 main.py [options]
```

### With Nix (Recommended)

#### Using the Flake

```bash
# Run directly
nix run github:leoraclet/disko-generate -- [options]

# Or from a local clone
cd disko-generate
nix run . -- [options]
```

#### In a Nix Shell

```bash
# Enter a shell with disko-generate available
nix shell github:leoraclet/disko-generate

# Then run
disko-generate [options]
```

#### In configuration.nix

```nix
{ pkgs, ... }:
{
  environment.systemPackages = [
    (import /path/to/disko-generate {}).packages.${pkgs.system}.disko-generate
  ];
}
```

#### As a Flake Input

```nix
# flake.nix
{
  inputs.disko-generate.url = "github:leoraclet/disko-generate";

  outputs = { self, nixpkgs, disko-generate }: {
    packages.default = disko-generate.packages.${system}.disko-generate;

    nixosConfigurations.your-hostname = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        ({ pkgs, ... }: {
          environment.systemPackages = [ disko-generate.packages.${pkgs.system}.disko-generate ];
        })
      ];
    };
  };
}
```

## Usage

### Basic Usage

```bash
# Generate configuration to stdout (Nix format)
disko-generate

# Generate pretty-printed Nix configuration
disko-generate --pretty

# Generate JSON format
disko-generate --json

# Save to file
disko-generate --output disko-config.nix

# Force sudo mode
disko-generate --sudo
```

### With Sudo

When run without root privileges, the script will:

1. Detect that it's running as non-root
2. Ask if you want to elevate to sudo
3. Re-run commands with sudo for complete detection

```bash
# Automatically prompts for sudo
./disko-generate

# Or explicitly request sudo
./disko-generate --sudo
```

### LUKS Key Configuration

When LUKS-encrypted partitions are detected, the script will prompt you to configure how to handle decryption:

```txt
LUKS Encryption Detected
========================
Found 1 encrypted device(s):
  - cryptroot: /dev/nvme0n1p2

How would you like to configure LUKS decryption?
  1. Use a key file (recommended for automated setups)
  2. Use interactive password entry (askPassword)
  3. Use both key file and password
  4. Skip (use placeholder /tmp/secret.key)
Enter choice [1-4]:
```

Options:

- **1. Key file**: Specify a path to a key file for automated decryption
- **2. Interactive**: Use `askPassword = true` for manual password entry at boot
- **3. Both**: Use key file with fallback to password
- **4. Skip**: Use placeholder `/tmp/secret.key` (you'll need to configure this manually)

### Skip Prompts

To skip all interactive prompts (useful for scripting):

```bash
disko-generate --no-luks-prompt
```

## Example Output

For a system with:

- NVMe disk with GPT
- EFI system partition (vfat)
- LUKS-encrypted partition containing LVM
- LVM with root, home, and swap volumes

The script generates:

```nix
{
  disko.devices = {
    disk = {
      nvme0n1 = {
        type = "disk";
        device = "/dev/nvme0n1";
        content = {
          type = "gpt";
          partitions = {
            nvme0n1p1 = {
              size = "1G";
              type = "EF00";  # EFI system partition
              content = {
                type = "filesystem";
                format = "vfat";
                mountpoint = "/boot";
                mountOptions = [ "umask=0077" ];
              };
            };
            nvme0n1p2 = {
              size = "100%";
              content = {
                type = "luks";
                name = "cryptroot";
                settings = {
                  keyFile = "/path/to/your/keyfile";
                  # or: askPassword = true;
                };
                content = {
                  type = "lvm_pv";
                  vg = "lvmroot";
                };
              };
            };
          };
        };
      };
    };
    lvm_vg = {
      lvmroot = {
        type = "lvm_vg";
        lvs = {
          root = {
            size = "100G";
            content = {
              type = "filesystem";
              format = "ext4";
              mountpoint = "/";
            };
          };
          home = {
            size = "200G";
            content = {
              type = "filesystem";
              format = "ext4";
              mountpoint = "/home";
            };
          };
          swap = {
            size = "16G";
            content = {
              type = "swap";
            };
          };
        };
      };
    };
  };
}
```

## Customization

After generating the configuration, you may need to:

1. **Update key file paths**: Replace the generated key file path with your actual key file
2. **Adjust sizes**: Use percentages or different absolute sizes
3. **Add options**: Add filesystem options (compression, etc.)
4. **Verify mountpoints**: Ensure mountpoints match your desired layout

## Requirements

### Runtime Dependencies

- Python 3.12+
- `lsblk` (from util-linux)
- `blkid` (from util-linux)

### Optional (for full detection)

- `cryptsetup` (for LUKS details)
- `pvs`, `vgs`, `lvs` (from lvm2, for LVM details)
- `mdadm` (for RAID details)
- `zpool`, `zfs` (for ZFS details)
- `bcachefs` (for bcachefs details)

## Project Structure

```txt
disko-generate/
├── main.py              # Main script (standalone)
├── pyproject.toml       # Python package metadata
├── .python-version      # Python version requirement
├── README.md            # This file
├── .gitignore           # Git ignore patterns
├── default.nix          # Legacy Nix package definition
├── flake.nix            # Nix flake for modern Nix usage
└── flake.lock           # Flake lock file (auto-generated)
```

## Development

### Running from Source

```bash
# Clone the repository
git clone https://github.com/leoraclet/disko-generate.git
cd disko-generate

# Run with Python directly
python3 main.py --help

# Or use the flake
nix run . -- --help
```

### Building with Nix

```bash
# Build the package
nix build .

# Run the built package
./result/bin/disko-generate --help
```

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- [nix-community/disko](https://github.com/nix-community/disko) - The inspiration and foundation
- All contributors to the NixOS project

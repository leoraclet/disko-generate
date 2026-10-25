#!/usr/bin/env python3
"""
Disko Configuration Generator

Analyzes system storage and generates valid NixOS disko configurations.

Features:
- Disk partitions (GPT, MBR)
- Filesystems (ext4, btrfs, xfs, f2fs, vfat, ntfs, swap)
- Encryption (LUKS)
- LVM (PV, VG, LV)
- MDADM RAID
- ZFS pools
- bcachefs
- Automatic sudo elevation
- Interactive LUKS key configuration

Usage:
    python3 -m disko_generate.main [OPTIONS]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PartitionInfo:
    """Information about a partition."""

    name: str
    device: str
    device_type: str = ""
    size: str | None = None
    start: str | None = None
    part_type: str | None = None
    part_uuid: str | None = None
    part_label: str | None = None
    filesystem: str | None = None
    mountpoint: str | None = None
    fs_uuid: str | None = None
    fs_label: str | None = None
    children: dict[str, "PartitionInfo"] = field(default_factory=dict)


@dataclass
class DiskInfo:
    """Information about a disk."""

    name: str
    device: str
    size: str | None = None
    table_type: str | None = None
    partitions: dict[str, PartitionInfo] = field(default_factory=dict)
    model: str | None = None
    serial: str | None = None
    filesystem: str | None = None
    fs_uuid: str | None = None
    fs_label: str | None = None
    mountpoint: str | None = None


@dataclass
class LUKSInfo:
    """Information about LUKS encryption."""

    device: str
    name: str
    uuid: str | None = None
    cipher: str | None = None
    key_size: int | None = None
    key_file: str | None = None
    use_password: bool = False


@dataclass
class LVMInfo:
    """Information about LVM."""

    pvs: dict[str, str] = field(default_factory=dict)
    vgs: dict[str, dict[str, Any]] = field(default_factory=dict)
    lvs: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class MDADMInfo:
    """Information about MDADM RAID arrays."""

    arrays: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class ZFSInfo:
    """Information about ZFS pools."""

    pools: dict[str, dict[str, Any]] = field(default_factory=dict)
    datasets: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class BcachefsInfo:
    """Information about bcachefs filesystems."""

    filesystems: dict[str, dict[str, Any]] = field(default_factory=dict)


class SystemAnalyzer:
    """Analyzes the current system's storage configuration."""

    def __init__(self, use_sudo: bool = False):
        self.disks: dict[str, DiskInfo] = {}
        self.luks_devices: dict[str, LUKSInfo] = {}
        self.lvm_info: LVMInfo = LVMInfo()
        self.mdadm_info: MDADMInfo = MDADMInfo()
        self.zfs_info: ZFSInfo = ZFSInfo()
        self.bcachefs_info: BcachefsInfo = BcachefsInfo()
        self.mountpoints: dict[str, str] = {}
        self.use_sudo = use_sudo

    def _run_command(self, cmd: list[str]) -> str:
        """Run a command and return its output."""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""

    def _run_sudo_command(self, cmd: list[str]) -> str:
        """Run a command with sudo if use_sudo is True."""
        if not self.use_sudo:
            return self._run_command(cmd)
        try:
            result = subprocess.run(
                ["sudo"] + cmd, capture_output=True, text=True, check=False
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format size in bytes to human-readable format."""
        for unit in ["B", "K", "M", "G", "T", "P"]:
            if size_bytes < 1024:
                return (
                    f"{size_bytes:.2f}{unit}" if unit != "B" else f"{size_bytes}{unit}"
                )
            size_bytes //= 1024
        return f"{size_bytes:.2f}P"

    def _get_disk_model(self, device: str) -> str | None:
        """Get disk model from udevadm."""
        try:
            result = subprocess.run(
                [
                    "udevadm",
                    "info",
                    "--query=property",
                    "--name=" + device.split("/")[-1],
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            for line in result.stdout.split("\n"):
                if line.startswith("ID_MODEL="):
                    return line.split("=", 1)[1].strip('"')
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        return None

    def _get_disk_serial(self, device: str) -> str | None:
        """Get disk serial from udevadm."""
        try:
            result = subprocess.run(
                [
                    "udevadm",
                    "info",
                    "--query=property",
                    "--name=" + device.split("/")[-1],
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            for line in result.stdout.split("\n"):
                if line.startswith("ID_SERIAL="):
                    return line.split("=", 1)[1].strip('"')
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        return None

    def _detect_table_type(self, device: str) -> str:
        """Detect partition table type (gpt, msdos, or default to gpt)."""
        # Try sysfs first
        try:
            device_name = device.split("/")[-1]
            with open(f"/sys/block/{device_name}/uevent", "r") as f:
                content = f.read()
                if "ID_PART_TABLE_TYPE=gpt" in content:
                    return "gpt"
                elif "ID_PART_TABLE_TYPE=dos" in content:
                    return "msdos"
        except (FileNotFoundError, PermissionError, OSError):
            pass

        # Try sgdisk
        try:
            result = subprocess.run(
                ["sgdisk", "-p", device], check=False, capture_output=True, text=True
            )
            if "GPT" in result.stdout or "GUID" in result.stdout:
                return "gpt"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Try fdisk
        try:
            result = subprocess.run(
                ["fdisk", "-l", device], check=False, capture_output=True, text=True
            )
            if "DOS/MBR" in result.stdout or "MBR" in result.stdout:
                return "msdos"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Default to gpt for modern systems
        return "gpt"

    def _get_luks_mapped_name(self, device: str) -> str | None:
        """Get the device-mapper name for a LUKS device."""
        for disk in self.disks.values():
            for part in disk.partitions.values():
                if part.device == device and part.children:
                    for child_name, child_info in part.children.items():
                        if child_info.device_type == "crypt":
                            return child_name
        return None

    def _get_vg_for_pv(self, device: str) -> str | None:
        """Get the VG name for a PV device."""
        for pv_device, vg_name in self.lvm_info.pvs.items():
            if pv_device == device:
                return vg_name

        # Try to extract from device path
        if "/dev/mapper/" in device:
            parts = device.replace("/dev/mapper/", "").split("-", 1)
            if len(parts) >= 1:
                return parts[0]
        elif "/dev/" in device and device != "/dev":
            parts = device.replace("/dev/", "").split("/", 1)
            if len(parts) >= 2:
                return parts[0]

        return None

    def get_lsblk_info(self) -> None:
        """Get disk and partition information using lsblk."""
        output = self._run_sudo_command(
            [
                "lsblk",
                "-J",
                "-b",
                "-o",
                "NAME,SIZE,START,TYPE,MOUNTPOINT,FSTYPE,UUID,LABEL,PARTUUID,PARTLABEL",
            ]
        )

        if not output:
            output = self._run_sudo_command(["lsblk", "-J", "-b"])

        if not output:
            return

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return

        for device_info in data.get("blockdevices", []):
            self._process_lsblk_device(device_info)

    def _process_lsblk_device(
        self,
        device_info: dict[str, Any],
        parent_device: str | None = None,
        parent_disk: str | None = None,
    ) -> None:
        """Process a device from lsblk output."""
        name = device_info.get("name", "")
        device_path = device_info.get("device", "")

        # Construct device path if not provided
        if not device_path and name:
            if parent_device:
                device_path = (
                    f"{parent_device}/{name}"
                    if parent_device != "/dev"
                    else f"/dev/{name}"
                )
            else:
                device_path = f"/dev/{name}"

        # Handle size (may be int or str)
        size_raw = device_info.get("size", None)
        if size_raw is not None:
            try:
                size = self._format_size(int(size_raw))
            except (ValueError, TypeError):
                size = str(size_raw) if size_raw else None
        else:
            size = None
        
        # Get start for partitions
        start_raw = device_info.get("start", None)
        if start_raw is not None:
            try:
                start = str(int(start_raw))
            except (ValueError, TypeError):
                start = str(start_raw) if start_raw else None
        else:
            start = None
        
        # END is not available from lsblk, we can skip it for now
        
        device_type = device_info.get("type", "")

        # Handle mountpoints (can be list or string)
        mountpoint_raw = device_info.get("mountpoint", None)
        if isinstance(mountpoint_raw, list):
            mountpoint = None
            # Prefer root mountpoint, then shortest path
            for mp in mountpoint_raw:
                if mp and not mp.startswith("["):
                    if mp == "/":
                        mountpoint = mp
                        break
                    if mountpoint is None or len(mp) < len(mountpoint):
                        mountpoint = mp
        else:
            mountpoint = mountpoint_raw

        if mountpoint and mountpoint.startswith("["):
            mountpoint = None

        fstype = device_info.get("fstype", None)
        uuid = device_info.get("uuid", None)
        label = device_info.get("label", None)
        partuuid = device_info.get("partuuid", None)
        partlabel = device_info.get("partlabel", None)

        # Process different device types
        if device_type == "disk":
            disk = DiskInfo(
                name=name,
                device=device_path,
                size=size,
                model=self._get_disk_model(device_path),
                serial=self._get_disk_serial(device_path),
            )
            disk.table_type = self._detect_table_type(device_path)
            self.disks[name] = disk

        elif device_type == "part":
            if parent_disk and parent_disk in self.disks:
                part_info = PartitionInfo(
                    name=name,
                    device=device_path,
                    device_type=device_type,
                    size=size,
                    start=start,
                    part_type=partuuid,
                    part_uuid=partuuid,
                    part_label=partlabel,
                    filesystem=fstype,
                    mountpoint=mountpoint,
                    fs_uuid=uuid,
                    fs_label=label,
                )
                self.disks[parent_disk].partitions[name] = part_info

                if mountpoint:
                    self.mountpoints[device_path] = mountpoint
                    if uuid:
                        self.mountpoints[uuid] = mountpoint

        elif device_type == "crypt":
            # Crypt devices are in /dev/mapper/
            if not device_path or device_path == f"/dev/{name}":
                device_path = f"/dev/mapper/{name}"

            luks_info = LUKSInfo(
                device=device_path,
                name=name,
                uuid=uuid,
            )
            self.luks_devices[name] = luks_info

            # Add as child to parent partition
            if parent_disk and parent_disk in self.disks:
                for part_info in self.disks[parent_disk].partitions.values():
                    if parent_device and parent_device == part_info.device:
                        crypt_info = PartitionInfo(
                            name=name,
                            device=device_path,
                            device_type=device_type,
                            size=size,
                            filesystem=fstype,
                            mountpoint=mountpoint,
                            fs_uuid=uuid,
                            fs_label=label,
                        )
                        part_info.children[name] = crypt_info

                        if mountpoint:
                            self.mountpoints[device_path] = mountpoint
                            if uuid:
                                self.mountpoints[uuid] = mountpoint
                        break

        elif device_type == "lvm":
            # Extract VG and LV name
            vg_name = None
            lv_name = name

            if "-" in name:
                parts = name.split("-", 1)
                vg_name = parts[0]
                lv_name = parts[1]
            elif "/" in name:
                parts = name.split("/", 1)
                vg_name = parts[0]
                lv_name = parts[1]
            elif "/dev/mapper/" in device_path:
                parts = device_path.replace("/dev/mapper/", "").split("-", 1)
                if len(parts) >= 2:
                    vg_name = parts[0]
                    lv_name = parts[1]
            elif "/dev/" in device_path and device_path != "/dev":
                parts = device_path.replace("/dev/", "").split("/", 1)
                if len(parts) >= 2:
                    vg_name = parts[0]
                    lv_name = parts[1]

            if vg_name:
                self.lvm_info.lvs[f"{vg_name}/{lv_name}"] = {
                    "size": size,
                    "type": "",
                    "device": device_path,
                    "filesystem": fstype,
                    "mountpoint": mountpoint,
                    "uuid": uuid,
                    "label": label,
                }

                if parent_device:
                    self.lvm_info.pvs[parent_device] = vg_name
                    if vg_name not in self.lvm_info.vgs:
                        self.lvm_info.vgs[vg_name] = {
                            "size": "",
                            "free": "",
                            "extent_size": "",
                        }

        # Process children recursively
        for child in device_info.get("children", []):
            self._process_lsblk_device(
                child,
                parent_device=device_path,
                parent_disk=parent_disk if parent_disk else name,
            )

    def get_blkid_info(self) -> None:
        """Get filesystem information using blkid."""
        output = self._run_sudo_command(["blkid", "-J"])

        if not output:
            return

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return

        for device_info in data.get("blockdevices", []):
            device = device_info.get("devname", "")
            fstype = device_info.get("type", "")
            uuid = device_info.get("uuid", None)
            label = device_info.get("label", None)

            for disk in self.disks.values():
                if disk.device == f"/dev/{device}":
                    disk.filesystem = fstype
                    disk.fs_uuid = uuid
                    disk.fs_label = label
                for part in disk.partitions.values():
                    if part.device == f"/dev/{device}":
                        part.filesystem = fstype
                        part.fs_uuid = uuid
                        part.fs_label = label

    def detect_luks(self) -> None:
        """Detect LUKS encrypted devices."""
        # First, detect from lsblk (crypto_LUKS filesystem)
        for disk in self.disks.values():
            for part in disk.partitions.values():
                if part.filesystem == "crypto_LUKS":
                    luks_name = self._get_luks_mapped_name(part.device)
                    if luks_name:
                        luks_info = LUKSInfo(
                            device=part.device,
                            name=luks_name,
                            uuid=part.fs_uuid,
                        )
                        self.luks_devices[luks_name] = luks_info
        
        # Get details from cryptsetup
        output = self._run_sudo_command(["cryptsetup", "luksDump", "--json"])
        
        if not output:
            return
        
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return
        
        for device_info in data.get("devices", []):
            device = device_info.get("device", "")
            uuid = device_info.get("uuid", None)
            cipher = device_info.get("cipher", None)
            key_size = device_info.get("key_size", None)
            
            mapped_name = self._get_luks_mapped_name(device)
            
            if mapped_name and mapped_name in self.luks_devices:
                luks = self.luks_devices[mapped_name]
                luks.cipher = cipher
                luks.key_size = key_size
                luks.uuid = uuid
                
                # Get additional settings from the LUKS2 JSON structure
                # These are in the keyslots or metadata sections
                if "keyslots" in device_info:
                    for keyslot in device_info["keyslots"]:
                        if "key_size" in keyslot:
                            luks.key_size = keyslot["key_size"]
                        break
                
                # Check for LUKS2 specific settings
                if "metadata" in device_info:
                    metadata = device_info["metadata"]
                    # These settings would need to be detected from the actual device
                    # For now, we'll use defaults that work for most setups

    def detect_lvm(self) -> None:
        """Detect LVM configuration."""
        # Detect from lsblk first
        for disk in self.disks.values():
            for part in disk.partitions.values():
                if part.filesystem == "LVM2_member":
                    vg_name = self._get_vg_for_pv(part.device)
                    if vg_name:
                        self.lvm_info.pvs[part.device] = vg_name

        # Get PV info
        output = self._run_sudo_command(["pvs", "-J"])
        if output:
            try:
                data = json.loads(output)
                for pv in data.get("report", []).get("pv", []):
                    device = pv.get("pv_name", "")
                    vg = pv.get("vg_name", "")
                    if device and vg:
                        self.lvm_info.pvs[device] = vg
            except json.JSONDecodeError:
                pass

        # Get VG info
        output = self._run_sudo_command(["vgs", "-J"])
        if output:
            try:
                data = json.loads(output)
                for vg in data.get("report", []).get("vg", []):
                    name = vg.get("vg_name", "")
                    if name:
                        self.lvm_info.vgs[name] = {
                            "extent_size": vg.get("vg_extent_size", ""),
                            "free": vg.get("vg_free", ""),
                            "size": vg.get("vg_size", ""),
                        }
            except json.JSONDecodeError:
                pass

        # Get LV info
        output = self._run_sudo_command(["lvs", "-J"])
        if output:
            try:
                data = json.loads(output)
                for lv in data.get("report", []).get("lv", []):
                    name = lv.get("lv_name", "")
                    vg = lv.get("vg_name", "")
                    if name and vg:
                        self.lvm_info.lvs[f"{vg}/{name}"] = {
                            "size": lv.get("lv_size", ""),
                            "type": lv.get("lv_layout", ""),
                            "device": lv.get("lv_full_name", ""),
                        }
            except json.JSONDecodeError:
                pass

    def detect_mdadm(self) -> None:
        """Detect MDADM RAID arrays."""
        output = self._run_sudo_command(["mdadm", "--detail", "--scan", "--json"])

        if not output:
            # Fallback to /proc/mdstat
            output = self._run_sudo_command(["cat", "/proc/mdstat"])
            if output:
                for line in output.split("\n"):
                    if line.startswith("md"):
                        parts = line.split()
                        if len(parts) >= 4:
                            name = parts[0].rstrip(":")
                            raid_type = parts[2]
                            devices = parts[3].split(",")
                            self.mdadm_info.arrays[name] = {
                                "level": raid_type,
                                "devices": devices,
                                "status": "active",
                            }
                return

        try:
            data = json.loads(output)
            for array in data.get("arrays", []):
                name = array.get("name", "")
                level = array.get("level", "")
                devices = array.get("devices", [])
                uuid = array.get("uuid", None)

                self.mdadm_info.arrays[name] = {
                    "level": level,
                    "devices": devices,
                    "uuid": uuid,
                    "status": "active",
                }
        except json.JSONDecodeError:
            pass

    def detect_zfs(self) -> None:
        """Detect ZFS pools and datasets."""
        output = self._run_sudo_command(
            ["zpool", "list", "-H", "-o", "name,guid,health,altroot"]
        )
        if output:
            for line in output.split("\n"):
                parts = line.split("\t")
                if len(parts) >= 4 and parts[0]:
                    pool_name = parts[0]
                    self.zfs_info.pools[pool_name] = {
                        "guid": parts[1] if len(parts) > 1 else None,
                        "health": parts[2] if len(parts) > 2 else None,
                        "altroot": parts[3] if len(parts) > 3 else None,
                    }

        output = self._run_sudo_command(
            [
                "zfs",
                "list",
                "-H",
                "-o",
                "name,type,mountpoint,compression,encryption,keylocation",
            ]
        )
        if output:
            for line in output.split("\n"):
                parts = line.split("\t")
                if len(parts) >= 6 and parts[0]:
                    dataset_name = parts[0]
                    self.zfs_info.datasets[dataset_name] = {
                        "type": parts[1] if len(parts) > 1 else None,
                        "mountpoint": parts[2] if len(parts) > 2 else None,
                        "compression": parts[3] if len(parts) > 3 else None,
                        "encryption": parts[4] if len(parts) > 4 else None,
                        "keylocation": parts[5] if len(parts) > 5 else None,
                    }

    def detect_bcachefs(self) -> None:
        """Detect bcachefs filesystems."""
        output = self._run_sudo_command(["bcachefs", "list", "-j"])

        if not output:
            output = self._run_sudo_command(["findmnt", "-J", "-t", "bcachefs"])
            if output:
                try:
                    data = json.loads(output)
                    for fs in data.get("filesystems", []):
                        device = fs.get("source", "")
                        target = fs.get("target", "")
                        if device:
                            self.bcachefs_info.filesystems[device] = {
                                "mountpoint": target,
                            }
                except json.JSONDecodeError:
                    pass
            return

        try:
            data = json.loads(output)
            for fs in data.get("filesystems", []):
                uuid = fs.get("uuid", "")
                label = fs.get("label", "")
                self.bcachefs_info.filesystems[uuid] = {
                    "label": label,
                }
        except json.JSONDecodeError:
            pass

    def detect_mountpoints(self) -> None:
        """Detect mountpoints from /proc/mounts."""
        try:
            with open("/proc/mounts", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        device = parts[0]
                        mountpoint = parts[1]
                        self.mountpoints[device] = mountpoint
        except (FileNotFoundError, PermissionError):
            pass

    def analyze(self) -> None:
        """Analyze the system's storage configuration."""
        print("Analyzing system storage configuration...", file=sys.stderr)

        self.get_lsblk_info()
        self.get_blkid_info()
        self.detect_mountpoints()
        self.detect_luks()
        self.detect_lvm()
        self.detect_mdadm()

        # Check for optional tools
        if self._run_command(["which", "zpool"]):
            self.detect_zfs()
        if self._run_command(["which", "bcachefs"]):
            self.detect_bcachefs()

        print(f"Found {len(self.disks)} disks", file=sys.stderr)
        print(f"Found {len(self.luks_devices)} LUKS devices", file=sys.stderr)
        print(f"Found {len(self.lvm_info.vgs)} LVM volume groups", file=sys.stderr)
        print(f"Found {len(self.mdadm_info.arrays)} MDADM arrays", file=sys.stderr)
        print(f"Found {len(self.zfs_info.pools)} ZFS pools", file=sys.stderr)
        print(
            f"Found {len(self.bcachefs_info.filesystems)} bcachefs filesystems",
            file=sys.stderr,
        )


class DiskoConfigGenerator:
    """Generates disko configuration from system analysis."""

    def __init__(self, analyzer: SystemAnalyzer):
        self.analyzer = analyzer
        self.config: dict[str, Any] = {
            "disko": {
                "devices": {
                    "disk": {},
                }
            }
        }

    def _get_luks_mapped_name(self, device: str) -> str | None:
        """Get the device-mapper name for a LUKS device."""
        for disk in self.analyzer.disks.values():
            for part in disk.partitions.values():
                if part.device == device and part.children:
                    for child_name, child_info in part.children.items():
                        if child_info.device_type == "crypt":
                            return child_name
        return None

    def _get_vg_for_pv(self, device: str) -> str | None:
        """Get the VG name for a PV device."""
        for pv_device, vg_name in self.analyzer.lvm_info.pvs.items():
            if pv_device == device:
                return vg_name

        if "/dev/mapper/" in device:
            parts = device.replace("/dev/mapper/", "").split("-", 1)
            if len(parts) >= 1:
                return parts[0]
        elif "/dev/" in device and device != "/dev":
            parts = device.replace("/dev/", "").split("/", 1)
            if len(parts) >= 2:
                return parts[0]

        return None

    def _get_filesystem_for_device(self, device: str) -> dict[str, Any] | None:
        """Get filesystem info for a device."""
        for disk in self.analyzer.disks.values():
            if disk.device == device:
                return {
                    "fstype": disk.filesystem,
                    "mountpoint": disk.mountpoint,
                    "uuid": disk.fs_uuid,
                    "label": disk.fs_label,
                }
            for part in disk.partitions.values():
                if part.device == device:
                    return {
                        "fstype": part.filesystem,
                        "mountpoint": part.mountpoint,
                        "uuid": part.fs_uuid,
                        "label": part.fs_label,
                    }
                for child_info in part.children.values():
                    if child_info.device == device:
                        return {
                            "fstype": child_info.filesystem,
                            "mountpoint": child_info.mountpoint,
                            "uuid": child_info.fs_uuid,
                            "label": child_info.fs_label,
                        }
        return None

    def _get_mount_options(self, fstype: str) -> list[str]:
        """Get default mount options for filesystem type."""
        options_map = {
            "vfat": ["umask=0077"],
            "ext4": ["defaults"],
            "xfs": ["defaults"],
            "btrfs": ["defaults"],
            "f2fs": ["defaults"],
            "ntfs": ["defaults"],
        }
        return options_map.get(fstype, [])

    def generate(self) -> dict[str, Any]:
        """Generate the disko configuration."""
        self._generate_disk_config()
        self._generate_lvm_config()
        self._generate_mdadm_config()
        self._generate_zfs_config()
        self._generate_bcachefs_config()
        return self.config

    def _generate_disk_config(self) -> None:
        """Generate disk configuration."""
        for disk_name, disk_info in self.analyzer.disks.items():
            disk_config: dict[str, Any] = {
                "type": "disk",
                "device": disk_info.device,
            }

            # Generate content based on partition table type
            if disk_info.table_type == "gpt":
                disk_config["content"] = self._generate_gpt_content(disk_info)
            elif disk_info.table_type == "msdos":
                content = self._generate_table_content(disk_info)
                if content:
                    disk_config["content"] = content
            elif disk_info.table_type:
                content = self._generate_direct_content(disk_info)
                if content:
                    disk_config["content"] = content

            self.config["disko"]["devices"]["disk"][disk_name] = disk_config

    def _generate_gpt_content(self, disk_info: DiskInfo) -> dict[str, Any]:
        """Generate GPT partition table content."""
        content: dict[str, Any] = {
            "type": "gpt",
            "partitions": {},
        }

        for part_name, part_info in disk_info.partitions.items():
            part_config: dict[str, Any] = {}

            if part_info.size:
                part_config["size"] = part_info.size
            
            if part_info.start:
                part_config["start"] = part_info.start

            if part_info.part_type:
                part_config["type"] = part_info.part_type

            if part_info.part_uuid:
                part_config["uuid"] = part_info.part_uuid
            if part_info.part_label:
                part_config["label"] = part_info.part_label

            part_content = self._generate_partition_content(part_info)
            if part_content:
                part_config["content"] = part_content

            content["partitions"][part_name] = part_config

        return content

    def _generate_table_content(self, disk_info: DiskInfo) -> dict[str, Any] | None:
        """Generate legacy MBR table content."""
        if not disk_info.partitions:
            return None

        content: dict[str, Any] = {
            "type": "table",
            "format": "msdos",
            "partitions": [],
        }

        for part_name, part_info in disk_info.partitions.items():
            part_config: dict[str, Any] = {
                "name": part_name,
                "part-type": "primary",
            }

            if part_info.size:
                part_config["size"] = part_info.size

            part_content = self._generate_partition_content(part_info)
            if part_content:
                part_config["content"] = part_content

            content["partitions"].append(part_config)

        return content

    def _generate_direct_content(self, disk_info: DiskInfo) -> dict[str, Any] | None:
        """Generate content for a disk without partition table."""
        if not disk_info.filesystem:
            return None

        return self._generate_filesystem_content(
            disk_info.filesystem or "ext4",
            disk_info.device,
            disk_info.mountpoint,
            disk_info.fs_uuid,
            disk_info.fs_label,
        )

    def _generate_partition_content(
        self, part_info: PartitionInfo
    ) -> dict[str, Any] | None:
        """Generate content for a partition based on its type."""
        # LUKS (crypto_LUKS filesystem)
        if part_info.filesystem == "crypto_LUKS":
            luks_name = self._get_luks_mapped_name(part_info.device)
            if luks_name and luks_name in self.analyzer.luks_devices:
                luks_info = self.analyzer.luks_devices[luks_name]
                return self._generate_luks_content(luks_info, part_info)
            else:
                luks_info = LUKSInfo(
                    device=part_info.device,
                    name=luks_name or "crypt" + part_info.name.replace("/", "_"),
                )
                return self._generate_luks_content(luks_info, part_info)

        # MDADM
        for array_name, array_info in self.analyzer.mdadm_info.arrays.items():
            if part_info.device in array_info.get("devices", []):
                return {"type": "mdraid", "name": array_name}

        # LVM PV
        if part_info.device in self.analyzer.lvm_info.pvs:
            vg_name = self.analyzer.lvm_info.pvs[part_info.device]
            return {"type": "lvm_pv", "vg": vg_name}

        # ZFS
        if part_info.filesystem == "zfs_member":
            for pool_name in self.analyzer.zfs_info.pools:
                return {"type": "zfs", "pool": pool_name}

        # bcachefs
        if part_info.filesystem == "bcachefs":
            return {
                "type": "bcachefs",
                "filesystem": "bcachefs_fs",
                "label": part_info.fs_label,
            }

        # Swap
        if part_info.filesystem == "swap":
            return {"type": "swap"}

        # LVM2_member (direct LVM without LUKS)
        if part_info.filesystem == "LVM2_member":
            vg_name = self._get_vg_for_pv(part_info.device)
            if vg_name:
                return {"type": "lvm_pv", "vg": vg_name}

        # Regular filesystem
        if part_info.filesystem:
            return self._generate_filesystem_content(
                part_info.filesystem,
                part_info.device,
                part_info.mountpoint,
                part_info.fs_uuid,
                part_info.fs_label,
            )

        return None

    def _generate_luks_content(
        self, luks_info: LUKSInfo, part_info: PartitionInfo
    ) -> dict[str, Any]:
        """Generate LUKS content configuration."""
        content: dict[str, Any] = {
            "type": "luks",
            "name": luks_info.name,
        }

        settings: dict[str, Any] = {}

        if luks_info.cipher:
            settings["cipher"] = luks_info.cipher
        if luks_info.key_size:
            settings["keySize"] = luks_info.key_size

        if luks_info.key_file:
            settings["keyFile"] = luks_info.key_file
        elif luks_info.use_password:
            settings["askPassword"] = True
        else:
            settings["keyFile"] = "/tmp/secret.key"

        if settings:
            content["settings"] = settings

        # Check what's inside the LUKS container
        mapped_device = f"/dev/mapper/{luks_info.name}"

        # Check for LVM inside LUKS
        for disk in self.analyzer.disks.values():
            for part in disk.partitions.values():
                if part.device == part_info.device and part.children:
                    for child_info in part.children.values():
                        if (
                            child_info.device_type == "crypt"
                            and child_info.filesystem == "LVM2_member"
                        ):
                            vg_name = self._get_vg_for_pv(child_info.device)
                            if vg_name:
                                content["content"] = {"type": "lvm_pv", "vg": vg_name}
                                return content

        # Check if PV points to mapped device
        for pv_device, vg_name in self.analyzer.lvm_info.pvs.items():
            if pv_device == mapped_device:
                content["content"] = {"type": "lvm_pv", "vg": vg_name}
                return content

        # Check for filesystem
        fs_info = self._get_filesystem_for_device(mapped_device)
        if fs_info and fs_info.get("fstype"):
            content["content"] = self._generate_filesystem_content(
                fs_info["fstype"],
                mapped_device,
                fs_info.get("mountpoint"),
                fs_info.get("uuid"),
                fs_info.get("label"),
            )
            return content

        # Default to ext4 filesystem
        content["content"] = {"type": "filesystem", "format": "ext4"}
        return content

    def _generate_filesystem_content(
        self,
        fstype: str,
        device: str,
        mountpoint: str | None,
        uuid: str | None,
        label: str | None,
    ) -> dict[str, Any]:
        """Generate filesystem content configuration."""
        content: dict[str, Any] = {
            "type": "filesystem",
            "format": fstype,
        }

        if mountpoint:
            content["mountpoint"] = mountpoint

        mount_options = self._get_mount_options(fstype)
        if mount_options:
            content["mountOptions"] = mount_options

        if uuid:
            content["uuid"] = uuid
        if label:
            content["label"] = label

        return content

    def _generate_lvm_config(self) -> None:
        """Generate LVM configuration."""
        if not self.analyzer.lvm_info.vgs:
            return

        lvm_vg_config: dict[str, Any] = {}
        for vg_name in self.analyzer.lvm_info.vgs:
            vg_config: dict[str, Any] = {
                "type": "lvm_vg",
                "lvs": {},
            }

            for lv_name, lv_info in self.analyzer.lvm_info.lvs.items():
                if lv_name.startswith(f"{vg_name}/"):
                    lv_short_name = lv_name.split("/")[-1]
                    lv_config: dict[str, Any] = {"size": lv_info.get("size", "")}

                    if lv_info.get("type"):
                        lv_config["lvm_type"] = lv_info["type"]

                    # Add filesystem content if available
                    if lv_info.get("filesystem"):
                        lv_config["content"] = self._generate_filesystem_content(
                            lv_info["filesystem"],
                            lv_info.get("device", f"/dev/{vg_name}/{lv_short_name}"),
                            lv_info.get("mountpoint"),
                            lv_info.get("uuid"),
                            lv_info.get("label"),
                        )
                    else:
                        lv_device = lv_info.get(
                            "device", f"/dev/{vg_name}/{lv_short_name}"
                        )
                        fs_info = self._get_filesystem_for_device(lv_device)
                        if fs_info and fs_info.get("fstype"):
                            lv_config["content"] = self._generate_filesystem_content(
                                fs_info["fstype"],
                                lv_device,
                                fs_info.get("mountpoint"),
                                fs_info.get("uuid"),
                                fs_info.get("label"),
                            )

                    vg_config["lvs"][lv_short_name] = lv_config

            lvm_vg_config[vg_name] = vg_config

        if lvm_vg_config:
            self.config["disko"]["devices"]["lvm_vg"] = lvm_vg_config

    def _generate_mdadm_config(self) -> None:
        """Generate MDADM configuration."""
        if not self.analyzer.mdadm_info.arrays:
            return

        mdadm_config: dict[str, Any] = {}
        for array_name, array_info in self.analyzer.mdadm_info.arrays.items():
            array_config: dict[str, Any] = {
                "type": "mdadm",
                "level": array_info.get("level", ""),
            }

            if array_info.get("uuid"):
                array_config["uuid"] = array_info["uuid"]
            if array_info.get("metadata"):
                array_config["metadata"] = array_info["metadata"]

            array_device = f"/dev/{array_name}"
            fs_info = self._get_filesystem_for_device(array_device)
            if fs_info and fs_info.get("fstype"):
                array_config["content"] = self._generate_filesystem_content(
                    fs_info["fstype"],
                    array_device,
                    fs_info.get("mountpoint"),
                    fs_info.get("uuid"),
                    fs_info.get("label"),
                )

            mdadm_config[array_name] = array_config

        if mdadm_config:
            self.config["disko"]["devices"]["mdadm"] = mdadm_config

    def _generate_zfs_config(self) -> None:
        """Generate ZFS configuration."""
        if not self.analyzer.zfs_info.pools:
            return

        zpool_config: dict[str, Any] = {}
        for pool_name, pool_info in self.analyzer.zfs_info.pools.items():
            pool_config: dict[str, Any] = {
                "type": "zpool",
                "datasets": {},
            }

            if pool_info.get("health"):
                pool_config["health"] = pool_info["health"]

            for dataset_name, dataset_info in self.analyzer.zfs_info.datasets.items():
                if (
                    dataset_name.startswith(f"{pool_name}/")
                    or dataset_name == pool_name
                ):
                    dataset_short_name = (
                        dataset_name.split("/")[-1]
                        if "/" in dataset_name
                        else dataset_name
                    )
                    dataset_config: dict[str, Any] = {}

                    if dataset_info.get("type") == "volume":
                        dataset_config["type"] = "zfs_volume"
                        if dataset_info.get("size"):
                            dataset_config["size"] = dataset_info["size"]
                    else:
                        dataset_config["type"] = "zfs_fs"

                    if dataset_info.get("mountpoint"):
                        dataset_config["mountpoint"] = dataset_info["mountpoint"]

                    options: dict[str, Any] = {}
                    if dataset_info.get("compression"):
                        options["compression"] = dataset_info["compression"]
                    if dataset_info.get("encryption"):
                        options["encryption"] = dataset_info["encryption"]
                    if dataset_info.get("keylocation"):
                        options["keylocation"] = dataset_info["keylocation"]

                    if options:
                        dataset_config["options"] = options

                    pool_config["datasets"][dataset_short_name] = dataset_config

            zpool_config[pool_name] = pool_config

        if zpool_config:
            self.config["disko"]["devices"]["zpool"] = zpool_config

    def _generate_bcachefs_config(self) -> None:
        """Generate bcachefs configuration."""
        if not self.analyzer.bcachefs_info.filesystems:
            return

        bcachefs_config: dict[str, Any] = {}
        for fs_uuid, fs_info in self.analyzer.bcachefs_info.filesystems.items():
            fs_config: dict[str, Any] = {"type": "bcachefs_filesystem"}

            if fs_info.get("label"):
                fs_config["label"] = fs_info["label"]
            if fs_info.get("mountpoint"):
                fs_config["mountpoint"] = fs_info["mountpoint"]

            bcachefs_config[fs_uuid] = fs_config

        if bcachefs_config:
            self.config["disko"]["devices"]["bcachefs_filesystem"] = bcachefs_config

    def to_nix(self, pretty: bool = False) -> str:
        """Convert configuration to Nix format."""
        return self._dict_to_nix(self.config, pretty, 0)

    def _dict_to_nix(self, data: Any, pretty: bool, indent: int) -> str:
        """Convert Python dict to Nix format."""
        indent_str = "  " * indent

        if isinstance(data, dict):
            if not data:
                return "{ }"

            lines = []
            for key, value in data.items():
                nix_key = self._to_nix_identifier(key)

                if isinstance(value, dict):
                    lines.append(
                        f"{indent_str}{nix_key} = {self._dict_to_nix(value, pretty, indent + 1)};"
                    )
                elif isinstance(value, list):
                    lines.append(
                        f"{indent_str}{nix_key} = {self._list_to_nix(value, pretty, indent + 1)};"
                    )
                elif isinstance(value, str):
                    lines.append(
                        f'{indent_str}{nix_key} = "{self._escape_nix_string(value)}";'
                    )
                elif isinstance(value, bool):
                    lines.append(f"{indent_str}{nix_key} = {str(value).lower()};")
                elif isinstance(value, int):
                    lines.append(f"{indent_str}{nix_key} = {value};")
                elif value is None:
                    lines.append(f"{indent_str}{nix_key} = null;")
                else:
                    lines.append(f'{indent_str}{nix_key} = "{value!s}";')

            if pretty:
                return "{\n" + "\n".join(lines) + "\n" + indent_str + "}"
            else:
                return "{ " + ", ".join(lines) + " }"

        elif isinstance(data, list):
            if not data:
                return "[ ]"

            if pretty:
                items = []
                for item in data:
                    if isinstance(item, str):
                        items.append(
                            f'{"  " * (indent + 1)}"{self._escape_nix_string(item)}"'
                        )
                    else:
                        items.append(
                            f"{'  ' * (indent + 1)}{self._dict_to_nix(item, pretty, indent + 1)}"
                        )
                return "[\n" + ",\n".join(items) + "\n" + "  " * indent + "]"
            else:
                items = []
                for item in data:
                    if isinstance(item, str):
                        items.append(f'"{self._escape_nix_string(item)}"')
                    else:
                        items.append(self._dict_to_nix(item, pretty, indent))
                return "[ " + " ".join(items) + " ]"

        elif isinstance(data, str):
            return f'"{self._escape_nix_string(data)}"'

        elif isinstance(data, bool):
            return str(data).lower()

        elif data is None:
            return "null"

        else:
            return str(data)

    def _list_to_nix(self, data: list[Any], pretty: bool, indent: int) -> str:
        """Convert Python list to Nix format."""
        if not data:
            return "[ ]"

        if pretty:
            items = []
            for item in data:
                if isinstance(item, str):
                    items.append(
                        f'{"  " * (indent + 1)}"{self._escape_nix_string(item)}"'
                    )
                elif isinstance(item, dict):
                    items.append(
                        f"{'  ' * (indent + 1)}{self._dict_to_nix(item, pretty, indent + 1)}"
                    )
                else:
                    items.append(
                        f"{'  ' * (indent + 1)}{self._dict_to_nix(item, pretty, indent + 1)}"
                    )
            return "[\n" + ",\n".join(items) + "\n" + "  " * indent + "]"
        else:
            items = []
            for item in data:
                if isinstance(item, str):
                    items.append(f'"{self._escape_nix_string(item)}"')
                else:
                    items.append(self._dict_to_nix(item, pretty, indent))
            return "[ " + " ".join(items) + " ]"

    @staticmethod
    def _to_nix_identifier(name: str) -> str:
        """Convert a name to a valid Nix identifier."""
        name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        name = re.sub(r"^[0-9]+", "", name)
        if not name or name[0] == "_":
            name = "dev" + name
        return name

    @staticmethod
    def _escape_nix_string(s: str) -> str:
        """Escape a string for Nix format."""
        s = s.replace("\\", "\\\\")
        s = s.replace('"', '\\"')
        s = s.replace("$", "\\$")
        s = s.replace("\n", "\\n")
        s = s.replace("\t", "\\t")
        return s


def get_luks_key_info(luks_devices: dict[str, LUKSInfo]) -> dict[str, LUKSInfo]:
    """Prompt user for LUKS key configuration."""
    if not luks_devices:
        return luks_devices

    print("\n" + "=" * 60, file=sys.stderr)
    print("LUKS Encryption Detected", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"Found {len(luks_devices)} encrypted device(s):", file=sys.stderr)

    for name, info in luks_devices.items():
        print(f"  - {name}: {info.device}", file=sys.stderr)

    print("\nHow would you like to configure LUKS decryption?", file=sys.stderr)
    print("  1. Use a key file (recommended for automated setups)", file=sys.stderr)
    print("  2. Use interactive password entry (askPassword)", file=sys.stderr)
    print("  3. Use both key file and password", file=sys.stderr)
    print("  4. Skip (use placeholder /tmp/secret.key)", file=sys.stderr)

    while True:
        choice = input("Enter choice [1-4]: ").strip()
        if choice in ["1", "2", "3", "4"]:
            break
        print("Please enter 1, 2, 3, or 4.", file=sys.stderr)

    if choice == "4":
        return luks_devices

    if choice == "1":
        key_file = input("Enter path to key file: ").strip()
        if not key_file:
            key_file = "/tmp/secret.key"
        for name, info in luks_devices.items():
            info.key_file = key_file
            info.use_password = False

    elif choice == "2":
        for name, info in luks_devices.items():
            info.key_file = None
            info.use_password = True

    elif choice == "3":
        key_file = input("Enter path to key file: ").strip()
        if not key_file:
            key_file = "/tmp/secret.key"
        for name, info in luks_devices.items():
            info.key_file = key_file
            info.use_password = True

    return luks_devices


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate NixOS disko configuration from current system"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="disko-config.nix",
        help="Output file path (default: disko-config.nix)",
    )
    parser.add_argument(
        "--pretty", "-p", action="store_true", help="Pretty-print the output"
    )
    parser.add_argument(
        "--json", "-j", action="store_true", help="Output in JSON format instead of Nix"
    )
    parser.add_argument(
        "--sudo", "-s", action="store_true", help="Run commands with sudo"
    )
    parser.add_argument(
        "--no-luks-prompt",
        action="store_true",
        help="Don't prompt for LUKS key configuration",
    )

    args = parser.parse_args()

    # Determine if we should use sudo
    use_sudo = args.sudo

    # Warn if not root and sudo not requested
    if os.geteuid() != 0 and not use_sudo and not args.no_luks_prompt:
        print(
            "Warning: Running as non-root. Some information may not be accessible.",
            file=sys.stderr,
        )
        print(
            "For complete detection, this script needs root privileges.",
            file=sys.stderr,
        )
        print(
            "Use --sudo to run with elevated privileges, or --no-luks-prompt to skip prompts.",
            file=sys.stderr,
        )

    # Create analyzer and run analysis
    analyzer = SystemAnalyzer(use_sudo=use_sudo)
    analyzer.analyze()

    # Prompt for LUKS configuration if encryption detected
    if not args.no_luks_prompt and analyzer.luks_devices:
        analyzer.luks_devices = get_luks_key_info(analyzer.luks_devices)

    # Generate configuration
    generator = DiskoConfigGenerator(analyzer)
    config = generator.generate()

    # Output
    if args.json:
        output = json.dumps(config, indent=2 if args.pretty else None)
    else:
        output = generator.to_nix(pretty=args.pretty)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Configuration written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

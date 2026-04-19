"""
Cloned v1.0.0 — Sector-by-Sector Drive Cloning & Imaging Tool
Clone entire drives, save drives to validated compressed image files,
and restore images back to physical drives.
Supports HDD, SATA SSD, NVMe, internal drives, and USB-connected drives.

Copyright (c) 2026 HawaiizFynest — MIT License
"""

import sys
import os
import ctypes
import ctypes.wintypes as wintypes
import time
import hashlib
import json
import struct
import zlib
import threading
import subprocess
import shutil
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Tuple
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QGroupBox, QMessageBox,
    QCheckBox, QFrame, QSplitter, QTextEdit, QStatusBar,
    QDialog, QGridLayout, QScrollArea, QFileDialog, QLineEdit,
    QButtonGroup, QRadioButton, QStackedWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPalette

# ═══════════════════════════════════════════════════════════════════════════════
# WINDOWS API
# ═══════════════════════════════════════════════════════════════════════════════

GENERIC_READ          = 0x80000000
GENERIC_WRITE         = 0x40000000
FILE_SHARE_READ       = 0x00000001
FILE_SHARE_WRITE      = 0x00000002
OPEN_EXISTING         = 3
FILE_FLAG_NO_BUFFERING   = 0x20000000
FILE_FLAG_WRITE_THROUGH  = 0x80000000
INVALID_HANDLE_VALUE  = ctypes.c_void_p(-1).value

FSCTL_LOCK_VOLUME     = 0x00090018
FSCTL_UNLOCK_VOLUME   = 0x0009001C
FSCTL_DISMOUNT_VOLUME = 0x00090020
IOCTL_DISK_GET_LENGTH = 0x0007405C

# Sleep prevention
ES_CONTINUOUS         = 0x80000000
ES_SYSTEM_REQUIRED    = 0x00000001

kernel32       = ctypes.windll.kernel32
_CreateFileW   = kernel32.CreateFileW
_CreateFileW.restype = wintypes.HANDLE
_ReadFile      = kernel32.ReadFile
_WriteFile     = kernel32.WriteFile
_CloseHandle   = kernel32.CloseHandle
_DeviceIoCtl   = kernel32.DeviceIoControl
_SetFilePtr64  = kernel32.SetFilePointerEx
_GetLastError  = kernel32.GetLastError
_FlushBuffers  = kernel32.FlushFileBuffers

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

APP_NAME    = "Cloned"
APP_VERSION = "1.0.0"
CHUNK_SIZE  = 4 * 1024 * 1024   # 4 MB — balanced for speed and memory
IMG_MAGIC   = b"CLONED01"
IMG_FMT_VER = "1.0"
ZLIB_LEVEL  = 6                  # 1-9, 6 = balanced speed/ratio

# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PartInfo:
    index: int
    letter: str
    label: str
    fs: str
    size: int
    free: int
    ptype: str
    boot: bool
    primary: bool

    def to_dict(self) -> dict:
        return {"index": self.index, "letter": self.letter, "label": self.label,
                "fs": self.fs, "size": self.size, "free": self.free,
                "ptype": self.ptype, "boot": self.boot, "primary": self.primary}

    @staticmethod
    def from_dict(d: dict) -> "PartInfo":
        return PartInfo(d.get("index",0), d.get("letter",""), d.get("label",""),
                        d.get("fs",""), d.get("size",0), d.get("free",0),
                        d.get("ptype",""), d.get("boot",False), d.get("primary",False))


@dataclass
class DriveInfo:
    index: int
    model: str
    serial: str
    size: int
    media: str          # Fixed, External/USB, Removable
    interface: str
    bus: str             # NVMe, SATA/SCSI, USB, IDE/SATA
    sector: int          # physical sector size
    parts: list = field(default_factory=list)
    is_system: bool = False
    is_boot: bool = False

    @property
    def size_str(self) -> str:  return fmt_bytes(self.size)
    @property
    def path(self) -> str:      return f"\\\\.\\PhysicalDrive{self.index}"


@dataclass
class ImageMeta:
    version: str = ""
    created: str = ""
    src_model: str = ""
    src_serial: str = ""
    src_size: int = 0
    sector: int = 512
    chunk: int = CHUNK_SIZE
    compression: str = "zlib"
    parts: list = field(default_factory=list)
    sha256: str = ""

    @property
    def size_str(self) -> str:  return fmt_bytes(self.src_size)

# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def fmt_bytes(n: int) -> str:
    if n <= 0: return "0 B"
    for u in ["B","KB","MB","GB","TB","PB"]:
        if abs(n) < 1024 or u == "PB":
            return f"{n:.2f} {u}" if abs(n) < 10 and u not in ("B","KB") else \
                   f"{n:.1f} {u}" if u in ("GB","TB","PB") else f"{int(n)} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def fmt_speed(bps: float) -> str:
    return f"{fmt_bytes(int(bps))}/s" if bps > 0 else "--"

def fmt_eta(s: float) -> str:
    if s <= 0 or s > 604800: return "--:--:--"
    return f"{int(s//3600):02d}:{int(s%3600//60):02d}:{int(s%60):02d}"

def is_admin() -> bool:
    try: return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except: return False

def elevate_uac():
    """Re-launch with UAC elevation prompt."""
    if getattr(sys, 'frozen', False):
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "", None, 1)
    else:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{os.path.abspath(sys.argv[0])}"', None, 1)
    sys.exit(0)

def sys_drive() -> str:
    return os.environ.get("SystemDrive", "C:")

def prevent_sleep():
    """Prevent Windows from sleeping while an operation is running.
    USB drives disconnect during sleep, which kills the operation."""
    kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)

def allow_sleep():
    """Re-allow Windows to sleep after operation completes."""
    kernel32.SetThreadExecutionState(ES_CONTINUOUS)

def run_ps(cmd: str, timeout: int = 30) -> Tuple[bool, str]:
    """Run a PowerShell command and return (success, stdout)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0, r.stdout.strip()
    except Exception as e:
        return False, str(e)

def run_diskpart(script: str):
    """Run a diskpart script silently."""
    try:
        subprocess.run(["diskpart"], input=script, capture_output=True,
                       text=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════════
# RAW DRIVE I/O
# ═══════════════════════════════════════════════════════════════════════════════

def open_read(path: str):
    h = _CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                     None, OPEN_EXISTING, FILE_FLAG_NO_BUFFERING, None)
    if h == INVALID_HANDLE_VALUE:
        raise OSError(f"Cannot open {path} for reading (error {_GetLastError()}). "
                      "Ensure you are running as Administrator.")
    return h

def open_write(path: str):
    h = _CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                     FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING,
                     FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH, None)
    if h == INVALID_HANDLE_VALUE:
        raise OSError(f"Cannot open {path} for writing (error {_GetLastError()}). "
                      "Is the drive locked by another process or BitLocker-encrypted?")
    return h

def seek(h, pos: int):
    np = ctypes.c_longlong(0)
    if not _SetFilePtr64(h, ctypes.c_longlong(pos), ctypes.byref(np), 0):
        raise OSError(f"Seek to {pos} failed (error {_GetLastError()})")
    return np.value

def drive_size(h) -> int:
    class LEN(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_longlong)]
    li = LEN()
    br = wintypes.DWORD(0)
    if _DeviceIoCtl(h, IOCTL_DISK_GET_LENGTH, None, 0,
                    ctypes.byref(li), ctypes.sizeof(li), ctypes.byref(br), None):
        return li.Length
    return 0

def lock_vol(letter: str):
    """Lock and dismount a volume by drive letter. Returns handle or None."""
    h = _CreateFileW(f"\\\\.\\{letter}", GENERIC_READ | GENERIC_WRITE,
                     FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE: return None
    br = wintypes.DWORD(0)
    if not _DeviceIoCtl(h, FSCTL_LOCK_VOLUME, None, 0, None, 0, ctypes.byref(br), None):
        _CloseHandle(h); return None
    _DeviceIoCtl(h, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0, ctypes.byref(br), None)
    return h

def lock_vol_path(vol_path: str):
    """Lock and dismount a volume by its GUID path (\\\\?\\Volume{...}). Returns handle or None."""
    # Volume GUID paths end with backslash — strip it for CreateFileW
    p = vol_path.rstrip("\\")
    h = _CreateFileW(p, GENERIC_READ | GENERIC_WRITE,
                     FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE: return None
    br = wintypes.DWORD(0)
    if not _DeviceIoCtl(h, FSCTL_LOCK_VOLUME, None, 0, None, 0, ctypes.byref(br), None):
        _CloseHandle(h); return None
    _DeviceIoCtl(h, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0, ctypes.byref(br), None)
    return h

def enum_disk_volumes(disk_idx: int) -> list:
    """Get ALL volume GUID paths on a physical disk, including hidden partitions
    (EFI, Recovery, MSR) that don't have drive letters."""
    ok, raw = run_ps(
        f'Get-Partition -DiskNumber {disk_idx} -ErrorAction SilentlyContinue | '
        f'ForEach-Object {{ $_.AccessPaths }} | '
        f'Where-Object {{ $_ -like "\\\\?\\*" }} | '
        f'ConvertTo-Json -Compress')
    if not ok or not raw: return []
    try:
        paths = json.loads(raw)
        if isinstance(paths, str): paths = [paths]
        return [p for p in paths if p]
    except (json.JSONDecodeError, TypeError):
        return []

def lock_all_volumes(disk_idx: int, log_fn=None) -> list:
    """Lock and dismount ALL volumes on a disk — lettered AND hidden.
    Returns list of handles to unlock later."""
    handles = []

    # Get all volume GUID paths on this disk
    vol_paths = enum_disk_volumes(disk_idx)
    if log_fn:
        log_fn(f"  Found {len(vol_paths)} volume(s) on disk {disk_idx}")

    for vp in vol_paths:
        vh = lock_vol_path(vp)
        if vh:
            handles.append(vh)
            if log_fn:
                # Show a short version of the GUID path
                short = vp.split("{")[1][:8] + "..." if "{" in vp else vp[-20:]
                log_fn(f"  Locked volume {short}")
        else:
            if log_fn:
                log_fn(f"  Warning: could not lock {vp[-30:]}")

    return handles

def clear_readonly(disk_idx: int, log_fn=None):
    """Clear read-only attribute on a disk via diskpart. USB-connected drives
    sometimes get flagged read-only by Windows."""
    script = f"select disk {disk_idx}\nattributes disk clear readonly\n"
    try:
        r = subprocess.run(["diskpart"], input=script, capture_output=True,
                           text=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
        if log_fn:
            if "successfully" in r.stdout.lower() or r.returncode == 0:
                log_fn("  Cleared read-only attribute")
            else:
                log_fn(f"  Read-only clear: {r.stdout.strip()[-80:]}")
    except Exception as e:
        if log_fn: log_fn(f"  Read-only clear failed: {e}")

def clean_disk(disk_idx: int, log_fn=None):
    """Wipe the partition table via diskpart clean. This forces Windows to release
    all volume references (including hidden EFI/Recovery/MSR volumes) and guarantees
    write access to the physical drive."""
    script = f"select disk {disk_idx}\nclean\n"
    try:
        r = subprocess.run(["diskpart"], input=script, capture_output=True,
                           text=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
        if log_fn:
            if "succeeded" in r.stdout.lower() or "clean" in r.stdout.lower():
                log_fn("  Disk cleaned — partition table wiped")
            else:
                log_fn(f"  Disk clean result: {r.stdout.strip()[-80:]}")
    except Exception as e:
        if log_fn: log_fn(f"  Disk clean failed: {e}")

def unlock_vol(h):
    if h:
        br = wintypes.DWORD(0)
        _DeviceIoCtl(h, FSCTL_UNLOCK_VOLUME, None, 0, None, 0, ctypes.byref(br), None)
        _CloseHandle(h)

def close(h):
    if h and h != INVALID_HANDLE_VALUE:
        _CloseHandle(h)

# ═══════════════════════════════════════════════════════════════════════════════
# DRIVE ENUMERATION
# ═══════════════════════════════════════════════════════════════════════════════

def enum_drives() -> list:
    """Enumerate all physical drives via PowerShell/WMI."""
    drives = []
    ok, raw = run_ps(
        'Get-CimInstance Win32_DiskDrive | Select-Object Index,Model,SerialNumber,'
        'Size,MediaType,InterfaceType,BytesPerSector,Status,PNPDeviceID | ConvertTo-Json -Depth 3')
    if not ok or not raw: return drives

    try:
        disks = json.loads(raw)
        if isinstance(disks, dict): disks = [disks]
    except json.JSONDecodeError:
        return drives

    # Partition mapping
    ok2, raw2 = run_ps('''
        $r = @()
        Get-CimInstance Win32_DiskDrive | ForEach-Object {
            $d = $_
            Get-CimAssociatedInstance -InputObject $d -ResultClassName Win32_DiskPartition | ForEach-Object {
                $p = $_
                Get-CimAssociatedInstance -InputObject $p -ResultClassName Win32_LogicalDisk | ForEach-Object {
                    $r += [PSCustomObject]@{
                        DI=$d.Index; PI=$p.Index; PT=$p.Type; PB=$p.BootPartition; PP=$p.PrimaryPartition
                        DL=$_.DeviceID; VN=$_.VolumeName; FS=$_.FileSystem; FR=$_.FreeSpace; LS=$_.Size
                    }
                }
            }
        }
        $r | ConvertTo-Json -Depth 3''')

    pmap = {}
    if ok2 and raw2:
        try:
            plist = json.loads(raw2)
            if isinstance(plist, dict): plist = [plist]
            for p in plist:
                di = p.get("DI", -1)
                pmap.setdefault(di, []).append(PartInfo(
                    index=p.get("PI",0), letter=p.get("DL",""), label=p.get("VN","") or "",
                    fs=p.get("FS","") or "", size=int(p.get("LS",0) or 0),
                    free=int(p.get("FR",0) or 0), ptype=p.get("PT","") or "",
                    boot=bool(p.get("PB",False)), primary=bool(p.get("PP",False))))
        except json.JSONDecodeError:
            pass

    sd = sys_drive()

    for d in disks:
        idx = d.get("Index", 0)
        pnp = (d.get("PNPDeviceID","") or "").upper()
        iface = d.get("InterfaceType","Unknown") or "Unknown"

        if "USBSTOR" in pnp or "USB" in pnp:     bus = "USB"
        elif "NVME" in pnp or "NVM" in iface.upper(): bus = "NVMe"
        elif "SCSI" in iface.upper():              bus = "SATA/SCSI"
        elif "IDE" in iface.upper():               bus = "IDE/SATA"
        else:                                       bus = iface

        mt = d.get("MediaType","") or ""
        media = "External/USB" if ("External" in mt or bus == "USB") else \
                "Removable" if "Removable" in mt else \
                "Fixed" if "Fixed" in mt else mt or "Unknown"

        parts = pmap.get(idx, [])
        drives.append(DriveInfo(
            index=idx, model=(d.get("Model","") or "Unknown").strip(),
            serial=(d.get("SerialNumber","") or "").strip(),
            size=int(d.get("Size",0) or 0), media=media, interface=iface,
            bus=bus, sector=int(d.get("BytesPerSector",512) or 512),
            parts=parts,
            is_system=any(p.letter == sd for p in parts),
            is_boot=any(p.boot for p in parts)))

    return sorted(drives, key=lambda x: x.index)

# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE FILE I/O
#
# Format:
#   CLONED01              (8 bytes magic)
#   header_len            (uint64 LE)
#   header_compressed     (header_len bytes — zlib-compressed JSON)
#   chunks:
#     comp_size           (uint32 LE)
#     orig_size           (uint32 LE)
#     chunk_sha256        (32 bytes raw)
#     compressed_data     (comp_size bytes)
#   EOF marker            (comp_size=0, orig_size=0 — 8 bytes)
#   full_sha256_hex       (64 bytes ASCII)
# ═══════════════════════════════════════════════════════════════════════════════

def read_image_meta(path: str) -> Optional[ImageMeta]:
    """Read metadata from a .cloned image file. Returns None if invalid."""
    try:
        with open(path, "rb") as f:
            if f.read(8) != IMG_MAGIC: return None
            hl = struct.unpack("<Q", f.read(8))[0]
            hdr = json.loads(zlib.decompress(f.read(hl)))

            # Read footer SHA-256 (last 64 bytes of file)
            f.seek(-64, 2)
            footer_hash = f.read(64).decode("ascii", errors="ignore")

            m = ImageMeta(
                version=hdr.get("version",""), created=hdr.get("created",""),
                src_model=hdr.get("src_model",""), src_serial=hdr.get("src_serial",""),
                src_size=hdr.get("src_size",0), sector=hdr.get("sector",512),
                chunk=hdr.get("chunk",CHUNK_SIZE), compression=hdr.get("compression","zlib"),
                sha256=footer_hash)
            for p in hdr.get("parts", []):
                m.parts.append(PartInfo.from_dict(p))
            return m
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════════════════════
# PARTITION EXPANSION
# ═══════════════════════════════════════════════════════════════════════════════

def expand_last_partition(disk_idx: int) -> Tuple[bool, str]:
    """Expand the last data partition on a disk to fill all unallocated space."""
    ps = f'''
        $ErrorActionPreference = 'Stop'
        try {{
            $parts = Get-Partition -DiskNumber {disk_idx} |
                Where-Object {{ $_.DriveLetter -or $_.Type -eq 'Basic' }} |
                Sort-Object -Property Offset
            if (-not $parts) {{ Write-Output "NO_PARTITIONS"; exit }}
            $last = $parts | Select-Object -Last 1
            $sup = Get-PartitionSupportedSize -DiskNumber {disk_idx} -PartitionNumber $last.PartitionNumber
            $gain = $sup.SizeMax - $last.Size
            if ($gain -lt 1048576) {{ Write-Output "ALREADY_MAX|0"; exit }}
            Resize-Partition -DiskNumber {disk_idx} -PartitionNumber $last.PartitionNumber -Size $sup.SizeMax
            Write-Output "EXPANDED|$gain"
        }} catch {{
            Write-Output "ERROR|$($_.Exception.Message)"
        }}
    '''
    ok, out = run_ps(ps, timeout=60)
    if not ok: return False, f"PowerShell error: {out}"
    if out.startswith("EXPANDED"):
        gain = int(out.split("|")[1])
        return True, f"Expanded by {fmt_bytes(gain)}"
    elif out.startswith("ALREADY_MAX"):
        return True, "Partition already at maximum size"
    elif out.startswith("NO_PARTITIONS"):
        return False, "No expandable partitions found"
    elif out.startswith("ERROR"):
        return False, out.split("|", 1)[1]
    return False, out

# ═══════════════════════════════════════════════════════════════════════════════
# SIZE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_d2d(src: DriveInfo, dst: DriveInfo) -> Tuple[str, str]:
    """Analyze Drive-to-Drive size compatibility. Returns (level, message).
    level: 'ok', 'warn', 'error'"""
    if dst.size >= src.size:
        extra = dst.size - src.size
        if extra == 0:
            return "ok", f"Drives are the same size ({src.size_str}). Perfect match."
        return "ok", (f"Destination is {fmt_bytes(extra)} larger than source. "
                      f"You'll be able to expand the partition after cloning.")
    short = src.size - dst.size
    pct = (short / src.size * 100) if src.size > 0 else 100
    # Manufacturer variance: "2 TB" drives vary by a few MB between brands/batches.
    # Under 0.1% or under 100 MB is normal — partitions never use the last few MB.
    if pct < 0.1 or short < 100 * 1024 * 1024:
        return "ok", (f"Drives are effectively the same size ({src.size_str}). "
                      f"Destination is {fmt_bytes(short)} smaller — this is normal "
                      f"manufacturer variance and will not affect the clone.")
    # Under 1 GB on a large drive is still very likely fine
    if short < 1024 * 1024 * 1024:
        return "ok", (f"Destination is {fmt_bytes(short)} smaller than source ({pct:.2f}%). "
                      f"This small difference is almost certainly unused space at the "
                      f"end of the disk and will not affect bootability or data.")
    return "warn", (f"Destination is {fmt_bytes(short)} SMALLER than source ({pct:.1f}%). "
                    f"Data beyond {dst.size_str} will be truncated. This may cause data loss "
                    f"if partitions extend past the destination boundary.")

def analyze_d2i(src: DriveInfo, dest_path: str) -> Tuple[str, str]:
    """Analyze Drive-to-Image free space. Returns (level, message)."""
    try:
        usage = shutil.disk_usage(str(Path(dest_path).parent))
        free = usage.free
        # Conservative estimate: compressed image ≈ 30-90% of source depending on content
        if free < src.size * 0.3:
            return "error", (f"Only {fmt_bytes(free)} free on destination. "
                           f"Source drive is {src.size_str}. Even with compression, "
                           f"this will very likely not fit.")
        elif free < src.size * 0.7:
            return "warn", (f"{fmt_bytes(free)} free on destination. Source is {src.size_str}. "
                          f"Image will be compressed but size depends on drive content. "
                          f"Mostly-empty drives compress well; full drives may not fit.")
        else:
            return "ok", (f"{fmt_bytes(free)} free on destination. Source is {src.size_str}. "
                        f"Plenty of space even with minimal compression.")
    except Exception as e:
        return "warn", f"Could not check free space: {e}"

def analyze_i2d(meta: ImageMeta, dst: DriveInfo) -> Tuple[str, str]:
    """Analyze Image-to-Drive size compatibility."""
    if dst.size >= meta.src_size:
        extra = dst.size - meta.src_size
        if extra == 0:
            return "ok", f"Drive matches image source size ({meta.size_str}). Perfect match."
        return "ok", (f"Destination is {fmt_bytes(extra)} larger than image. "
                      f"You'll be able to expand the partition after restoring.")
    short = meta.src_size - dst.size
    pct = (short / meta.src_size * 100) if meta.src_size > 0 else 100
    # Manufacturer variance: same-capacity drives differ by a few MB.
    # Under 0.1% or under 100 MB is normal and won't affect anything.
    if pct < 0.1 or short < 100 * 1024 * 1024:
        return "ok", (f"Drives are effectively the same size ({meta.size_str}). "
                      f"Destination is {fmt_bytes(short)} smaller — this is normal "
                      f"manufacturer variance and will not affect the restore.")
    if short < 1024 * 1024 * 1024:
        return "ok", (f"Destination is {fmt_bytes(short)} smaller than image source ({pct:.2f}%). "
                      f"This small difference is almost certainly unused space at the "
                      f"end of the disk and will not affect bootability or data.")
    return "warn", (f"Destination is {fmt_bytes(short)} SMALLER than image source ({pct:.1f}%). "
                    f"Image was created from a {meta.size_str} drive, destination is {dst.size_str}. "
                    f"Data beyond the destination size will be truncated. "
                    f"This may affect the last partition or prevent booting.")

# ═══════════════════════════════════════════════════════════════════════════════
# PROGRESS TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

class Tracker:
    def __init__(self):
        self.t0 = time.time()
        self._lt = self.t0
        self._lb = 0

    def tick(self, done: int, total: int, w):
        pct = min(done / total * 100, 100.0) if total > 0 else 0
        w.progress.emit(pct)
        w.bytes_update.emit(done, total)
        now = time.time()
        if now - self._lt >= 0.4:
            spd = (done - self._lb) / (now - self._lt) if now > self._lt else 0
            w.speed_update.emit(spd)
            if spd > 0: w.eta_update.emit((total - done) / spd)
            self._lt, self._lb = now, done

# ═══════════════════════════════════════════════════════════════════════════════
# WORKER: DRIVE → DRIVE
# ═══════════════════════════════════════════════════════════════════════════════

class CloneWorker(QThread):
    progress       = pyqtSignal(float)
    speed_update   = pyqtSignal(float)
    eta_update     = pyqtSignal(float)
    bytes_update   = pyqtSignal(int, int)
    status         = pyqtSignal(str)
    phase          = pyqtSignal(str)
    finished_sig   = pyqtSignal(bool, str)
    log            = pyqtSignal(str)
    offer_expand   = pyqtSignal(int, int)  # dest_idx, extra_bytes

    def __init__(self, src: DriveInfo, dst: DriveInfo, verify: bool):
        super().__init__()
        self.src, self.dst, self.verify = src, dst, verify
        self._cancel = False
        self._pe = threading.Event(); self._pe.set()
        self._paused = False

    def cancel(self):  self._cancel = True; self._pe.set()
    def pause(self):   self._paused = True; self._pe.clear()
    def resume(self):  self._paused = False; self._pe.set()
    @property
    def is_paused(self): return self._paused

    def run(self):
        sh = dh = None
        locks = []
        try:
            self.phase.emit("clone")
            self.log.emit(f"Source: Disk {self.src.index} — {self.src.model} ({self.src.size_str})")
            self.log.emit(f"Dest:   Disk {self.dst.index} — {self.dst.model} ({self.dst.size_str})")

            # Clear read-only attribute (USB drives sometimes get this)
            self.status.emit("Preparing destination disk...")
            clear_readonly(self.dst.index, log_fn=lambda m: self.log.emit(m))

            # Lock ALL volumes on dest disk — including hidden EFI/Recovery/MSR
            self.status.emit("Locking all destination volumes...")
            locks = lock_all_volumes(self.dst.index, log_fn=lambda m: self.log.emit(m))

            # Wipe partition table — forces Windows to release all volume references
            self.status.emit("Cleaning destination disk...")
            clean_disk(self.dst.index, log_fn=lambda m: self.log.emit(m))

            # Open drive handles AFTER cleaning
            self.status.emit("Opening drives...")
            sh = open_read(self.src.path)
            dh = open_write(self.dst.path)
            self.log.emit("Drive handles opened")

            total = drive_size(sh) or self.src.size
            self.log.emit(f"Clone size: {fmt_bytes(total)}")

            sec = max(self.src.sector, 512)
            chunk = CHUNK_SIZE if CHUNK_SIZE % sec == 0 else (CHUNK_SIZE // sec) * sec

            self.status.emit("Cloning...")
            trk = Tracker()
            done = 0; errors = 0
            buf = ctypes.create_string_buffer(chunk)
            br = wintypes.DWORD(0); bw = wintypes.DWORD(0)

            while done < total:
                if self._cancel: self.finished_sig.emit(False, "Cancelled"); return
                self._pe.wait()

                rs = min(chunk, total - done)
                if rs % sec: rs = ((rs // sec) + 1) * sec

                seek(sh, done)
                if not _ReadFile(sh, buf, rs, ctypes.byref(br), None) or br.value == 0:
                    errors += 1; self.log.emit(f"  Read error @ {done}, zero-filling")
                    ctypes.memset(buf, 0, rs); br.value = rs

                seek(dh, done)
                if not _WriteFile(dh, buf, br.value, ctypes.byref(bw), None):
                    err = _GetLastError()
                    errors += 1; self.log.emit(f"  Write error @ {done} (Win32 error {err})")

                done += br.value
                trk.tick(done, total, self)

            _FlushBuffers(dh)
            elapsed = time.time() - trk.t0
            self.log.emit(f"Clone done — {fmt_bytes(done)} in {fmt_eta(elapsed)}, "
                          f"avg {fmt_speed(done/elapsed if elapsed else 0)}")
            close(sh); close(dh); sh = dh = None

            # Verify
            mismatches = 0
            if self.verify and not self._cancel:
                mismatches = self._verify(total, sec)

            run_diskpart(f"select disk {self.dst.index}\nonline disk\n")

            # Check for expand opportunity
            if self.dst.size > total:
                self.offer_expand.emit(self.dst.index, self.dst.size - total)

            if errors:
                self.finished_sig.emit(False, f"Clone done with {errors} error(s). Check log.")
            elif self.verify and mismatches:
                self.finished_sig.emit(False, f"Clone done but {mismatches} verification mismatch(es)!")
            else:
                msg = "Clone completed successfully!"
                if self.verify: msg += " Verification passed."
                self.finished_sig.emit(True, msg)

        except Exception as e:
            self.log.emit(f"FATAL: {e}")
            self.finished_sig.emit(False, str(e))
        finally:
            close(sh); close(dh)
            for v in locks: unlock_vol(v)

    def _verify(self, total, sec):
        self.phase.emit("verify"); self.status.emit("Verifying...")
        self.progress.emit(0); self.log.emit("Verification pass...")
        sh = open_read(self.src.path); dh = open_read(self.dst.path)
        chunk = CHUNK_SIZE if CHUNK_SIZE % sec == 0 else (CHUNK_SIZE // sec) * sec
        sb = ctypes.create_string_buffer(chunk); db = ctypes.create_string_buffer(chunk)
        sr = wintypes.DWORD(0); dr = wintypes.DWORD(0)
        trk = Tracker(); done = 0; mm = 0
        while done < total:
            if self._cancel: break
            self._pe.wait()
            rs = min(chunk, total - done)
            if rs % sec: rs = ((rs // sec) + 1) * sec
            seek(sh, done); _ReadFile(sh, sb, rs, ctypes.byref(sr), None)
            seek(dh, done); _ReadFile(dh, db, rs, ctypes.byref(dr), None)
            actual = min(sr.value, dr.value, total - done)
            if sb[:actual] != db[:actual]:
                mm += 1; self.log.emit(f"  Mismatch @ offset {done}")
            done += actual; trk.tick(done, total, self)
        close(sh); close(dh)
        self.log.emit(f"Verify: {'PASSED' if mm == 0 else f'{mm} MISMATCH(ES)'}")
        return mm

# ═══════════════════════════════════════════════════════════════════════════════
# WORKER: DRIVE → IMAGE
# ═══════════════════════════════════════════════════════════════════════════════

class ImagingWorker(QThread):
    progress       = pyqtSignal(float)
    speed_update   = pyqtSignal(float)
    eta_update     = pyqtSignal(float)
    bytes_update   = pyqtSignal(int, int)
    status         = pyqtSignal(str)
    phase          = pyqtSignal(str)
    finished_sig   = pyqtSignal(bool, str)
    log            = pyqtSignal(str)

    def __init__(self, src: DriveInfo, path: str):
        super().__init__()
        self.src, self.img_path = src, path
        self._cancel = False
        self._pe = threading.Event(); self._pe.set()
        self._paused = False

    def cancel(self):  self._cancel = True; self._pe.set()
    def pause(self):   self._paused = True; self._pe.clear()
    def resume(self):  self._paused = False; self._pe.set()
    @property
    def is_paused(self): return self._paused

    def run(self):
        sh = None; fp = None
        try:
            self.phase.emit("image")
            self.status.emit("Preparing...")
            self.log.emit(f"Source: Disk {self.src.index} — {self.src.model}")
            self.log.emit(f"Image:  {self.img_path}")

            sh = open_read(self.src.path)
            total = drive_size(sh) or self.src.size
            sec = max(self.src.sector, 512)
            chunk = CHUNK_SIZE if CHUNK_SIZE % sec == 0 else (CHUNK_SIZE // sec) * sec
            self.log.emit(f"Drive size: {fmt_bytes(total)}")

            # Write header
            hdr = {"version": IMG_FMT_VER, "created": datetime.now().isoformat(),
                   "src_model": self.src.model, "src_serial": self.src.serial,
                   "src_size": total, "sector": self.src.sector, "chunk": chunk,
                   "compression": "zlib",
                   "parts": [p.to_dict() for p in self.src.parts]}
            hdr_z = zlib.compress(json.dumps(hdr).encode(), ZLIB_LEVEL)

            fp = open(self.img_path, "wb")
            fp.write(IMG_MAGIC)
            fp.write(struct.pack("<Q", len(hdr_z)))
            fp.write(hdr_z)

            # Write chunks
            self.status.emit("Imaging...")
            buf = ctypes.create_string_buffer(chunk)
            br = wintypes.DWORD(0)
            trk = Tracker(); done = 0; full_h = hashlib.sha256()

            while done < total:
                if self._cancel: self.finished_sig.emit(False, "Cancelled"); return
                self._pe.wait()
                rs = min(chunk, total - done)
                if rs % sec: rs = ((rs // sec) + 1) * sec

                seek(sh, done)
                if not _ReadFile(sh, buf, rs, ctypes.byref(br), None) or br.value == 0:
                    self.log.emit(f"  Read error @ {done}, zero-filling")
                    ctypes.memset(buf, 0, rs); br.value = rs

                actual = min(br.value, total - done)
                raw = bytes(buf[:actual])
                full_h.update(raw)
                ch = hashlib.sha256(raw).digest()
                comp = zlib.compress(raw, ZLIB_LEVEL)
                fp.write(struct.pack("<II", len(comp), actual))
                fp.write(ch)
                fp.write(comp)
                done += actual; trk.tick(done, total, self)

            # EOF marker + footer hash
            fp.write(struct.pack("<II", 0, 0))
            digest = full_h.hexdigest()
            fp.write(digest.encode("ascii"))
            fp.flush(); fp.close(); fp = None
            close(sh); sh = None

            fsize = os.path.getsize(self.img_path)
            ratio = fsize / total * 100 if total else 0
            elapsed = time.time() - trk.t0
            self.log.emit(f"{'='*50}")
            self.log.emit(f"Imaging complete!")
            self.log.emit(f"  Drive:  {fmt_bytes(total)}")
            self.log.emit(f"  Image:  {fmt_bytes(fsize)} ({ratio:.1f}%)")
            self.log.emit(f"  Time:   {fmt_eta(elapsed)}")
            self.log.emit(f"  SHA-256: {digest}")

            # Validate
            self.phase.emit("validate"); self.status.emit("Validating image...")
            self.progress.emit(0); self.log.emit("Validating image integrity...")
            ok, msg = self._validate(total)
            self.progress.emit(100)

            if ok:
                self.log.emit("Validation PASSED — image is intact and ready for use")
                self.finished_sig.emit(True,
                    f"Image saved and validated!\n"
                    f"Size: {fmt_bytes(fsize)} ({ratio:.1f}% of {fmt_bytes(total)})\n"
                    f"SHA-256: {digest[:32]}...")
            else:
                self.log.emit(f"Validation FAILED: {msg}")
                self.finished_sig.emit(False, f"Image saved but validation failed: {msg}")

        except Exception as e:
            self.log.emit(f"FATAL: {e}")
            self.finished_sig.emit(False, str(e))
        finally:
            close(sh)
            if fp: fp.close()

    def _validate(self, expected: int) -> Tuple[bool, str]:
        try:
            with open(self.img_path, "rb") as f:
                if f.read(8) != IMG_MAGIC: return False, "Bad magic"
                hl = struct.unpack("<Q", f.read(8))[0]; f.read(hl)
                fh = hashlib.sha256(); vb = 0; ci = 0
                while True:
                    if self._cancel: return False, "Cancelled"
                    raw8 = f.read(8)
                    if len(raw8) < 8: return False, "Unexpected EOF"
                    cs, os_ = struct.unpack("<II", raw8)
                    if cs == 0 and os_ == 0: break
                    sha = f.read(32); comp = f.read(cs)
                    if len(comp) != cs: return False, f"Chunk {ci} truncated"
                    raw = zlib.decompress(comp)
                    if len(raw) != os_: return False, f"Chunk {ci} decompressed size mismatch"
                    if hashlib.sha256(raw).digest() != sha: return False, f"Chunk {ci} hash mismatch"
                    fh.update(raw); vb += os_; ci += 1
                    if expected > 0:
                        self.progress.emit(min(vb / expected * 100, 100.0))
                        self.bytes_update.emit(vb, expected)
                stored = f.read(64).decode("ascii", errors="ignore")
                computed = fh.hexdigest()
                if stored != computed: return False, "Full image hash mismatch"
                return True, "OK"
        except Exception as e:
            return False, str(e)

# ═══════════════════════════════════════════════════════════════════════════════
# WORKER: IMAGE → DRIVE
# ═══════════════════════════════════════════════════════════════════════════════

class RestoreWorker(QThread):
    progress       = pyqtSignal(float)
    speed_update   = pyqtSignal(float)
    eta_update     = pyqtSignal(float)
    bytes_update   = pyqtSignal(int, int)
    status         = pyqtSignal(str)
    phase          = pyqtSignal(str)
    finished_sig   = pyqtSignal(bool, str)
    log            = pyqtSignal(str)
    offer_expand   = pyqtSignal(int, int)

    def __init__(self, path: str, dst: DriveInfo, verify: bool, validate: bool = True):
        super().__init__()
        self.img_path, self.dst, self.verify, self.validate = path, dst, verify, validate
        self._cancel = False
        self._pe = threading.Event(); self._pe.set()
        self._paused = False

    def cancel(self):  self._cancel = True; self._pe.set()
    def pause(self):   self._paused = True; self._pe.clear()
    def resume(self):  self._paused = False; self._pe.set()
    @property
    def is_paused(self): return self._paused

    def run(self):
        dh = None; fp = None; locks = []
        try:
            meta = read_image_meta(self.img_path)
            if not meta:
                self.finished_sig.emit(False, "Invalid or corrupt image file"); return

            self.log.emit(f"Image:  {self.img_path}")
            self.log.emit(f"Source: {meta.src_model} ({meta.size_str})")
            self.log.emit(f"Dest:   Disk {self.dst.index} — {self.dst.model} ({self.dst.size_str})")
            expected = meta.src_size

            # Validate image first (if enabled)
            if self.validate:
                self.phase.emit("validate"); self.status.emit("Validating image...")
                self.log.emit("Validating image before restore...")
                ok, vmsg = self._validate(expected)
                if not ok:
                    self.log.emit(f"Validation FAILED: {vmsg}")
                    self.finished_sig.emit(False, f"Image validation failed: {vmsg}"); return
                self.log.emit("Image validation PASSED — safe to restore")
            else:
                self.log.emit("Image validation skipped")

            # Clear read-only attribute (USB drives sometimes get this)
            self.phase.emit("restore"); self.status.emit("Preparing destination disk...")
            clear_readonly(self.dst.index, log_fn=lambda m: self.log.emit(m))

            # Lock ALL volumes on dest disk — including hidden EFI/Recovery/MSR
            self.status.emit("Locking all destination volumes...")
            locks = lock_all_volumes(self.dst.index, log_fn=lambda m: self.log.emit(m))

            # Wipe partition table — forces Windows to release all volume references
            self.status.emit("Cleaning destination disk...")
            clean_disk(self.dst.index, log_fn=lambda m: self.log.emit(m))

            # Open drive handle AFTER cleaning
            self.status.emit("Opening destination drive...")
            dh = open_write(self.dst.path)
            self.log.emit("Drive handle opened")

            # Restore
            self.status.emit("Restoring..."); self.progress.emit(0)
            fp = open(self.img_path, "rb")
            fp.read(8)  # magic
            hl = struct.unpack("<Q", fp.read(8))[0]; fp.read(hl)

            trk = Tracker(); done = 0; errors = 0
            bw = wintypes.DWORD(0)

            while True:
                if self._cancel: self.finished_sig.emit(False, "Cancelled"); return
                self._pe.wait()
                raw8 = fp.read(8)
                if len(raw8) < 8: break
                cs, os_ = struct.unpack("<II", raw8)
                if cs == 0 and os_ == 0: break

                sha = fp.read(32); comp = fp.read(cs)
                raw = zlib.decompress(comp)

                if hashlib.sha256(raw).digest() != sha:
                    errors += 1; self.log.emit(f"  Chunk hash mismatch @ offset {done}")

                wb = ctypes.create_string_buffer(raw)
                seek(dh, done)
                if not _WriteFile(dh, wb, len(raw), ctypes.byref(bw), None):
                    err = _GetLastError()
                    errors += 1; self.log.emit(f"  Write error @ {done} (Win32 error {err})")

                done += len(raw); trk.tick(done, expected, self)

            _FlushBuffers(dh); close(dh); dh = None
            fp.close(); fp = None

            elapsed = time.time() - trk.t0
            self.log.emit(f"Restore done — {fmt_bytes(done)} in {fmt_eta(elapsed)}")

            # Post-verify
            mm = 0
            if self.verify and not self._cancel:
                mm = self._post_verify(expected, meta.sector)

            run_diskpart(f"select disk {self.dst.index}\nonline disk\n")

            if self.dst.size > expected:
                self.offer_expand.emit(self.dst.index, self.dst.size - expected)

            if errors:
                self.finished_sig.emit(False, f"Restore done with {errors} error(s).")
            elif self.verify and mm:
                self.finished_sig.emit(False, f"Restore done but {mm} verify mismatch(es)!")
            else:
                msg = "Restore completed successfully!"
                if self.verify: msg += " Verification passed."
                self.finished_sig.emit(True, msg)

        except Exception as e:
            self.log.emit(f"FATAL: {e}")
            self.finished_sig.emit(False, str(e))
        finally:
            close(dh)
            if fp: fp.close()
            for v in locks: unlock_vol(v)

    def _validate(self, expected: int) -> Tuple[bool, str]:
        try:
            with open(self.img_path, "rb") as f:
                if f.read(8) != IMG_MAGIC: return False, "Bad magic"
                hl = struct.unpack("<Q", f.read(8))[0]; f.read(hl)
                fh = hashlib.sha256(); vb = 0; ci = 0
                while True:
                    if self._cancel: return False, "Cancelled"
                    raw8 = f.read(8)
                    if len(raw8) < 8: return False, "Truncated"
                    cs, os_ = struct.unpack("<II", raw8)
                    if cs == 0 and os_ == 0: break
                    sha = f.read(32); comp = f.read(cs)
                    raw = zlib.decompress(comp)
                    if hashlib.sha256(raw).digest() != sha: return False, f"Chunk {ci} bad"
                    fh.update(raw); vb += os_; ci += 1
                    if expected > 0:
                        self.progress.emit(min(vb / expected * 100, 100.0))
                        self.bytes_update.emit(vb, expected)
                stored = f.read(64).decode("ascii", errors="ignore")
                if stored != fh.hexdigest(): return False, "Full hash mismatch"
                return True, "OK"
        except Exception as e:
            return False, str(e)

    def _post_verify(self, expected, sec_size):
        self.phase.emit("verify"); self.status.emit("Post-restore verification...")
        self.progress.emit(0); self.log.emit("Verifying restored drive against image...")
        sec = max(sec_size, 512)
        dh = open_read(self.dst.path)
        fp = open(self.img_path, "rb")
        fp.read(8); hl = struct.unpack("<Q", fp.read(8))[0]; fp.read(hl)
        trk = Tracker(); off = 0; mm = 0; dbr = wintypes.DWORD(0)
        while True:
            if self._cancel: break
            self._pe.wait()
            raw8 = fp.read(8)
            if len(raw8) < 8: break
            cs, os_ = struct.unpack("<II", raw8)
            if cs == 0 and os_ == 0: break
            fp.read(32); comp = fp.read(cs)
            img_raw = zlib.decompress(comp)
            rs = len(img_raw)
            if rs % sec: rs = ((rs // sec) + 1) * sec
            db = ctypes.create_string_buffer(rs)
            seek(dh, off); _ReadFile(dh, db, rs, ctypes.byref(dbr), None)
            if bytes(db[:len(img_raw)]) != img_raw:
                mm += 1; self.log.emit(f"  Mismatch @ {off}")
            off += len(img_raw); trk.tick(off, expected, self)
        close(dh); fp.close()
        self.log.emit(f"Post-verify: {'PASSED' if mm == 0 else f'{mm} MISMATCH(ES)'}")
        return mm

# ═══════════════════════════════════════════════════════════════════════════════
# UI: SHARED STYLES & HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

DLG_STYLE = """
    QDialog { background: #1a1a2e; color: #e0e0e0; }
    QLabel { color: #e0e0e0; }
    QGroupBox { border: 1px solid #e94560; border-radius: 6px; margin-top: 12px;
                padding-top: 16px; font-weight: bold; color: #e94560; }
    QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
    QLineEdit { background: #16213e; border: 2px solid #e94560; border-radius: 4px;
                padding: 8px; color: #fff; font-size: 16px; font-weight: bold; }
"""
BTN_X = "QPushButton { background: #333; border: 1px solid #555; border-radius: 4px; padding: 8px 24px; color: #ccc; } QPushButton:hover { background: #444; }"
BTN_GO = "QPushButton { background: #e94560; border: none; border-radius: 4px; padding: 8px 24px; color: white; font-weight: bold; } QPushButton:hover { background: #c73e54; } QPushButton:disabled { background: #555; color: #888; }"

def _grp(title: str, lines: list) -> QGroupBox:
    g = QGroupBox(title)
    gl = QGridLayout(g)
    for i, txt in enumerate(lines):
        gl.addWidget(QLabel(txt), i, 0, 1, 2)
    return g

def drv_grp(title, d):
    pts = ", ".join(p.letter for p in d.parts if p.letter) or "None"
    return _grp(title, [f"Disk {d.index}: {d.model}", f"Size: {d.size_str}  •  Bus: {d.bus}", f"Partitions: {pts}"])

def img_grp(title, m, path):
    return _grp(title, [f"Source: {m.src_model}", f"Size: {m.size_str}  •  Created: {m.created[:19]}",
                        f"File: {Path(path).name}", f"SHA-256: {m.sha256[:32]}..."])

def size_label(level, msg):
    colors = {"ok": "#16c79a", "warn": "#f0a500", "error": "#e94560"}
    icons  = {"ok": "✅", "warn": "⚠️", "error": "❌"}
    lbl = QLabel(f"{icons.get(level,'')}  {msg}")
    lbl.setStyleSheet(f"color: {colors.get(level,'#ccc')}; font-weight: bold; padding: 6px;")
    lbl.setWordWrap(True)
    return lbl


class ConfirmDlg(QDialog):
    def __init__(self, title, widgets, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title); self.setMinimumWidth(540); self.setStyleSheet(DLG_STYLE)
        lo = QVBoxLayout(self); lo.setSpacing(12)
        w = QLabel("⚠️  WARNING: This will DESTROY all data on the destination!")
        w.setStyleSheet("color: #ff4444; font-size: 14px; font-weight: bold; padding: 8px;")
        w.setAlignment(Qt.AlignmentFlag.AlignCenter); w.setWordWrap(True); lo.addWidget(w)
        for ww in widgets: lo.addWidget(ww)
        lo.addWidget(QLabel('Type "CLONE" to confirm:'))
        self.inp = QLineEdit(); self.inp.setPlaceholderText("Type CLONE here..."); lo.addWidget(self.inp)
        bl = QHBoxLayout()
        cb = QPushButton("Cancel"); cb.setStyleSheet(BTN_X); cb.clicked.connect(self.reject)
        self.go = QPushButton("Start"); self.go.setEnabled(False); self.go.setStyleSheet(BTN_GO)
        self.go.clicked.connect(self.accept)
        self.inp.textChanged.connect(lambda t: self.go.setEnabled(t.strip().upper() == "CLONE"))
        bl.addWidget(cb); bl.addStretch(); bl.addWidget(self.go); lo.addLayout(bl)


class SimpleConfirmDlg(QDialog):
    """Confirm dialog without type-to-confirm (for non-destructive imaging)."""
    def __init__(self, title, widgets, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title); self.setMinimumWidth(500); self.setStyleSheet(DLG_STYLE)
        lo = QVBoxLayout(self); lo.setSpacing(12)
        for w in widgets: lo.addWidget(w)
        bl = QHBoxLayout()
        cb = QPushButton("Cancel"); cb.setStyleSheet(BTN_X); cb.clicked.connect(self.reject)
        sb = QPushButton("Start Imaging"); sb.setStyleSheet(BTN_GO); sb.clicked.connect(self.accept)
        bl.addWidget(cb); bl.addStretch(); bl.addWidget(sb); lo.addLayout(bl)

# ═══════════════════════════════════════════════════════════════════════════════
# UI: DRIVE CARD
# ═══════════════════════════════════════════════════════════════════════════════

class DriveCard(QFrame):
    clicked = pyqtSignal(object)
    def __init__(self, d: DriveInfo, parent=None):
        super().__init__(parent)
        self.drive = d; self._sel = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(88); self.setMaximumHeight(118)
        lo = QVBoxLayout(self); lo.setContentsMargins(12,8,12,8); lo.setSpacing(3)
        top = QHBoxLayout(); top.setSpacing(8)
        icon = {"NVMe":"⚡","USB":"🔌"}.get(d.bus, "💾" if "SSD" in d.model.upper() else "💿")
        il = QLabel(icon); il.setStyleSheet("font-size: 22px;"); top.addWidget(il)
        ml = QLabel(f"Disk {d.index}: {d.model}")
        ml.setStyleSheet("font-weight: bold; font-size: 13px; color: #fff;"); ml.setWordWrap(True)
        top.addWidget(ml, 1)
        sl = QLabel(d.size_str)
        sl.setStyleSheet("background:#16213e; border-radius:10px; padding:4px 10px; font-weight:bold; color:#00d2ff; font-size:12px;")
        top.addWidget(sl); lo.addLayout(top)
        tags = [d.bus, d.media]
        if d.is_system: tags.append("🖥️ System")
        if d.is_boot:   tags.append("🔑 Boot")
        pts = [p.letter for p in d.parts if p.letter]
        if pts: tags.append(f"[{', '.join(pts)}]")
        dl = QLabel("  •  ".join(tags)); dl.setStyleSheet("color:#888; font-size:11px; padding-left:34px;")
        lo.addWidget(dl)
        self._restyle()

    def _restyle(self):
        if self._sel:
            self.setStyleSheet("DriveCard{background:#1a2744; border:2px solid #00d2ff; border-radius:8px;}")
        else:
            self.setStyleSheet("DriveCard{background:#16213e; border:1px solid #2a3a5c; border-radius:8px;}"
                               "DriveCard:hover{border:1px solid #00d2ff; background:#1a2744;}")

    def set_selected(self, s): self._sel = s; self._restyle()
    def mousePressEvent(self, e): self.clicked.emit(self.drive)

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════════════════

M_D2D, M_D2I, M_I2D = 0, 1, 2

class MainWin(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setMinimumSize(1000, 780); self.resize(1100, 820)
        self.drives = []; self.src = self.dst = None
        self.img_path = None; self.img_meta = None
        self.worker = None; self.src_cards = []; self.dst_cards = []
        self.mode = M_D2D
        self._theme(); self._ui(); self._scan()

    def _theme(self):
        self.setStyleSheet("""
            QMainWindow{background:#0f0f23}
            QWidget{color:#e0e0e0; font-family:"Segoe UI",Arial,sans-serif}
            QGroupBox{border:1px solid #2a3a5c; border-radius:8px; margin-top:14px; padding-top:18px; font-weight:bold; font-size:13px}
            QGroupBox::title{subcontrol-origin:margin; left:14px; padding:0 8px; color:#00d2ff}
            QPushButton{background:#16213e; border:1px solid #2a3a5c; border-radius:6px; padding:8px 20px; color:#e0e0e0; font-weight:bold; font-size:12px}
            QPushButton:hover{background:#1a2744; border-color:#00d2ff}
            QPushButton:disabled{background:#111; color:#555; border-color:#222}
            QProgressBar{border:1px solid #2a3a5c; border-radius:6px; text-align:center; background:#16213e; color:#fff; font-weight:bold; height:28px}
            QProgressBar::chunk{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #00d2ff,stop:1 #0092b3); border-radius:5px}
            QTextEdit{background:#0a0a1a; border:1px solid #2a3a5c; border-radius:6px; color:#16c79a; font-family:"Cascadia Code","Consolas",monospace; font-size:11px; padding:6px}
            QCheckBox{spacing:8px} QCheckBox::indicator{width:18px; height:18px; border:2px solid #2a3a5c; border-radius:4px; background:#16213e}
            QCheckBox::indicator:checked{background:#00d2ff; border-color:#00d2ff}
            QStatusBar{background:#0a0a1a; color:#888; border-top:1px solid #2a3a5c}
            QRadioButton{spacing:8px; font-size:13px; font-weight:bold}
            QScrollArea{border:none; background:transparent}
        """)

    def _mk_scroll(self):
        sa = QScrollArea(); sa.setWidgetResizable(True)
        w = QWidget(); lo = QVBoxLayout(w); lo.setContentsMargins(4,4,4,4); lo.setSpacing(6); lo.addStretch()
        sa.setWidget(w); return sa, w, lo

    def _mk_panel(self, left_title, right_title, left_scroll, right_scroll, left_info, right_info):
        """Build a splitter with source/arrow/dest."""
        w = QWidget(); hl = QHBoxLayout(w); hl.setContentsMargins(0,0,0,0)
        sp = QSplitter(Qt.Orientation.Horizontal)
        lg = QGroupBox(left_title); ll = QVBoxLayout(lg); ll.addWidget(left_scroll); ll.addWidget(left_info); sp.addWidget(lg)
        aw = QWidget(); aw.setFixedWidth(60); al = QVBoxLayout(aw); al.addStretch()
        ar = QLabel("➡️"); ar.setStyleSheet("font-size:28px;"); ar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        al.addWidget(ar); al.addStretch(); sp.addWidget(aw)
        rg = QGroupBox(right_title); rl = QVBoxLayout(rg); rl.addWidget(right_scroll); rl.addWidget(right_info); sp.addWidget(rg)
        sp.setSizes([420,60,420]); hl.addWidget(sp); return w

    def _mk_file_panel(self, icon_txt, path_label, meta_label, btn_text, btn_handler, info_label):
        """Build a file picker panel (for image source or dest)."""
        sa = QScrollArea(); sa.setWidgetResizable(True); w = QWidget(); lo = QVBoxLayout(w)
        lo.addStretch()
        il = QLabel(icon_txt); il.setStyleSheet("font-size:48px;"); il.setAlignment(Qt.AlignmentFlag.AlignCenter); lo.addWidget(il)
        lo.addWidget(path_label)
        if meta_label: lo.addWidget(meta_label)
        bb = QPushButton(btn_text)
        bb.setStyleSheet("QPushButton{background:#1a2744; border:2px dashed #00d2ff; padding:12px; font-size:13px} QPushButton:hover{background:#1e3050}")
        bb.clicked.connect(btn_handler); lo.addWidget(bb)
        lo.addStretch(); lo.addWidget(info_label)
        sa.setWidget(w); return sa

    def _ui(self):
        c = QWidget(); self.setCentralWidget(c)
        m = QVBoxLayout(c); m.setContentsMargins(16,12,16,8); m.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        t = QLabel(f"⚡ {APP_NAME}"); t.setStyleSheet("font-size:24px; font-weight:bold; color:#00d2ff;"); hdr.addWidget(t)
        s = QLabel("Drive Cloning & Imaging"); s.setStyleSheet("font-size:13px; color:#888; padding-top:8px;"); hdr.addWidget(s)
        hdr.addStretch()
        self.scan_btn = QPushButton("🔄 Refresh"); self.scan_btn.clicked.connect(self._scan); hdr.addWidget(self.scan_btn)
        m.addLayout(hdr)

        # Mode bar
        mf = QFrame(); mf.setStyleSheet("QFrame{background:#16213e; border:1px solid #2a3a5c; border-radius:8px;}")
        ml = QHBoxLayout(mf); ml.setContentsMargins(16,10,16,10)
        ml.addWidget(QLabel("Mode:"))
        self.mg = QButtonGroup(self)
        self.rb = [QRadioButton("💿→💿 Drive to Drive"), QRadioButton("💿→📦 Drive to Image"), QRadioButton("📦→💿 Image to Drive")]
        self.rb[0].setChecked(True)
        for i,r in enumerate(self.rb): self.mg.addButton(r,i); ml.addWidget(r)
        ml.addStretch(); self.mg.idClicked.connect(self._mode_changed); m.addWidget(mf)

        # Panels
        self.stack = QStackedWidget()

        # D2D
        self.d2d_ss, self.d2d_sw, self.d2d_sl = self._mk_scroll()
        self.d2d_ds, self.d2d_dw, self.d2d_dl = self._mk_scroll()
        self.d2d_si = QLabel("Select source"); self.d2d_si.setStyleSheet("color:#666; font-style:italic; padding:4px;")
        self.d2d_di = QLabel("Select destination"); self.d2d_di.setStyleSheet("color:#666; font-style:italic; padding:4px;")
        self.stack.addWidget(self._mk_panel("📤 SOURCE — Clone From", "📥 DESTINATION — Clone To",
                                             self.d2d_ss, self.d2d_ds, self.d2d_si, self.d2d_di))

        # D2I
        self.d2i_ss, self.d2i_sw, self.d2i_sl = self._mk_scroll()
        self.d2i_si = QLabel("Select source drive"); self.d2i_si.setStyleSheet("color:#666; font-style:italic; padding:4px;")
        self.d2i_pl = QLabel("No file selected"); self.d2i_pl.setStyleSheet("color:#888; font-size:12px; padding:8px;"); self.d2i_pl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.d2i_pl.setWordWrap(True)
        self.d2i_di = QLabel("Choose save location"); self.d2i_di.setStyleSheet("color:#666; font-style:italic; padding:4px;")
        d2i_right = self._mk_file_panel("📦", self.d2i_pl, None, "📂 Choose Save Location", self._browse_save, self.d2i_di)
        d2i_w = QWidget(); d2i_hl = QHBoxLayout(d2i_w); d2i_hl.setContentsMargins(0,0,0,0)
        d2i_sp = QSplitter(Qt.Orientation.Horizontal)
        d2i_lg = QGroupBox("📤 SOURCE DRIVE — Image From"); d2i_ll = QVBoxLayout(d2i_lg); d2i_ll.addWidget(self.d2i_ss); d2i_ll.addWidget(self.d2i_si); d2i_sp.addWidget(d2i_lg)
        aw = QWidget(); aw.setFixedWidth(60); al = QVBoxLayout(aw); al.addStretch(); ar = QLabel("➡️"); ar.setStyleSheet("font-size:28px;"); ar.setAlignment(Qt.AlignmentFlag.AlignCenter); al.addWidget(ar); al.addStretch(); d2i_sp.addWidget(aw)
        d2i_rg = QGroupBox("📦 IMAGE FILE — Save To"); d2i_rl = QVBoxLayout(d2i_rg); d2i_rl.addWidget(d2i_right); d2i_rl.addWidget(self.d2i_di); d2i_sp.addWidget(d2i_rg)
        d2i_sp.setSizes([420,60,420]); d2i_hl.addWidget(d2i_sp)
        self.stack.addWidget(d2i_w)

        # I2D
        self.i2d_ds, self.i2d_dw, self.i2d_dl = self._mk_scroll()
        self.i2d_di = QLabel("Select destination"); self.i2d_di.setStyleSheet("color:#666; font-style:italic; padding:4px;")
        self.i2d_pl = QLabel("No image selected"); self.i2d_pl.setStyleSheet("color:#888; font-size:12px; padding:8px;"); self.i2d_pl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.i2d_pl.setWordWrap(True)
        self.i2d_ml = QLabel(""); self.i2d_ml.setStyleSheet("color:#16c79a; font-size:11px; padding:4px;"); self.i2d_ml.setAlignment(Qt.AlignmentFlag.AlignCenter); self.i2d_ml.setWordWrap(True)
        self.i2d_si = QLabel("Choose an image"); self.i2d_si.setStyleSheet("color:#666; font-style:italic; padding:4px;")
        i2d_left = self._mk_file_panel("📦", self.i2d_pl, self.i2d_ml, "📂 Choose Image File", self._browse_load, self.i2d_si)
        i2d_w = QWidget(); i2d_hl = QHBoxLayout(i2d_w); i2d_hl.setContentsMargins(0,0,0,0)
        i2d_sp = QSplitter(Qt.Orientation.Horizontal)
        i2d_lg = QGroupBox("📦 IMAGE FILE — Restore From"); i2d_ll = QVBoxLayout(i2d_lg); i2d_ll.addWidget(i2d_left); i2d_ll.addWidget(self.i2d_si); i2d_sp.addWidget(i2d_lg)
        aw2 = QWidget(); aw2.setFixedWidth(60); al2 = QVBoxLayout(aw2); al2.addStretch(); ar2 = QLabel("➡️"); ar2.setStyleSheet("font-size:28px;"); ar2.setAlignment(Qt.AlignmentFlag.AlignCenter); al2.addWidget(ar2); al2.addStretch(); i2d_sp.addWidget(aw2)
        i2d_rg = QGroupBox("📥 DESTINATION — Restore To"); i2d_rl = QVBoxLayout(i2d_rg); i2d_rl.addWidget(self.i2d_ds); i2d_rl.addWidget(self.i2d_di); i2d_sp.addWidget(i2d_rg)
        i2d_sp.setSizes([420,60,420]); i2d_hl.addWidget(i2d_sp)
        self.stack.addWidget(i2d_w)

        m.addWidget(self.stack, 1)

        # Controls
        cl = QHBoxLayout(); cl.setSpacing(16)
        self.vfy_cb = QCheckBox("Verify after operation"); self.vfy_cb.setChecked(True); cl.addWidget(self.vfy_cb)
        self.val_cb = QCheckBox("Validate image before restore"); self.val_cb.setChecked(True); cl.addWidget(self.val_cb)
        cl.addStretch()
        self.pause_btn = QPushButton("⏸️ Pause"); self.pause_btn.setEnabled(False); self.pause_btn.setFixedWidth(110); self.pause_btn.clicked.connect(self._pause); cl.addWidget(self.pause_btn)
        self.xbtn = QPushButton("✖️ Cancel"); self.xbtn.setEnabled(False); self.xbtn.setFixedWidth(110)
        self.xbtn.setStyleSheet("QPushButton{background:#3a1a1a; border:1px solid #e94560; color:#e94560} QPushButton:hover{background:#4a2020} QPushButton:disabled{background:#111; color:#555; border-color:#222}")
        self.xbtn.clicked.connect(self._cancel); cl.addWidget(self.xbtn)
        self.go_btn = QPushButton("🚀 Start Clone"); self.go_btn.setEnabled(False); self.go_btn.setFixedWidth(180)
        self.go_btn.setStyleSheet("QPushButton{background:#00d2ff; color:#0f0f23; font-size:14px; padding:10px 24px; border:none} QPushButton:hover{background:#00b8e6} QPushButton:disabled{background:#222; color:#555}")
        self.go_btn.clicked.connect(self._start); cl.addWidget(self.go_btn)
        m.addLayout(cl)

        # Progress
        pg = QGroupBox("Progress"); pl = QVBoxLayout(pg); pl.setSpacing(6)
        self.phase_lbl = QLabel("Idle"); self.phase_lbl.setStyleSheet("font-size:13px; font-weight:bold; color:#00d2ff;"); pl.addWidget(self.phase_lbl)
        self.pbar = QProgressBar(); self.pbar.setRange(0,1000); self.pbar.setValue(0); pl.addWidget(self.pbar)
        sl2 = QHBoxLayout()
        self.spd_lbl = QLabel("Speed: --"); self.spd_lbl.setStyleSheet("color:#aaa; font-size:11px;"); sl2.addWidget(self.spd_lbl)
        self.byt_lbl = QLabel("0 B / 0 B"); self.byt_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter); self.byt_lbl.setStyleSheet("color:#aaa; font-size:11px;"); sl2.addWidget(self.byt_lbl)
        self.eta_lbl = QLabel("ETA: --:--:--"); self.eta_lbl.setAlignment(Qt.AlignmentFlag.AlignRight); self.eta_lbl.setStyleSheet("color:#aaa; font-size:11px;"); sl2.addWidget(self.eta_lbl)
        pl.addLayout(sl2); m.addWidget(pg)

        self.log_box = QTextEdit(); self.log_box.setReadOnly(True); self.log_box.setMaximumHeight(150); self.log_box.setPlaceholderText("Operation log..."); m.addWidget(self.log_box)
        self.sbar = QStatusBar(); self.setStatusBar(self.sbar); self.sbar.showMessage("Ready")

    # ── Scanning ──
    def _scan(self):
        self.sbar.showMessage("Scanning drives..."); self.src = self.dst = None; self.go_btn.setEnabled(False)
        for c in self.src_cards + self.dst_cards: c.deleteLater()
        self.src_cards.clear(); self.dst_cards.clear()
        self.drives = enum_drives()
        if not self.drives: self.sbar.showMessage("No drives found — run as Administrator"); return

        for layout, widget, handler, kind in [
            (self.d2d_sl, self.d2d_sw, self._s_d2d_src, "s"), (self.d2d_dl, self.d2d_dw, self._s_d2d_dst, "d"),
            (self.d2i_sl, self.d2i_sw, self._s_d2i_src, "s"), (self.i2d_dl, self.i2d_dw, self._s_i2d_dst, "d"),
        ]:
            for d in self.drives:
                c = DriveCard(d); c.clicked.connect(handler)
                layout.insertWidget(layout.count()-1, c)
                (self.src_cards if kind == "s" else self.dst_cards).append(c)

        self.sbar.showMessage(f"Found {len(self.drives)} drive(s)")

    # ── Selections ──
    def _sel(self, cards, widget, drive, info_lbl, is_src):
        for c in cards:
            if c.parent() == widget: c.set_selected(c.drive.index == drive.index)
        info_lbl.setText(f"Disk {drive.index} — {drive.model} ({drive.size_str})")
        if is_src: self.src = drive
        else:      self.dst = drive
        self._upd_go()

    def _s_d2d_src(self, d): self._sel(self.src_cards, self.d2d_sw, d, self.d2d_si, True)
    def _s_d2d_dst(self, d): self._sel(self.dst_cards, self.d2d_dw, d, self.d2d_di, False)
    def _s_d2i_src(self, d): self._sel(self.src_cards, self.d2i_sw, d, self.d2i_si, True)
    def _s_i2d_dst(self, d): self._sel(self.dst_cards, self.i2d_dw, d, self.i2d_di, False)

    # ── File dialogs ──
    def _browse_save(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save Drive Image", "", "Cloned Image (*.cloned);;All Files (*)")
        if not p: return
        if not p.lower().endswith(".cloned"): p += ".cloned"
        self.img_path = p; self.d2i_pl.setText(p); self.d2i_di.setText(f"Save to: {Path(p).name}"); self._upd_go()

    def _browse_load(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open Image", "", "Cloned Image (*.cloned);;All Files (*)")
        if not p: return
        meta = read_image_meta(p)
        if not meta: QMessageBox.warning(self, "Invalid", "Not a valid .cloned image."); return
        self.img_path = p; self.img_meta = meta; self.i2d_pl.setText(p)
        self.i2d_ml.setText(f"Source: {meta.src_model}\nSize: {meta.size_str}  •  Created: {meta.created[:19]}\nSHA-256: {meta.sha256[:32]}...")
        self.i2d_si.setText(f"Image: {Path(p).name}"); self._upd_go()

    # ── Mode ──
    def _mode_changed(self, i):
        self.mode = i; self.src = self.dst = None; self.img_path = self.img_meta = None
        self.stack.setCurrentIndex(i)
        for c in self.src_cards + self.dst_cards: c.set_selected(False)
        self.go_btn.setText(["🚀 Start Clone", "🚀 Save Image", "🚀 Restore Image"][i])
        self.go_btn.setEnabled(False); self.sbar.showMessage("Select source and destination")

    def _upd_go(self):
        if self.worker: self.go_btn.setEnabled(False); return
        ok = False
        if self.mode == M_D2D: ok = self.src and self.dst and self.src.index != self.dst.index
        elif self.mode == M_D2I: ok = self.src is not None and self.img_path is not None
        elif self.mode == M_I2D: ok = self.img_path and self.img_meta and self.dst
        self.go_btn.setEnabled(bool(ok))

    # ── Start ──
    def _start(self):
        [self._go_d2d, self._go_d2i, self._go_i2d][self.mode]()

    def _go_d2d(self):
        s, d = self.src, self.dst
        if not s or not d or s.index == d.index: return

        if s.is_system:
            QMessageBox.information(self, "Active OS Drive",
                "You're cloning the drive Windows is currently running from.\n\n"
                "This works for most scenarios — Cloned reads raw sectors, and "
                "Windows handles open-file inconsistencies on boot via chkdsk.\n\n"
                "For maximum reliability on critical servers:\n"
                "• Connect both drives to a second PC and clone from there\n"
                "• Or boot from a WinPE USB and run Cloned from there\n\n"
                "For workstation/laptop migrations, a live clone works well.")

        if d.is_system:
            if QMessageBox.warning(self, "System Drive",
                f"Destination contains {sys_drive()}! This PC will be unbootable.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return

        lvl, msg = analyze_d2d(s, d)
        ws = [drv_grp("Source", s), drv_grp("Destination (OVERWRITTEN)", d), size_label(lvl, msg)]
        if ConfirmDlg("Confirm Clone", ws, self).exec() != QDialog.DialogCode.Accepted: return

        self.worker = CloneWorker(s, d, self.vfy_cb.isChecked())
        self.worker.offer_expand.connect(self._offer_expand)
        self._wire("clone")

    def _go_d2i(self):
        s = self.src
        if not s or not self.img_path: return
        lvl, msg = analyze_d2i(s, self.img_path)
        ws = [drv_grp("Source Drive", s), size_label(lvl, msg)]
        note = QLabel("Image will be compressed and automatically validated after creation.")
        note.setStyleSheet("color:#16c79a; padding:4px;"); ws.append(note)
        if lvl == "error":
            QMessageBox.critical(self, "Not Enough Space", msg); return
        if SimpleConfirmDlg("Confirm Imaging", ws, self).exec() != QDialog.DialogCode.Accepted: return
        self.worker = ImagingWorker(s, self.img_path)
        self._wire("image")

    def _go_i2d(self):
        d, meta = self.dst, self.img_meta
        if not d or not meta or not self.img_path: return

        if d.is_system:
            if QMessageBox.warning(self, "System Drive",
                f"Destination contains {sys_drive()}!\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return

        lvl, msg = analyze_i2d(meta, d)
        ws = [img_grp("Source Image", meta, self.img_path), drv_grp("Destination (OVERWRITTEN)", d), size_label(lvl, msg)]
        note = QLabel("Image integrity is verified before restore begins.")
        note.setStyleSheet("color:#16c79a; padding:4px;"); ws.append(note)
        if ConfirmDlg("Confirm Restore", ws, self).exec() != QDialog.DialogCode.Accepted: return

        self.worker = RestoreWorker(self.img_path, d, self.vfy_cb.isChecked(), self.val_cb.isChecked())
        self.worker.offer_expand.connect(self._offer_expand)
        self._wire("restore")

    def _wire(self, op):
        w = self.worker
        w.progress.connect(lambda p: self.pbar.setValue(int(p * 10)))
        w.speed_update.connect(lambda s: self.spd_lbl.setText(f"Speed: {fmt_speed(s)}"))
        w.eta_update.connect(lambda s: self.eta_lbl.setText(f"ETA: {fmt_eta(s)}"))
        w.bytes_update.connect(lambda d, t: self.byt_lbl.setText(f"{fmt_bytes(d)} / {fmt_bytes(t)}"))
        w.status.connect(self.sbar.showMessage)
        w.phase.connect(self._set_phase)
        w.log.connect(self._log)
        w.finished_sig.connect(self._done)

        self.log_box.clear()
        self._log(f"{'='*50}\n  {APP_NAME} v{APP_VERSION} — {op.title()}\n  {datetime.now():%Y-%m-%d %H:%M:%S}\n{'='*50}")
        w.start()
        prevent_sleep()

        self.go_btn.setEnabled(False); self.scan_btn.setEnabled(False); self.vfy_cb.setEnabled(False); self.val_cb.setEnabled(False)
        self.pause_btn.setEnabled(True); self.xbtn.setEnabled(True)
        for r in self.rb: r.setEnabled(False)

    def _set_phase(self, p):
        lbl = {"clone":"⚡ Cloning...","image":"📦 Imaging...","restore":"📥 Restoring...",
               "validate":"🔒 Validating...","verify":"🔍 Verifying..."}.get(p, p)
        clr = {"validate":"#f0a500","verify":"#16c79a"}.get(p, "#00d2ff")
        self.phase_lbl.setText(lbl); self.phase_lbl.setStyleSheet(f"font-size:13px; font-weight:bold; color:{clr};")
        self.pbar.setValue(0)

    def _pause(self):
        if not self.worker: return
        if self.worker.is_paused: self.worker.resume(); self.pause_btn.setText("⏸️ Pause")
        else: self.worker.pause(); self.pause_btn.setText("▶️ Resume")

    def _cancel(self):
        if self.worker and QMessageBox.question(self, "Cancel", "Cancel the operation?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.worker.cancel()

    def _offer_expand(self, disk_idx, extra):
        reply = QMessageBox.question(self, "Expand Partition?",
            f"The destination drive has {fmt_bytes(extra)} of unallocated space.\n\n"
            f"Would you like to expand the last partition to fill the entire drive?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if reply == QMessageBox.StandardButton.Yes:
            self._log("Expanding last partition...")
            ok, msg = expand_last_partition(disk_idx)
            self._log(f"  {'Success' if ok else 'Failed'}: {msg}")
            if ok:
                QMessageBox.information(self, "Expanded", f"Partition expanded: {msg}")
            else:
                QMessageBox.warning(self, "Expand Failed",
                    f"Could not auto-expand: {msg}\n\nYou can expand manually in Disk Management.")

    def _done(self, ok, msg):
        allow_sleep()
        self.worker = None
        ic = "✅" if ok else "❌"; c = "#16c79a" if ok else "#e94560"
        self.phase_lbl.setText(f"{ic} {'Complete' if ok else 'Failed'}")
        self.phase_lbl.setStyleSheet(f"font-size:13px; font-weight:bold; color:{c};")
        self.go_btn.setEnabled(False); self.scan_btn.setEnabled(True); self.vfy_cb.setEnabled(True); self.val_cb.setEnabled(True)
        self.pause_btn.setEnabled(False); self.xbtn.setEnabled(False)
        for r in self.rb: r.setEnabled(True)
        self._log(f"\n{msg}"); self.sbar.showMessage(msg)
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Result", msg)
        self._scan()

    def _log(self, t):
        self.log_box.append(t); self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    if sys.platform == "win32" and not is_admin():
        _a = QApplication(sys.argv)
        if QMessageBox.question(None, APP_NAME,
            f"{APP_NAME} needs Administrator privileges to access physical drives.\n\nElevate now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            elevate_uac()
        sys.exit(1)

    app = QApplication(sys.argv); app.setStyle("Fusion")
    p = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, "#0f0f23"), (QPalette.ColorRole.WindowText, "#e0e0e0"),
        (QPalette.ColorRole.Base, "#16213e"), (QPalette.ColorRole.AlternateBase, "#1a2744"),
        (QPalette.ColorRole.Text, "#e0e0e0"), (QPalette.ColorRole.Button, "#16213e"),
        (QPalette.ColorRole.ButtonText, "#e0e0e0"), (QPalette.ColorRole.Highlight, "#00d2ff"),
        (QPalette.ColorRole.HighlightedText, "#0f0f23")]:
        p.setColor(role, QColor(color))
    app.setPalette(p)

    w = MainWin(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

# ⚡ Cloned

**Sector-by-Sector Drive Cloning & Imaging Tool for Windows**

Cloned is a production-grade desktop application for cloning entire drives, saving drives to compressed validated image files, and restoring images back to physical hardware. Built for MSPs and IT professionals who need to rescue failing OS drives, create backup images, and migrate systems to new hardware.

---

## Features

### Three Operating Modes

- **💿→💿 Drive to Drive** — Direct sector-by-sector clone with optional partition expansion
- **💿→📦 Drive to Image** — Save a drive to a compressed, validated `.cloned` image file
- **📦→💿 Image to Drive** — Restore a validated `.cloned` image back to a physical drive

### Core Capabilities

- **Full Sector-by-Sector I/O** — Copies every byte including MBR/GPT partition tables, boot records, EFI system partitions, recovery partitions, and all filesystem data
- **All Drive Types** — HDD, SATA SSD, NVMe, and USB-connected external drives (internal + external)
- **Size Analysis** — Compares source vs destination before every operation. Warns if the destination is too small, shows exact size differences, and checks free space for image saves
- **Small → Large Cloning** — After cloning to a larger drive, Cloned offers to automatically expand the last partition to fill the entire drive
- **Compressed Images** — zlib compression reduces image file size (30–90% of original depending on drive content)
- **Automatic Validation** — Every image is validated chunk-by-chunk (SHA-256 per 4MB block + full-image digest) immediately after creation. Images are also validated before every restore. Corrupt images are refused.
- **Post-Operation Verification** — Optional byte-for-byte verification pass compares the destination against the source/image
- **UAC Elevation** — Automatic Administrator privilege request on launch via Windows UAC
- **Pause / Resume / Cancel** — Full control over long-running operations
- **Live Progress** — Real-time transfer speed, ETA, bytes transferred, and operation logging

### Safety

- **Type-to-confirm** dialog (type "CLONE") before any destructive operation
- **System drive detection** — Warns before overwriting the active Windows drive
- **Same-drive protection** — Cannot select the same drive as both source and destination
- **Free space analysis** — Blocks image saves that won't fit on the destination filesystem
- **Volume locking** — Destination volumes are locked, dismounted, and taken offline before writing

## Do I Need a Bootable Drive?

**Short answer: No, for most MSP workflows.**

The typical scenario — pulling a failing drive from a client PC, connecting it via USB dock, and cloning to new hardware — runs perfectly within Windows because the failing drive isn't the active OS.

For the less common case where you need to clone the currently-running Windows drive (e.g., migrating your own workstation), Cloned reads raw sectors and produces a clone that boots. Windows runs chkdsk on first boot to resolve any open-file inconsistencies. This works well for workstations and laptops.

For maximum reliability on critical servers, connect both drives to a second PC and clone there, or boot from a WinPE USB.

## Requirements

- Windows 10/11 (x64)
- Administrator privileges (auto-prompted via UAC)
- Python 3.10+ (for running from source)

## Installation

### Download Release

1. Go to [Releases](https://github.com/HawaiizFynest/Cloned/releases)
2. Download `Cloned.exe`
3. Run — UAC will prompt for Administrator

### Run from Source

```
git clone https://github.com/HawaiizFynest/Cloned.git
cd Cloned
pip install -r requirements.txt
python cloned.py
```

### Build EXE Locally

```
pip install pyinstaller
pyinstaller --onefile --windowed --name Cloned cloned.py
```

Output: `dist/Cloned.exe`

## Usage

### Drive-to-Drive Clone

1. Select **💿→💿 Drive to Drive** mode
2. Click the source drive (left panel)
3. Click the destination drive (right panel)
4. Review the size analysis — Cloned tells you if the destination fits
5. Click **Start Clone**, type `CLONE` to confirm
6. After cloning, if the destination is larger, Cloned offers to expand the partition to fill the drive

### Drive-to-Image (Backup)

1. Select **💿→📦 Drive to Image** mode
2. Click the source drive
3. Click **Choose Save Location** — Cloned checks if you have enough free space
4. Click **Save Image** — the image is compressed and automatically validated after creation

### Image-to-Drive (Restore)

1. Select **📦→💿 Image to Drive** mode
2. Click **Choose Image File** — metadata displays instantly (source drive, size, SHA-256)
3. Click the destination drive — Cloned compares the image size vs drive size
4. Click **Restore Image**, type `CLONE` to confirm
5. Image is validated before restore begins. After restore, partition expansion is offered if applicable.

### Boot Drive Migration Workflow

1. Connect the new drive via USB adapter/dock (or install internally)
2. Clone the source OS drive → new drive (or save to image, then restore)
3. Shut down, swap drives
4. Boot — if needed, use Windows Recovery USB:
   - "Repair your computer" → Command Prompt
   - `bootrec /fixboot` then `bcdboot C:\Windows`

## Image Format (.cloned)

Purpose-built for reliability:

- `CLONED01` magic header for instant identification
- JSON metadata: source model, serial, size, sector size, partition layout, timestamp
- Per-chunk SHA-256 hash (every 4MB block individually verified)
- zlib compression per chunk
- Full-image SHA-256 digest in footer
- EOF marker separating data from footer hash

## Project Structure

```
Cloned/
├── cloned.py                  # Main application
├── requirements.txt           # Python dependencies
├── LICENSE                    # MIT License
├── README.md                  # This file
└── .github/
    └── workflows/
        └── build.yml          # GitHub Actions release pipeline
```

## Tech Stack

- **Python 3.12** + **PyQt6** — Desktop GUI
- **ctypes** — Windows CreateFileW API for raw sector I/O with 64-bit SetFilePointerEx
- **zlib** + **hashlib** — Compression and SHA-256 integrity verification
- **PowerShell/WMI** — Drive enumeration and partition expansion (Resize-Partition)
- **diskpart** — Volume locking and disk online/offline management
- **PyInstaller** — Single-file EXE packaging
- **GitHub Actions** — Automated release builds on version tag push

## License

MIT License — See [LICENSE](LICENSE) for details.

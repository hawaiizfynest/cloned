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
- **All Drive Types** — HDD, SATA SSD, NVMe, and USB-connected external drives via caddies and docking stations
- **Size Analysis** — Compares source vs destination before every operation. Warns if the destination is too small, shows exact size differences, checks free space for image saves, and handles manufacturer variance between same-capacity drives
- **Small → Large Cloning** — After cloning to a larger drive, Cloned offers to automatically expand the last partition to fill the entire drive
- **Compressed Images** — zlib compression reduces image file size (30–90% of original depending on drive content)
- **Automatic Validation** — Every image is validated chunk-by-chunk (SHA-256 per 16MB block + full-image digest) immediately after creation. Images can optionally be validated before restore. Corrupt images are refused.
- **Post-Operation Verification** — Optional byte-for-byte verification pass compares the destination against the source/image
- **UAC Elevation** — Automatic Administrator privilege request on launch via Windows UAC
- **Sleep Prevention** — Prevents Windows from sleeping during operations so USB drives don't disconnect mid-clone
- **Pause / Resume / Cancel** — Full control over long-running operations

### v2.0.0 Improvements

- **Read/Write Retry with Backoff** — Failed reads and writes are retried up to 5 times with increasing delay (0.3s, 0.6s, 0.9s, 1.2s, 1.5s) before giving up. Critical for recovering data from failing drives where sectors may succeed on retry.
- **Completion Sound** — Plays a Windows notification sound when an operation finishes (success or failure) so you don't have to watch multi-hour clones
- **Auto-Save Log** — Every operation log is automatically saved as a timestamped `.log` file on the Desktop. Useful for client records — includes SHA-256 hashes, error details, elapsed time, and transfer speeds.
- **Human-Readable Error Codes** — Log messages now show "Access Denied (5)" or "I/O Device Error (1117)" instead of raw Win32 error numbers
- **Elapsed Time Display** — Live elapsed time counter in the progress area alongside speed and ETA
- **Performance Optimizations** — 16 MB chunks (4x larger), removed per-write cache flushing, sequential read hints, and faster compression for significantly improved transfer speeds especially on USB

### v2.0.1 — USB Disconnect Recovery

- **USB Glitch Recovery** — On write failure (error 5 or 21), Cloned closes the stale disk handle and reopens it. If the USB bridge briefly disconnected and came back, the operation continues from where it left off instead of failing every remaining chunk.
- **Cascade Abort** — If 10 consecutive chunks fail even after retries and handle reopening, Cloned aborts immediately with a clear message identifying a likely USB bridge disconnect. Prevents hours of wasted time writing to a dead handle.

### v2.0.2 — Diskpart Clean Retry

- **Diskpart Clean Retry** — If `diskpart clean` fails or times out, Cloned retries up to 3 attempts with a 3-second delay between each. If all attempts fail, the operation aborts entirely rather than proceeding with a potentially locked disk.

### Safety

- **Type-to-confirm** dialog (type "CLONE") before any destructive operation
- **Disk clean before write** — Wipes partition table via diskpart before clone/restore to guarantee write access. Retries up to 3 times on failure and aborts if unsuccessful.
- **Cascade failure protection** — Detects USB bridge disconnects (consecutive read/write failures) and aborts early with a clear diagnostic message
- **System drive detection** — Warns before overwriting the active Windows drive
- **Same-drive protection** — Cannot select the same drive as both source and destination
- **Free space analysis** — Blocks image saves that won't fit on the destination filesystem
- **Comprehensive volume locking** — All volumes on the destination disk are locked and dismounted, including hidden EFI/Recovery/MSR partitions without drive letters
- **Read-only clearing** — Automatically clears the read-only attribute that USB bridges sometimes set

## Do I Need a Bootable Drive?

**Short answer: No, for most MSP workflows.**

The typical scenario — pulling a failing drive from a client PC, connecting it via USB caddy, and cloning to a new drive in another caddy — runs perfectly within Windows because neither drive is the active OS.

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
pyinstaller --onefile --windowed --name Cloned --uac-admin cloned.py
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
4. Uncheck **Validate image before restore** if the image was previously validated and you want to skip the validation pass
5. Click **Restore Image**, type `CLONE` to confirm
6. After restore, partition expansion is offered if the destination is larger

### Boot Drive Migration Workflow

1. Connect the new drive via USB adapter/dock (or install internally)
2. Clone the source OS drive → new drive (or save to image, then restore)
3. Shut down, swap drives
4. Boot — if needed, use Windows Recovery USB:
   - "Repair your computer" → Command Prompt
   - `bootrec /fixboot` then `bcdboot C:\Windows`

### Operation Logs

Every operation automatically saves a log file to your Desktop with the naming format `Cloned_<operation>_<timestamp>.log`. These logs include the full operation details: source/destination info, SHA-256 hashes, any errors with human-readable codes, transfer speeds, and total elapsed time. Keep these for client records.

## Image Format (.cloned)

Purpose-built for reliability:

- `CLONED01` magic header for instant identification
- JSON metadata: source model, serial, size, sector size, partition layout, timestamp
- Per-chunk SHA-256 hash (every 16MB block individually verified)
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
- **diskpart** — Volume locking, read-only clearing, disk cleaning, and online/offline management
- **winsound** — Completion notifications
- **PyInstaller** — Single-file EXE packaging with UAC manifest
- **GitHub Actions** — Automated release builds on version tag push

## License

MIT License — See [LICENSE](LICENSE) for details.

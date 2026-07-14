"""Package Prose into dist/Prose/ and a distributable zip.

Usage:  py build.py
Then:   py install.py   (installs it and starts it with Windows)

Why --onedir and not --onefile
------------------------------
A --onefile exe is a self-extracting archive: every launch it unpacks ~90 files
into a fresh %TEMP%\\_MEIxxxx and boots Python from there. Antivirus real-time
scanners lock or quarantine those files mid-extraction, and Python then dies with
"Failed to import encodings module". It also looks like a malware dropper, which
is a large part of why PyInstaller binaries get false-positived.

--onedir extracts once, at install time. Nothing to race at launch, fewer
heuristics tripped, faster startup.

Secrets are never compiled in — the app reads .env at runtime. This script never
copies your real .env into dist/, because .env is hidden in Windows Explorer and
would silently ride along if you zipped the folder to share.
"""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from config import APP_VERSION, PUBLISHER

ROOT = Path(__file__).parent
ICON = ROOT / "prose.ico"
VERSION_FILE = ROOT / "build" / "version_info.txt"
DIST = ROOT / "dist"
APP_DIR = DIST / "Prose"
ZIP_PATH = DIST / f"Prose-{APP_VERSION}-win64.zip"

_VERSION_TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v0}, {v1}, {v2}, 0),
    prodvers=({v0}, {v1}, {v2}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '{publisher}'),
        StringStruct('FileDescription', 'Prose - AI voice dictation'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'Prose'),
        StringStruct('LegalCopyright', 'Copyright (c) {publisher}. MIT Licence.'),
        StringStruct('OriginalFilename', 'Prose.exe'),
        StringStruct('ProductName', 'Prose'),
        StringStruct('ProductVersion', '{version}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def make_icon() -> None:
    from icon import build_ico

    build_ico(ICON)
    print(f"[build] icon -> {ICON.name}")


def make_version_file() -> Path:
    """Embed publisher/product/version so Explorer shows real details, not blanks."""
    major, minor, patch = (APP_VERSION.split(".") + ["0", "0"])[:3]
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(
        _VERSION_TEMPLATE.format(
            v0=int(major), v1=int(minor), v2=int(patch),
            version=APP_VERSION, publisher=PUBLISHER,
        ),
        encoding="utf-8",
    )
    print(f"[build] version {APP_VERSION}, publisher {PUBLISHER!r}")
    return VERSION_FILE


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller not installed. Run: py -m pip install pyinstaller")

    make_icon()
    version_file = make_version_file()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",      # extract once at install time, not on every launch
        "--noconsole",   # no terminal window; output goes to the log file
        "--name", "Prose",
        "--icon", str(ICON),
        "--version-file", str(version_file),
        "main.py",
    ]
    print("[build] running PyInstaller (this takes ~1 min)...")
    subprocess.run(cmd, cwd=ROOT, check=True)

    exe = APP_DIR / "Prose.exe"
    if not exe.exists():
        sys.exit(f"expected {exe} — PyInstaller layout changed?")

    # Ship the template, never the real keys.
    example = ROOT / ".env.example"
    if example.exists():
        shutil.copy(example, APP_DIR / ".env.example")

    # One zip is the whole download.
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    print("[build] zipping...")
    shutil.make_archive(str(ZIP_PATH.with_suffix("")), "zip", root_dir=DIST, base_dir="Prose")

    digest = sha256(ZIP_PATH)
    (DIST / f"{ZIP_PATH.name}.sha256").write_text(f"{digest}  {ZIP_PATH.name}\n", encoding="utf-8")

    n_files = sum(1 for _ in APP_DIR.rglob("*") if _.is_file())
    print(f"\n[build] Done: {APP_DIR}  ({n_files} files)")
    print(f"[build] Zip : {ZIP_PATH}  ({ZIP_PATH.stat().st_size / 1_048_576:.0f} MB)")
    print(f"[build] SHA-256: {digest}")
    print("[build] Logs: %LOCALAPPDATA%\\Prose\\prose.log")

    if (APP_DIR / ".env").exists():
        print(
            "\n[build] !! dist/Prose/.env exists and holds your live API keys.\n"
            "[build] !! It is HIDDEN in Explorer — delete it before sharing,\n"
            "[build] !! or anyone you send it to will spend your credits."
        )
    else:
        print("\n[build] Clean — the zip contains no secrets and is safe to publish.")


if __name__ == "__main__":
    main()

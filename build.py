"""Package Prose into a single dist/Prose.exe.

Usage:  py build.py
Then:   put a .env with your API keys next to dist/Prose.exe.

Secrets are never compiled into the exe — it reads .env at runtime. This script
deliberately does NOT copy your real .env into dist/, because .env is hidden in
Windows Explorer and would silently ride along if you zipped the folder to share.

The exe is unsigned, so Windows SmartScreen will warn on first run. Embedding
version metadata (below) at least makes the file properties identify the app
instead of showing a blank, anonymous binary.
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
    """Multi-resolution .ico: crisp mic glyph at small sizes, full logo at large ones."""
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


def main() -> None:
    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            sys.exit("PyInstaller not installed. Run: py -m pip install pyinstaller")

    make_icon()
    version_file = make_version_file()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",     # single self-contained exe
        "--noconsole",   # no terminal window; output goes to the log file
        "--name", "Prose",
        "--icon", str(ICON),
        "--version-file", str(version_file),
        "main.py",
    ]
    print("[build] running PyInstaller (this takes ~1 min)...")
    subprocess.run(cmd, cwd=ROOT, check=True)

    dist = ROOT / "dist"
    exe = dist / "Prose.exe"

    # Ship the template, never the real keys.
    example = ROOT / ".env.example"
    if example.exists():
        shutil.copy(example, dist / ".env.example")

    # Publish this next to the download so people can verify what they got.
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    (dist / "Prose.exe.sha256").write_text(f"{digest}  Prose.exe\n", encoding="utf-8")

    size_mb = exe.stat().st_size / 1_048_576
    print(f"\n[build] Done: {exe}  ({size_mb:.0f} MB)")
    print(f"[build] SHA-256: {digest}")
    print("[build] Logs: %LOCALAPPDATA%\\Prose\\prose.log")

    if (dist / ".env").exists():
        print(
            "\n[build] !! dist/.env exists and holds your live API keys.\n"
            "[build] !! It is HIDDEN in Explorer — delete it before sharing this folder,\n"
            "[build] !! or anyone you send it to will spend your credits."
        )
    else:
        print("\n[build] dist/ is clean — Prose.exe alone is safe to share.")
        print("[build] On a machine with no keys it shows a setup dialog and saves")
        print("[build] the recipient's own keys to %APPDATA%\\Prose\\.env.")


if __name__ == "__main__":
    main()

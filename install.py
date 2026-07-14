"""Install Prose for the current user and start it with Windows.

    py install.py              copy the exe somewhere stable + enable startup
    py install.py --uninstall  undo it

Installs to %LOCALAPPDATA%\\Programs\\Prose so rebuilding dist/ can't break the
startup entry. No admin rights needed, nothing written outside your user profile.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import config
import startup

ROOT = Path(__file__).parent
SOURCE_DIR = ROOT / "dist" / "Prose"  # --onedir build: exe + _internal/
DEFAULT_DEST = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "Prose"


def _stop_running() -> None:
    subprocess.run(
        ["taskkill", "/IM", "Prose.exe", "/F"],
        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _seed_user_config(quiet: bool = False) -> None:
    """Copy existing keys into %APPDATA%\\Prose\\.env so the installed exe finds them.

    Skipped if a user config already exists. Without this the installed exe would
    show the setup dialog even though keys are sitting in the project folder.
    """
    if config.USER_ENV_PATH.exists():
        return
    if not config.GROQ_API_KEY:
        return  # nothing to seed; the setup dialog will ask on first launch

    from first_run import save_keys

    extra = ""
    from cleanup import PROVIDER_EXTRA_KEY

    name = PROVIDER_EXTRA_KEY.get(config.CLEANUP_PROVIDER)
    if name:
        extra = getattr(config, name, "")
    save_keys(config.GROQ_API_KEY, extra, config.CLEANUP_ENABLED)
    if not quiet:
        print(f"[install] copied your API keys to {config.USER_ENV_PATH}")


def install(dest_dir: Path, enable_startup: bool = True) -> Path:
    if not SOURCE_DIR.exists():
        sys.exit(f"{SOURCE_DIR} not found — run `py build.py` first.")

    _stop_running()
    # Replace wholesale: a stale _internal/ from an older build would break startup.
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
    shutil.copytree(SOURCE_DIR, dest_dir)
    dest = dest_dir / "Prose.exe"
    n = sum(1 for _ in dest_dir.rglob("*") if _.is_file())
    print(f"[install] {dest_dir}  ({n} files)")

    # Don't ship the user's own keys into the install dir; they live in %APPDATA%.
    (dest_dir / ".env").unlink(missing_ok=True)

    _seed_user_config()

    if enable_startup:
        startup.enable(f'"{dest}"')
        print("[install] will start with Windows")
    return dest


def uninstall(dest_dir: Path) -> None:
    _stop_running()
    startup.disable()
    print("[uninstall] removed from Windows startup")
    if dest_dir.exists():
        shutil.rmtree(dest_dir, ignore_errors=True)
        print(f"[uninstall] deleted {dest_dir}")
    print(f"[uninstall] your API keys remain at {config.USER_ENV_PATH} (delete manually if you like)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uninstall", action="store_true", help="remove Prose and its startup entry")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="install directory")
    ap.add_argument("--no-startup", action="store_true", help="install but don't run at logon")
    args = ap.parse_args()

    if args.uninstall:
        uninstall(args.dest)
        return

    dest = install(args.dest, enable_startup=not args.no_startup)
    print(f"\nDone. Launch it now:  {dest}")
    print("Toggle startup any time from the tray menu → “Start with Windows”.")


if __name__ == "__main__":
    main()

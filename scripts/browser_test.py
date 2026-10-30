"""Install project-local Chromium and run real browser tests."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-install", action="store_true")
    options = parser.parse_args()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    environment = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / "data" / "browsers"), "PLAYWRIGHT_SKIP_BROWSER_GC": "1"}
    if not npm:
        raise SystemExit("npm 未安装")
    if not options.skip_install:
        subprocess.run([npm, "exec", "--", "playwright", "install", "chromium"], cwd=ROOT / "frontend", env=environment, check=True)
    for suffix in ("", "-wal", "-shm"):
        (ROOT / "data" / f"e2e.db{suffix}").unlink(missing_ok=True)
    subprocess.run([npm, "run", "test:e2e"], cwd=ROOT / "frontend", env=environment, check=True)


if __name__ == "__main__":
    main()

"""Install the Chromium expected by Playwright: python -m install_chromium.

On macOS, use curl and ditto for the official Chrome for Testing archive. Elsewhere,
delegate installation to Playwright. No extra Python dependencies are needed.
"""

from importlib.resources import files
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright


def main() -> None:
    if sys.platform != "darwin":
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium", "--no-shell"],
                       check=True)
        return
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
    destination = next(parent for parent in executable.parents
                       if parent.name.startswith("chromium-"))
    package = files("playwright").joinpath("driver/package").resolve()
    links = destination.parent / ".links"
    links.mkdir(parents=True, exist_ok=True)
    links.joinpath(hashlib.sha1(str(package).encode()).hexdigest()).write_text(str(package))
    if executable.is_file() and destination.joinpath("INSTALLATION_COMPLETE").is_file():
        print(f"Chromium is already installed: {executable}")
        return
    architecture = {"arm64": "mac-arm64", "x86_64": "mac-x64"}[platform.machine()]
    metadata = json.loads(package.joinpath("browsers.json").read_text())
    chromium = next(browser for browser in metadata["browsers"] if browser["name"] == "chromium")
    version = chromium["browserVersion"]
    url = (f"https://storage.googleapis.com/chrome-for-testing-public/{version}/"
           f"{architecture}/chrome-{architecture}.zip")
    print(f"Installing Chrome for Testing {version} from {url}", flush=True)
    with tempfile.TemporaryDirectory(prefix="chromium-download-",
                                     dir=destination.parent) as temporary:
        folder = Path(temporary)
        archive, extracted = folder / "chrome.zip", folder / "extracted"
        subprocess.run(["curl", "--fail", "--location", "--retry", "2", "--connect-timeout", "30",
                        "--max-time", "300", "--output", str(archive), url], check=True)
        subprocess.run(["ditto", "-x", "-k", str(archive), str(extracted)], check=True)
        installed = extracted / executable.relative_to(destination)
        if not installed.is_file():
            missing = installed.relative_to(extracted)
            raise RuntimeError(f"Downloaded archive does not contain {missing}")
        extracted.joinpath("INSTALLATION_COMPLETE").touch()
        if destination.exists():
            shutil.rmtree(destination)
        extracted.rename(destination)
    print(f"Installed Chromium: {executable}")


if __name__ == "__main__":
    main()

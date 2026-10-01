"""Launcher for uv: configure OpenRouter, install Chromium, and run agent.py."""

from getpass import getpass
import os
from pathlib import Path
import runpy
import subprocess
import sys

from playwright.sync_api import sync_playwright


def main() -> None:
    args = sys.argv[1:]
    if not any(arg in ("-h", "--help") for arg in args):
        if not args:
            sys.argv.append(input("Task: "))
        os.environ.setdefault("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
        if not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = getpass("OpenRouter API key: ")
        if not any(arg == "--model" or arg.startswith("--model=") for arg in args):
            sys.argv.extend(["--model", "google/gemini-3.8-flash"])
        if "--connect" not in args:
            with sync_playwright() as playwright:
                executable = Path(playwright.chromium.executable_path)
            if not executable.exists():
                subprocess.run([sys.executable, "-m", "playwright", "install",
                                "chromium", "--no-shell"], check=True)
    runpy.run_module("agent", run_name="__main__")


if __name__ == "__main__":
    main()

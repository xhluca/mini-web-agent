"""Installer recovery and cache tests; real macOS installation is verified separately."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from install_chromium import main


class InstallerTests(unittest.TestCase):
    def test_linux_uses_playwright_installer(self):
        with (patch("install_chromium.sys.platform", "linux"),
              patch("install_chromium.subprocess.run") as run):
            main()
        self.assertEqual(run.call_args.args[0][1:],
                         ["-m", "playwright", "install", "chromium", "--no-shell"])
        self.assertTrue(run.call_args.kwargs["check"])

    def test_macos_install_cache_and_download_failure(self):
        for architecture, package in (("arm64", "mac-arm64"), ("x86_64", "mac-x64")):
            with (self.subTest(architecture=architecture),
                  tempfile.TemporaryDirectory() as temporary):
                root = Path(temporary)
                cache = root / "chromium-9999"
                relative = Path(package) / "Chrome.app/Contents/MacOS/Chrome"
                executable = cache / relative
                metadata = root / "driver/package/browsers.json"
                metadata.parent.mkdir(parents=True)
                metadata.write_text('{"browsers":[{"name":"chromium",'
                                    '"browserVersion":"153.0.8010.12"}]}')
                cache.mkdir()
                sentinel = cache / "incomplete-install"
                sentinel.write_text("preserve until replacement succeeds")
                with (patch("install_chromium.sys.platform", "darwin"),
                      patch("install_chromium.platform.machine", return_value=architecture),
                      patch("install_chromium.files", return_value=root),
                      patch("install_chromium.sync_playwright") as playwright,
                      patch("install_chromium.subprocess.run") as run):
                    driver = playwright.return_value.__enter__.return_value
                    driver.chromium.executable_path = str(executable)
                    run.side_effect = subprocess.CalledProcessError(22, "curl")
                    with self.assertRaises(subprocess.CalledProcessError):
                        main()
                    self.assertTrue(sentinel.is_file())
                    self.assertFalse(cache.joinpath("INSTALLATION_COMPLETE").exists())

                    def extract(arguments, **kwargs):
                        if arguments[0] == "ditto":
                            installed = Path(arguments[-1]) / relative
                            installed.parent.mkdir(parents=True)
                            installed.write_bytes(b"browser")

                    run.reset_mock()
                    run.side_effect = extract
                    main()
                    download = run.call_args_list[0].args[0]
                    self.assertEqual(download[-1],
                        f"https://storage.googleapis.com/chrome-for-testing-public/153.0.8010.12/"
                        f"{package}/chrome-{package}.zip")
                    self.assertTrue(executable.is_file())
                    self.assertTrue(cache.joinpath("INSTALLATION_COMPLETE").is_file())
                    registered = [link.read_text() for link in root.joinpath(".links").iterdir()]
                    self.assertEqual(registered, [str(root.joinpath("driver/package").resolve())])
                    self.assertFalse(sentinel.exists())
                    run.reset_mock()
                    main()
                    run.assert_not_called()

    def test_incomplete_archive_preserves_previous_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "chromium-9999/chrome-mac-arm64/Chrome.app/Contents/MacOS/Chrome"
            executable.parents[4].mkdir(parents=True)
            metadata = root / "driver/package/browsers.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text('{"browsers":[{"name":"chromium","browserVersion":"153"}]}')
            with (patch("install_chromium.sys.platform", "darwin"),
                  patch("install_chromium.platform.machine", return_value="arm64"),
                  patch("install_chromium.files", return_value=root),
                  patch("install_chromium.sync_playwright") as playwright,
                  patch("install_chromium.subprocess.run")):
                driver = playwright.return_value.__enter__.return_value
                driver.chromium.executable_path = str(executable)
                with self.assertRaisesRegex(RuntimeError, "does not contain"):
                    main()
                self.assertTrue(executable.parents[4].is_dir())
                self.assertFalse(executable.parents[4].joinpath("INSTALLATION_COMPLETE").exists())


if __name__ == "__main__":
    unittest.main()

"""Pruebas de las dos ubicaciones de migración con un home desechable en Ubuntu."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "fclean"


@unittest.skipUnless(sys.platform.startswith("linux"), "requiere Ubuntu y /proc")
class CleanLocationsTests(unittest.TestCase):
    def setUp(self):
        import pwd

        if os.getuid() == 0:
            self.skipTest("fclean se ejecuta como usuario del campus")
        self.addCleanup(os.umask, os.umask(0o077))
        self.home = self.new_directory()
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.runtime = self.new_directory()

    def new_directory(self, parent=None):
        temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temp.cleanup)
        return Path(temp.name).resolve()

    def mount_sgoinfre(self):
        shm = Path("/dev/shm")
        if not shm.is_dir() or not shm.is_mount() or shm.stat().st_dev == self.home.stat().st_dev:
            self.skipTest("requiere /dev/shm en otro sistema de archivos")
        shared = self.new_directory(shm)
        (self.home / "sgoinfre").symlink_to(shared, target_is_directory=True)
        personal = shared / self.user
        personal.mkdir()
        return personal

    def run_cli(self, mode, commands=None):
        env = dict(os.environ, HOME=str(self.home), USER=self.user, XDG_RUNTIME_DIR=str(self.runtime),
                   XDG_CONFIG_HOME=str(self.home / ".config"))
        env.pop("XDG_CACHE_HOME", None)
        if commands:
            env["PATH"] = str(commands) + os.pathsep + env["PATH"]
        return subprocess.run(["bash", SCRIPT, mode], env=env, cwd=self.home,
                              check=True, capture_output=True, text=True).stdout

    def test_unmigrated_cache_without_sgoinfre(self):
        cache = self.home / ".cache"
        cache.mkdir()
        (cache / "local").touch()
        self.run_cli("--clean")
        self.assertEqual(list(cache.iterdir()), [])

    def test_both_copies_without_shell_configuration(self):
        personal = self.mount_sgoinfre()
        sentinel = personal / "datos"
        sentinel.touch()
        for base in (self.home, personal):
            cache = base / ".cache"
            extensions = base / ".vscode/extensions"
            cache.mkdir()
            extensions.mkdir(parents=True)
            for root in (cache, extensions):
                (root / "visible").touch()
                (root / ".oculto").touch()
                (root / "enlace").symlink_to(sentinel)
            extension = extensions / "ejemplo.editor-1.0"
            discarded = extension / "Cache/old_Cache_Data_001"
            discarded.mkdir(parents=True)
            (extension / "package.json").write_text("{}")
            (discarded / "0123456789abcdef_0").touch()
            (discarded / "indice-incluido").touch()
        self.run_cli("--clean")
        for base in (self.home, personal):
            self.assertEqual([p.name for p in (base / ".cache").iterdir()], [".oculto"])
            self.assertTrue((base / ".vscode/extensions/visible").exists())
        self.run_cli("--fclean")
        for base in (self.home, personal):
            self.assertEqual(list((base / ".cache").iterdir()), [])
            extensions = base / ".vscode/extensions"
            self.assertTrue((extensions / "visible").is_file())
            self.assertTrue((extensions / ".oculto").is_file())
            self.assertTrue((extensions / "enlace").is_symlink())
            extension = extensions / "ejemplo.editor-1.0"
            self.assertEqual((extension / "package.json").read_text(), "{}")
            self.assertEqual([p.name for p in (extension / "Cache/old_Cache_Data_001").iterdir()],
                             ["indice-incluido"])
        self.assertTrue(sentinel.exists())

    def test_migrated_links_survive_and_cache_is_reported_once(self):
        personal = self.mount_sgoinfre()
        for relative in (".cache", ".vscode/extensions"):
            target = personal / relative
            target.mkdir(parents=True)
            (target / "visible").touch()
            link = self.home / relative
            link.parent.mkdir(exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
        preview = self.run_cli("--dry-run")
        self.assertEqual(preview.count(f"\t{str(personal / '.cache')!r} ("), 1)
        self.assertTrue((personal / ".cache/visible").exists())
        self.run_cli("--clean")
        self.assertEqual(list((personal / ".cache").iterdir()), [])
        self.assertTrue((personal / ".vscode/extensions/visible").exists())
        self.run_cli("--fclean")
        for relative in (".cache", ".vscode/extensions"):
            self.assertTrue((self.home / relative).is_symlink())
        self.assertEqual(list((personal / ".cache").iterdir()), [])
        self.assertTrue((personal / ".vscode/extensions/visible").is_file())

    def test_fclean_cleans_busy_applications_and_preserves_profile(self):
        commands = self.new_directory()
        for name, output in (("pgrep", "exit 0"), ("flatpak", "echo com.slack.Slack")):
            stub = commands / name
            stub.write_text("#!/bin/sh\n" + output + "\n")
            stub.chmod(0o700)
        targets = (
            ".config/Slack/Service Worker/CacheStorage",
            ".config/google-chrome/Default/Cache",
            ".config/Code/CachedExtensionVSIXs",
            ".var/app/com.slack.Slack/config/Slack/Code Cache",
            "snap/firefox/common/.cache",
            "snap/chromium/common/.cache",
        )
        for relative in targets:
            directory = self.home / relative
            directory.mkdir(parents=True)
            (directory / "cached").write_bytes(b"datos")
        lock = self.home / ".config/google-chrome/SingletonLock"
        lock.symlink_to("puesto-1234")
        prefs = self.home / ".config/google-chrome/Default/Preferences"
        prefs.write_text("{}")
        self.run_cli("--clean", commands)
        self.assertTrue(all((self.home / r / "cached").exists() for r in targets))
        with (self.home / targets[0] / "cached").open("rb") as handle:
            self.run_cli("--fclean", commands)
            self.assertTrue(all(not (self.home / r / "cached").exists() for r in targets))
            self.assertEqual(handle.read(), b"datos")
        self.assertTrue(lock.is_symlink())
        self.assertEqual(prefs.read_text(), "{}")


if __name__ == "__main__":
    unittest.main()

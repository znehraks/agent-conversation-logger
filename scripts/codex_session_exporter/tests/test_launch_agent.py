from pathlib import Path
import tempfile
import unittest

from codex_session_exporter.install_launch_agent import build_launch_agent_plist, choose_python_path, ensure_obsidian_symlink


class LaunchAgentTest(unittest.TestCase):
    def test_build_launch_agent_plist_runs_exporter_on_interval(self) -> None:
        plist = build_launch_agent_plist(
            label="com.example.codex-exporter",
            python_path=Path("/usr/local/bin/python3"),
            exporter_path=Path("/workspace/codex_session_exporter/exporter.py"),
            codex_home=Path("/Users/example/.codex"),
            output_root=Path("/Users/example/Vault/개발/codex-logs"),
            interval_seconds=30,
            stdout_path=Path("/Users/example/.codex/exporter.out.log"),
            stderr_path=Path("/Users/example/.codex/exporter.err.log"),
            working_directory=Path("/workspace"),
            include_active=True,
        )

        self.assertEqual(plist["Label"], "com.example.codex-exporter")
        self.assertEqual(plist["StartInterval"], 30)
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["WorkingDirectory"], "/workspace")
        self.assertEqual(plist["StandardOutPath"], "/Users/example/.codex/exporter.out.log")
        self.assertEqual(plist["StandardErrorPath"], "/Users/example/.codex/exporter.err.log")
        self.assertEqual(
            plist["ProgramArguments"],
            [
                "/usr/bin/env",
                "-i",
                "PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME=/Users/example",
                "USER=example",
                "/usr/local/bin/python3",
                "/workspace/codex_session_exporter/exporter.py",
                "--codex-home",
                "/Users/example/.codex",
                "--output-root",
                "/Users/example/Vault/개발/codex-logs",
                "--append-live",
                "--active-within-hours",
                "24",
                "--max-active-mb",
                "10",
                "--limit",
                "50",
            ],
        )

    def test_choose_python_path_prefers_homebrew_python(self) -> None:
        self.assertEqual(
            choose_python_path(
                candidates=[
                    Path("/usr/bin/python3"),
                    Path("/usr/local/bin/python3"),
                ],
                exists=lambda path: path == Path("/usr/bin/python3"),
                fallback=Path("/usr/local/bin/python3"),
            ),
            Path("/usr/bin/python3"),
        )

    def test_ensure_obsidian_symlink_moves_existing_directory_to_local_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            link_path = tmp / "vault" / "codex-logs"
            target_path = tmp / "local" / "obsidian-output"
            link_path.mkdir(parents=True)
            (link_path / "existing.md").write_text("hello", encoding="utf-8")

            backup_path = ensure_obsidian_symlink(link_path, target_path, timestamp="20260527T120000")

            self.assertTrue(link_path.is_symlink())
            self.assertEqual(link_path.resolve(), target_path.resolve())
            self.assertEqual((target_path / "existing.md").read_text(encoding="utf-8"), "hello")
            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.exists())


if __name__ == "__main__":
    unittest.main()

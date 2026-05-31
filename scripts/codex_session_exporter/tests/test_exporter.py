import json
import os
from pathlib import Path
import time
import unittest

from codex_session_exporter.exporter import (
    ExportConfig,
    append_from_hook_input,
    append_live_session_file,
    append_live_sessions,
    export_session_file,
    parse_complete_jsonl_rows,
    parse_session_file,
    redact_secrets,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


class CodexSessionExporterTest(unittest.TestCase):
    def test_parse_session_extracts_prompt_context_tool_and_quality_fields(self) -> None:
        with self.subTest("parse a representative Codex archived session"):
            tmp = Path(self._testMethodName)
            tmp.mkdir(exist_ok=True)
            session_path = tmp / "rollout-2026-05-27T12-00-00-019e-test.jsonl"
            write_jsonl(
                session_path,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-05-27T03:00:00.000Z",
                        "payload": {
                            "id": "019e-test",
                            "timestamp": "2026-05-27T03:00:00.000Z",
                            "cwd": "/workspace/project",
                            "originator": "Codex Desktop",
                            "cli_version": "0.133.0-alpha.1",
                            "model_provider": "openai",
                            "base_instructions": {"text": "developer prompt with sk-secret"},
                            "git": {"branch": "main"},
                        },
                    },
                    {
                        "type": "turn_context",
                        "timestamp": "2026-05-27T03:00:01.000Z",
                        "payload": {
                            "turn_id": "turn-1",
                            "cwd": "/workspace/project",
                            "model": "gpt-5.4",
                            "effort": "high",
                            "approval_policy": "never",
                            "sandbox_policy": {"type": "danger-full-access"},
                            "timezone": "Asia/Seoul",
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-05-27T03:00:02.000Z",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "src/app.ts를 코드리뷰해. npm test로 검증해줘.",
                                }
                            ],
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-05-27T03:00:04.000Z",
                        "payload": {
                            "type": "function_call",
                            "name": "exec_command",
                            "call_id": "call-1",
                            "arguments": json.dumps(
                                {"cmd": "npm test", "workdir": "/workspace/project"},
                                ensure_ascii=False,
                            ),
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-05-27T03:00:05.000Z",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "call-1",
                            "output": "Process exited with code 0\nOutput:\n1 passed",
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-05-27T03:00:06.000Z",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "검토했고 테스트도 통과했습니다."}],
                        },
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-05-27T03:00:07.000Z",
                        "payload": {"type": "task_complete", "turn_id": "turn-1"},
                    },
                ],
            )

            parsed = parse_session_file(session_path, include_developer_prompts=False)

            self.assertEqual(parsed.session["session_id"], "019e-test")
            self.assertEqual(parsed.session["task_type"], "code_review")
            self.assertEqual(parsed.session["requested_mode"], "review")
            self.assertEqual(parsed.session["completion_status"], "complete")
            self.assertEqual(parsed.session["verification_commands"], ["npm test"])
            self.assertEqual(parsed.session["mentioned_files"], ["src/app.ts"])
            self.assertTrue(parsed.session["developer_prompt_present"])
            self.assertNotIn("developer prompt", json.dumps(parsed.turns, ensure_ascii=False))
            self.assertEqual(parsed.tool_calls[0]["name"], "exec_command")
            self.assertEqual(parsed.tool_calls[0]["exit_code"], 0)

            for child in tmp.iterdir():
                child.unlink()
            tmp.rmdir()

    def test_export_session_writes_markdown_and_append_only_analysis_jsonl(self) -> None:
        with self.subTest("export one session into an Obsidian-compatible layout"):
            tmp = Path(self._testMethodName)
            tmp.mkdir(exist_ok=True)
            source = tmp / "rollout-2026-05-27T12-00-00-019e-export.jsonl"
            vault = tmp / "vault"
            write_jsonl(
                source,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-05-27T03:00:00.000Z",
                        "payload": {"id": "019e-export", "cwd": "/workspace/project", "timestamp": "2026-05-27T03:00:00.000Z"},
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-05-27T03:00:02.000Z",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "이 버그를 고쳐줘. tests/test_app.py"}],
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-05-27T03:00:03.000Z",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "수정했습니다."}],
                        },
                    },
                ],
            )

            result = export_session_file(source, ExportConfig(output_root=vault, force=True))

            self.assertTrue(result.markdown_path.exists())
            self.assertIn("Prompt Anatomy", result.markdown_path.read_text(encoding="utf-8"))
            self.assertTrue((vault / "data" / "codex_sessions.jsonl").exists())
            self.assertTrue((vault / "data" / "codex_turns.jsonl").exists())
            self.assertTrue((vault / "data" / "codex_tool_calls.jsonl").exists())
            self.assertTrue((vault / "state" / "processed_sessions.json").exists())

            session_rows = (vault / "data" / "codex_sessions.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(session_rows), 1)
            self.assertEqual(json.loads(session_rows[0])["session_id"], "019e-export")

            export_session_file(source, ExportConfig(output_root=vault, force=True))
            session_rows_after_force = (vault / "data" / "codex_sessions.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(session_rows_after_force), 1)

            skipped = export_session_file(source, ExportConfig(output_root=vault))
            self.assertFalse(skipped.exported)
            self.assertEqual(skipped.skipped_reason, "already_processed")

            with source.open("a", encoding="utf-8") as file:
                file.write(
                    json.dumps(
                        {
                            "type": "response_item",
                            "timestamp": "2026-05-27T03:00:04.000Z",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "추가 응답입니다."}],
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            refreshed = export_session_file(source, ExportConfig(output_root=vault))
            self.assertTrue(refreshed.exported)
            self.assertEqual(len((vault / "data" / "codex_sessions.jsonl").read_text(encoding="utf-8").splitlines()), 1)

            for child in sorted(tmp.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            tmp.rmdir()

    def test_redacts_common_secret_shapes(self) -> None:
        self.assertEqual(redact_secrets("Authorization: Bearer abc.def.ghi"), "Authorization: Bearer [REDACTED]")
        self.assertEqual(redact_secrets("OPENAI_API_KEY=sk-proj-abc123"), "OPENAI_API_KEY=[REDACTED]")
        self.assertEqual(redact_secrets("Slack token xoxb-123-456-secret"), "Slack token [REDACTED]")

    def test_append_live_session_file_appends_only_new_refined_rows(self) -> None:
        tmp = Path(self._testMethodName)
        tmp.mkdir(exist_ok=True)
        source = tmp / "rollout-2026-05-27T12-00-00-019e-live.jsonl"
        output_root = tmp / "vault"
        write_jsonl(
            source,
            [
                {
                    "type": "session_meta",
                    "timestamp": "2026-05-27T03:00:00.000Z",
                    "payload": {"id": "019e-live", "cwd": "/workspace/project", "timestamp": "2026-05-27T03:00:00.000Z"},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-27T03:00:02.000Z",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "첫 질문입니다."}],
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-27T03:00:03.000Z",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": "call-1",
                        "arguments": json.dumps({"cmd": "python3 -m unittest", "workdir": "/workspace/project"}, ensure_ascii=False),
                    },
                },
            ],
        )

        first = append_live_session_file(source, output_root)

        self.assertEqual(first["appended_events"], 2)
        live_doc = Path(first["markdown_path"])
        self.assertIn("첫 질문입니다.", live_doc.read_text(encoding="utf-8"))
        self.assertIn("exec_command", live_doc.read_text(encoding="utf-8"))

        with source.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    {
                        "type": "response_item",
                        "timestamp": "2026-05-27T03:00:04.000Z",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "첫 답변입니다."}],
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        second = append_live_session_file(source, output_root)

        self.assertEqual(second["appended_events"], 1)
        doc_text = live_doc.read_text(encoding="utf-8")
        self.assertEqual(doc_text.count("첫 질문입니다."), 1)
        self.assertEqual(doc_text.count("첫 답변입니다."), 1)
        self.assertEqual(len((output_root / "data" / "codex_live_events.jsonl").read_text(encoding="utf-8").splitlines()), 3)

        for child in sorted(tmp.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        tmp.rmdir()

    def test_codex_logs_never_written_inside_obsidian_vault(self) -> None:
        tmp = Path(self._testMethodName)
        tmp.mkdir(exist_ok=True)
        # Simulate a cached hook command still pointing at an iCloud Obsidian vault.
        vault_root = tmp / "iCloud~md~obsidian" / "Documents" / "DesignC" / "개발" / "agent-logs"
        source = tmp / "rollout-2026-05-27T12-00-00-019e-vault.jsonl"
        write_jsonl(
            source,
            [
                {"type": "session_meta", "timestamp": "2026-05-27T03:00:00.000Z", "payload": {"id": "019e-vault", "cwd": "/workspace/project", "timestamp": "2026-05-27T03:00:00.000Z"}},
                {"type": "response_item", "timestamp": "2026-05-27T03:00:02.000Z", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "응답"}]}},
            ],
        )
        redirected = tmp / "redirected"
        os.environ["AGENT_LOGS_CODEX_ROOT"] = str(redirected)
        try:
            result = append_live_session_file(source, vault_root)
        finally:
            del os.environ["AGENT_LOGS_CODEX_ROOT"]
        # Output must be redirected out of the vault, never written inside it.
        self.assertNotIn("iCloud~md~obsidian", result["markdown_path"])
        self.assertFalse((vault_root / "codex-logs").exists())
        self.assertTrue((redirected / "codex-logs" / "019e-vault" / "transcript.md").exists())

        for child in sorted(tmp.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        tmp.rmdir()

    def test_append_live_session_emits_per_batch_token_usage_delta(self) -> None:
        tmp = Path(self._testMethodName)
        tmp.mkdir(exist_ok=True)
        source = tmp / "rollout-2026-05-27T12-00-00-019e-usage.jsonl"
        output_root = tmp / "vault"

        def token_count(ts: str, totals: dict) -> dict:
            return {
                "type": "event_msg",
                "timestamp": ts,
                "payload": {"type": "token_count", "info": {"total_token_usage": totals, "last_token_usage": totals, "model_context_window": 258400}},
            }

        cum_a = {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20, "reasoning_output_tokens": 5, "total_tokens": 125}
        cum_b = {"input_tokens": 300, "cached_input_tokens": 140, "output_tokens": 60, "reasoning_output_tokens": 15, "total_tokens": 375}

        write_jsonl(
            source,
            [
                {"type": "session_meta", "timestamp": "2026-05-27T03:00:00.000Z", "payload": {"id": "019e-usage", "cwd": "/workspace/project", "timestamp": "2026-05-27T03:00:00.000Z"}},
                token_count("2026-05-27T03:00:01.000Z", {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 5, "reasoning_output_tokens": 1, "total_tokens": 65}),
                token_count("2026-05-27T03:00:02.000Z", cum_a),
            ],
        )

        append_live_session_file(source, output_root)
        live_doc = output_root / "codex-logs" / "019e-usage" / "transcript.md"
        text = live_doc.read_text(encoding="utf-8")
        # First batch delta == latest cumulative seen (cum_a), from a zero baseline.
        self.assertEqual(text.count("## "), 1)  # exactly one USAGE section so far
        self.assertIn("- in: `100`", text)
        self.assertIn("- cache_read: `40`", text)
        self.assertIn("- reasoning: `5`", text)
        self.assertIn("- total: `125`", text)

        # File grows with a higher cumulative; second batch must emit only the delta.
        with source.open("a", encoding="utf-8") as file:
            file.write(json.dumps(token_count("2026-05-27T03:00:03.000Z", cum_b), ensure_ascii=False) + "\n")
        append_live_session_file(source, output_root)
        text2 = live_doc.read_text(encoding="utf-8")

        import re
        totals: dict[str, int] = {}
        for block in re.findall(r"## \S+ - USAGE\n\n((?:- \w+: `\d+`\n)+)", text2):
            for key, value in re.findall(r"- (\w+): `(\d+)`", block):
                totals[key] = totals.get(key, 0) + int(value)
        # Sum of per-batch deltas equals the final cumulative (no double counting).
        self.assertEqual(totals["in"], cum_b["input_tokens"])
        self.assertEqual(totals["out"], cum_b["output_tokens"])
        self.assertEqual(totals["cache_read"], cum_b["cached_input_tokens"])
        self.assertEqual(totals["reasoning"], cum_b["reasoning_output_tokens"])
        self.assertEqual(totals["total"], cum_b["total_tokens"])

        for child in sorted(tmp.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        tmp.rmdir()

    def test_append_live_sessions_filters_stale_active_files(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            codex_home = tmp / ".codex"
            sessions = codex_home / "sessions" / "2026" / "05" / "27"
            sessions.mkdir(parents=True)
            recent = sessions / "rollout-2026-05-27T12-00-00-019e-recent.jsonl"
            stale = sessions / "rollout-2026-05-26T12-00-00-019e-stale.jsonl"
            for path, session_id in [(recent, "019e-recent"), (stale, "019e-stale")]:
                write_jsonl(
                    path,
                    [
                        {
                            "type": "session_meta",
                            "timestamp": "2026-05-27T03:00:00.000Z",
                            "payload": {"id": session_id, "cwd": "/workspace/project", "timestamp": "2026-05-27T03:00:00.000Z"},
                        },
                        {
                            "type": "response_item",
                            "timestamp": "2026-05-27T03:00:02.000Z",
                            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": session_id}]},
                        },
                    ],
                )
            stale_time = time.time() - 48 * 60 * 60
            os.utime(stale, (stale_time, stale_time))

            oversize = sessions / "rollout-2026-05-27T13-00-00-019e-oversize.jsonl"
            write_jsonl(
                oversize,
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-05-27T03:00:00.000Z",
                        "payload": {"id": "019e-oversize", "cwd": "/workspace/project", "timestamp": "2026-05-27T03:00:00.000Z"},
                    }
                ],
            )
            with oversize.open("a", encoding="utf-8") as file:
                file.write(" " * 1024)

            results = append_live_sessions(codex_home, tmp / "vault", active_within_hours=24, max_active_mb=0.0005)

            self.assertEqual([result["session_id"] for result in results], ["019e-recent"])

    def test_parse_complete_jsonl_rows_preserves_partial_trailing_line(self) -> None:
        complete = json.dumps({"type": "session_meta", "payload": {"id": "019e-partial"}}, ensure_ascii=False)
        partial = '{"type":"response_item"'
        rows, new_offset = parse_complete_jsonl_rows((complete + "\n" + partial).encode("utf-8"), 10)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "session_meta")
        self.assertEqual(new_offset, 10 + len(complete.encode("utf-8")) + 1)

    def test_append_from_hook_input_uses_transcript_path_without_scanning(self) -> None:
        tmp = Path(self._testMethodName)
        tmp.mkdir(exist_ok=True)
        source = tmp / "rollout-2026-05-27T12-00-00-019e-hook.jsonl"
        output_root = tmp / "vault"
        hook_log = tmp / "hook.log.jsonl"
        write_jsonl(
            source,
            [
                {
                    "type": "session_meta",
                    "timestamp": "2026-05-27T03:00:00.000Z",
                    "payload": {"id": "019e-hook", "cwd": "/workspace/project", "timestamp": "2026-05-27T03:00:00.000Z"},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-27T03:00:02.000Z",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hook 질문"}]},
                },
            ],
        )

        result = append_from_hook_input(
            {
                "hook_event_name": "Stop",
                "session_id": "019e-hook",
                "turn_id": "turn-1",
                "transcript_path": str(source),
            },
            output_root,
            hook_log,
        )

        self.assertTrue(result["appended"])
        self.assertEqual(result["appended_events"], 1)
        self.assertIn("hook 질문", Path(result["markdown_path"]).read_text(encoding="utf-8"))
        self.assertIn('"event_name": "Stop"', hook_log.read_text(encoding="utf-8"))

        for child in sorted(tmp.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        tmp.rmdir()

    def test_automation_prompt_is_not_misclassified_as_bugfix(self) -> None:
        tmp = Path(self._testMethodName)
        tmp.mkdir(exist_ok=True)
        session_path = tmp / "rollout-2026-05-27T12-00-00-019e-automation.jsonl"
        write_jsonl(
            session_path,
            [
                {
                    "type": "session_meta",
                    "timestamp": "2026-05-27T03:00:00.000Z",
                    "payload": {"id": "019e-automation", "cwd": "/workspace/project", "timestamp": "2026-05-27T03:00:00.000Z"},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-27T03:00:02.000Z",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Automation: Slack digest rerun. Perform cross-check verification and fix discrepancies before posting.",
                            }
                        ],
                    },
                },
            ],
        )

        parsed = parse_session_file(session_path)

        self.assertEqual(parsed.session["task_type"], "automation")

        for child in tmp.iterdir():
            child.unlink()
        tmp.rmdir()

    def test_explicit_automation_prompt_is_not_misclassified_as_code_review(self) -> None:
        tmp = Path(self._testMethodName)
        tmp.mkdir(exist_ok=True)
        session_path = tmp / "rollout-2026-05-27T12-00-00-019e-auto-review-word.jsonl"
        write_jsonl(
            session_path,
            [
                {
                    "type": "session_meta",
                    "timestamp": "2026-05-27T03:00:00.000Z",
                    "payload": {"id": "019e-auto-review-word", "cwd": "/workspace/project", "timestamp": "2026-05-27T03:00:00.000Z"},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-27T03:00:02.000Z",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Automation: daily Slack news digest\nAutomation ID: daily-slack-news-digest\nInclude Codex UI review perspective if relevant.",
                            }
                        ],
                    },
                },
            ],
        )

        parsed = parse_session_file(session_path)

        self.assertEqual(parsed.session["task_type"], "automation")

        for child in tmp.iterdir():
            child.unlink()
        tmp.rmdir()


if __name__ == "__main__":
    unittest.main()

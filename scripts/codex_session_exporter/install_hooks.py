from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any

from codex_session_exporter.exporter import DEFAULT_CODEX_HOME
from codex_session_exporter.install_launch_agent import (
    DEFAULT_LABEL,
    DEFAULT_LAUNCHD_OUTPUT_ROOT,
    LEGACY_LABELS,
    choose_python_path,
    default_obsidian_link_path,
    ensure_obsidian_symlink,
    uninstall_launch_agent,
)


DEFAULT_HOOK_EVENTS = ("UserPromptSubmit", "PostToolUse", "Stop")


def _default_claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


DEFAULT_CLAUDE_OUTPUT_ROOT = _default_claude_config_dir() / "agent-conversation-logger" / "output"
DEFAULT_CLAUDE_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop")


def default_claude_obsidian_link_path() -> Path | None:
    explicit = os.environ.get("CLAUDE_LOGGER_OBSIDIAN_LINK")
    if explicit:
        return Path(explicit).expanduser()
    vault = os.environ.get("OBSIDIAN_VAULT") or os.environ.get("OBSIDIAN_VAULT_PATH")
    if vault:
        return Path(vault).expanduser() / "agent-logs" / "claude-logs"
    icloud = Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents"
    if not icloud.exists():
        return None
    designc = icloud / "DesignC"
    if designc.exists():
        return designc / "개발" / "agent-logs" / "claude-logs"
    vaults = [p for p in icloud.iterdir() if p.is_dir()]
    if len(vaults) == 1:
        return vaults[0] / "agent-logs" / "claude-logs"
    return None


def hook_command(*, python_path: Path, exporter_path: Path, codex_home: Path, output_root: Path, hook_log: Path) -> str:
    return " ".join(
        shlex.quote(str(part))
        for part in [
            python_path,
            exporter_path,
            "--from-hook-stdin",
            "--codex-home",
            codex_home,
            "--output-root",
            output_root,
            "--hook-log",
            hook_log,
        ]
    )


def install_hooks(
    *,
    codex_home: Path = DEFAULT_CODEX_HOME,
    output_root: Path = DEFAULT_LAUNCHD_OUTPUT_ROOT,
    python_path: Path | None = None,
    obsidian_link: Path | None = None,
    create_obsidian_link: bool = True,
    events: tuple[str, ...] = DEFAULT_HOOK_EVENTS,
    remove_launch_agent: bool = True,
    trust: bool = True,
    install_claude: bool = True,
    claude_config_dir: Path | None = None,
    claude_output_root: Path = DEFAULT_CLAUDE_OUTPUT_ROOT,
    claude_obsidian_link: Path | None = None,
    create_claude_obsidian_link: bool = True,
    claude_events: tuple[str, ...] = DEFAULT_CLAUDE_HOOK_EVENTS,
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    source_exporter_path = repo_root / "codex_session_exporter" / "exporter.py"
    runtime_dir = codex_home / "codex-session-exporter"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    exporter_path = runtime_dir / "exporter.py"
    shutil.copy2(source_exporter_path, exporter_path)
    resolved_obsidian_link = None
    if create_obsidian_link:
        resolved_obsidian_link = obsidian_link if obsidian_link is not None else default_obsidian_link_path()
    obsidian_backup = None
    if resolved_obsidian_link is not None:
        codex_live_dir = output_root / "codex"
        codex_live_dir.mkdir(parents=True, exist_ok=True)
        obsidian_backup = ensure_obsidian_symlink(resolved_obsidian_link, codex_live_dir)
    resolved_python_path = python_path or choose_python_path()

    command = hook_command(
        python_path=resolved_python_path,
        exporter_path=exporter_path,
        codex_home=codex_home,
        output_root=output_root,
        hook_log=runtime_dir / "hook.log.jsonl",
    )
    hooks_path = codex_home / "hooks.json"
    backup_path = update_hooks_json(hooks_path, command, events)

    trust_result = None
    if trust:
        try:
            trust_result = trust_installed_hooks(command, hooks_path, cwd=repo_root)
        except Exception as exc:
            trust_result = {"trusted": False, "reason": "trust_failed", "error": str(exc)}

    removed_launch_agents = []
    if remove_launch_agent:
        for label in (DEFAULT_LABEL, *LEGACY_LABELS):
            removed_launch_agents.append(str(uninstall_launch_agent(label=label)))

    claude_result: dict[str, Any] | None = None
    if install_claude:
        claude_result = install_claude_code_hooks(
            claude_config_dir=claude_config_dir or _default_claude_config_dir(),
            output_root=claude_output_root,
            python_path=resolved_python_path,
            obsidian_link=claude_obsidian_link,
            create_obsidian_link=create_claude_obsidian_link,
            events=claude_events,
        )

    return {
        "hooks_path": str(hooks_path),
        "hooks_backup_path": str(backup_path) if backup_path else None,
        "command": command,
        "events": list(events),
        "output_root": str(output_root),
        "obsidian_link": str(resolved_obsidian_link) if resolved_obsidian_link else None,
        "obsidian_backup_path": str(obsidian_backup) if obsidian_backup else None,
        "removed_launch_agents": removed_launch_agents,
        "trust_result": trust_result,
        "claude": claude_result,
    }


def claude_hook_command(*, python_path: Path, logger_path: Path, output_root: Path, hook_log: Path) -> str:
    return " ".join(
        shlex.quote(str(part))
        for part in [
            python_path,
            logger_path,
            "--from-hook-stdin",
            "--output-root",
            output_root,
            "--hook-log",
            hook_log,
        ]
    )


def claude_hook_group(command: str, event: str) -> dict[str, Any]:
    return {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": 10 if event == "SessionStart" else 20,
                "statusMessage": f"Logging Claude Code {event}",
            }
        ]
    }


def remove_existing_claude_logger_hooks(groups: list[Any]) -> list[Any]:
    cleaned = []
    for group in groups:
        if not isinstance(group, dict):
            cleaned.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            cleaned.append(group)
            continue
        filtered = [
            handler
            for handler in handlers
            if "claude_logger.py" not in str(handler.get("command") if isinstance(handler, dict) else "")
        ]
        if filtered:
            copied = dict(group)
            copied["hooks"] = filtered
            cleaned.append(copied)
    return cleaned


def update_claude_settings_json(settings_path: Path, command: str, events: tuple[str, ...]) -> Path | None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if settings_path.exists():
        try:
            config = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup_path = backup_file(settings_path)
            config = {}
    else:
        config = {}
    if not isinstance(config, dict):
        backup_path = backup_file(settings_path)
        config = {}
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        backup_path = backup_file(settings_path)
        hooks = {}
        config["hooks"] = hooks
    for event in events:
        existing = hooks.get(event, [])
        if not isinstance(existing, list):
            existing = []
        hooks[event] = remove_existing_claude_logger_hooks(existing) + [claude_hook_group(command, event)]
    settings_path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return backup_path


def install_claude_code_hooks(
    *,
    claude_config_dir: Path,
    output_root: Path,
    python_path: Path,
    obsidian_link: Path | None,
    create_obsidian_link: bool,
    events: tuple[str, ...],
) -> dict[str, Any]:
    runtime_dir = claude_config_dir / "agent-conversation-logger"
    logger_path = runtime_dir / "claude_logger.py"
    repo_root = Path(__file__).resolve().parents[1]
    source_logger = repo_root / "claude_logger.py"
    if source_logger.exists():
        runtime_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_logger, logger_path)
    if not logger_path.exists():
        return {
            "installed": False,
            "reason": "claude_logger_missing",
            "expected_logger_path": str(logger_path),
        }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "claude-code").mkdir(parents=True, exist_ok=True)
    (output_root / "data").mkdir(parents=True, exist_ok=True)
    (output_root / "state").mkdir(parents=True, exist_ok=True)

    hook_log = runtime_dir / "hook.log.jsonl"
    command = claude_hook_command(
        python_path=python_path,
        logger_path=logger_path,
        output_root=output_root,
        hook_log=hook_log,
    )
    settings_path = claude_config_dir / "settings.json"
    settings_backup = update_claude_settings_json(settings_path, command, events)

    resolved_link = None
    if create_obsidian_link:
        resolved_link = obsidian_link if obsidian_link is not None else default_claude_obsidian_link_path()
    obsidian_backup = None
    if resolved_link is not None:
        claude_live_dir = output_root / "claude-code"
        claude_live_dir.mkdir(parents=True, exist_ok=True)
        obsidian_backup = ensure_obsidian_symlink(resolved_link, claude_live_dir)

    return {
        "installed": True,
        "settings_path": str(settings_path),
        "settings_backup_path": str(settings_backup) if settings_backup else None,
        "logger_path": str(logger_path),
        "command": command,
        "events": list(events),
        "output_root": str(output_root),
        "obsidian_link": str(resolved_link) if resolved_link else None,
        "obsidian_backup_path": str(obsidian_backup) if obsidian_backup else None,
    }


def update_hooks_json(hooks_path: Path, command: str, events: tuple[str, ...]) -> Path | None:
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if hooks_path.exists():
        try:
            config = json.loads(hooks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup_path = backup_file(hooks_path)
            config = {}
    else:
        config = {}

    if not isinstance(config, dict):
        backup_path = backup_file(hooks_path)
        config = {}
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        backup_path = backup_file(hooks_path)
        hooks = {}
        config["hooks"] = hooks

    for event in events:
        existing_groups = hooks.get(event, [])
        if not isinstance(existing_groups, list):
            existing_groups = []
        hooks[event] = remove_existing_exporter_hooks(existing_groups) + [hook_group(command, event)]

    hooks_path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return backup_path


def backup_file(path: Path) -> Path:
    backup_path = path.with_name(f"{path.name}.backup-{datetime.now().strftime('%Y%m%dT%H%M%S')}")
    shutil.copy2(path, backup_path)
    return backup_path


def remove_existing_exporter_hooks(groups: list[Any]) -> list[Any]:
    cleaned = []
    for group in groups:
        if not isinstance(group, dict):
            cleaned.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            cleaned.append(group)
            continue
        filtered_handlers = [
            handler
            for handler in handlers
            if "/codex-session-exporter/exporter.py" not in str(handler.get("command") if isinstance(handler, dict) else "")
            and " --from-hook-stdin" not in str(handler.get("command") if isinstance(handler, dict) else "")
        ]
        if filtered_handlers:
            copied = dict(group)
            copied["hooks"] = filtered_handlers
            cleaned.append(copied)
    return cleaned


def hook_group(command: str, event: str) -> dict[str, Any]:
    return {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": 20,
                "statusMessage": f"Logging Codex {event}",
            }
        ]
    }


def trust_installed_hooks(command: str, hooks_path: Path, cwd: Path) -> dict[str, Any]:
    response = app_server_request_sequence(
        [
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "hooks/list",
                "params": {"cwds": [str(cwd)]},
            }
        ]
    )[2]
    hooks = response["result"]["data"][0]["hooks"]
    matching = [
        hook
        for hook in hooks
        if hook.get("command") == command and Path(str(hook.get("sourcePath"))) == hooks_path
    ]
    if not matching:
        return {"trusted": False, "reason": "installed_hooks_not_listed", "listed_hooks": hooks}

    state_value = {hook["key"]: {"trusted_hash": hook["currentHash"]} for hook in matching}
    write_response = app_server_request_sequence(
        [
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "config/batchWrite",
                "params": {
                    "edits": [
                        {
                            "keyPath": "hooks.state",
                            "value": state_value,
                            "mergeStrategy": "upsert",
                        }
                    ],
                    "filePath": None,
                    "expectedVersion": None,
                    "reloadUserConfig": True,
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "hooks/list",
                "params": {"cwds": [str(cwd)]},
            },
        ]
    )
    verified_hooks = write_response[3]["result"]["data"][0]["hooks"]
    trusted = [
        hook
        for hook in verified_hooks
        if hook.get("command") == command and Path(str(hook.get("sourcePath"))) == hooks_path
    ]
    return {
        "trusted": all(hook.get("trustStatus") == "trusted" for hook in trusted),
        "trusted_count": sum(1 for hook in trusted if hook.get("trustStatus") == "trusted"),
        "installed_count": len(matching),
        "write_response": write_response[2].get("result"),
        "hooks": [
            {
                "key": hook.get("key"),
                "event_name": hook.get("eventName"),
                "trust_status": hook.get("trustStatus"),
            }
            for hook in trusted
        ],
    }


def app_server_request_sequence(requests: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    process = subprocess.Popen(
        ["codex", "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "codex-session-exporter-installer", "title": "Codex Session Exporter Installer", "version": "1.0.0"},
            "capabilities": {"experimentalApi": True},
        },
    }
    messages = [initialize, {"jsonrpc": "2.0", "method": "initialized"}, *requests]
    for message in messages:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    wanted_ids = {message["id"] for message in messages if "id" in message}
    responses: dict[int, dict[str, Any]] = {}
    try:
        while not wanted_ids <= responses.keys():
            line = process.stdout.readline()
            if not line:
                break
            message = json.loads(line)
            if "id" in message:
                responses[int(message["id"])] = message
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
    if not wanted_ids <= responses.keys():
        stderr = process.stderr.read() if process.stderr else ""
        raise RuntimeError(f"app-server did not return expected responses: {responses}; stderr={stderr}")
    return responses


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install Codex and Claude Code lifecycle hooks for conversation logging.")
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_LAUNCHD_OUTPUT_ROOT)
    parser.add_argument("--python", dest="python_path", type=Path, default=None)
    parser.add_argument("--obsidian-link", type=Path, default=None)
    parser.add_argument("--no-obsidian-link", action="store_true")
    parser.add_argument("--no-remove-launch-agent", action="store_true")
    parser.add_argument("--no-trust", action="store_true")
    parser.add_argument("--no-claude", action="store_true", help="Skip Claude Code logger installation.")
    parser.add_argument("--claude-config-dir", type=Path, default=None)
    parser.add_argument("--claude-output-root", type=Path, default=DEFAULT_CLAUDE_OUTPUT_ROOT)
    parser.add_argument("--claude-obsidian-link", type=Path, default=None)
    parser.add_argument("--no-claude-obsidian-link", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = install_hooks(
        codex_home=args.codex_home,
        output_root=args.output_root,
        python_path=args.python_path,
        obsidian_link=None if args.no_obsidian_link else args.obsidian_link,
        create_obsidian_link=not args.no_obsidian_link,
        remove_launch_agent=not args.no_remove_launch_agent,
        trust=not args.no_trust,
        install_claude=not args.no_claude,
        claude_config_dir=args.claude_config_dir,
        claude_output_root=args.claude_output_root,
        claude_obsidian_link=None if args.no_claude_obsidian_link else args.claude_obsidian_link,
        create_claude_obsidian_link=not args.no_claude_obsidian_link,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

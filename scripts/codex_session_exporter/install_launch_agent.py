from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from typing import Any

from codex_session_exporter.exporter import DEFAULT_CODEX_HOME


DEFAULT_LABEL = "com.codex-session-exporter"
LEGACY_LABELS = ("com.youjungmin.codex-session-exporter",)
DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_SCAN_LIMIT = 50
DEFAULT_ACTIVE_WITHIN_HOURS = 24
DEFAULT_MAX_ACTIVE_MB = 10
DEFAULT_LAUNCHD_OUTPUT_ROOT = DEFAULT_CODEX_HOME / "codex-session-exporter" / "obsidian-output"


def default_obsidian_link_path() -> Path | None:
    explicit = os.environ.get("CODEX_SESSION_EXPORTER_OBSIDIAN_LINK") or os.environ.get("OBSIDIAN_CODEX_LOG_LINK")
    if explicit:
        return Path(explicit).expanduser()

    vault = os.environ.get("OBSIDIAN_VAULT") or os.environ.get("OBSIDIAN_VAULT_PATH")
    if vault:
        return Path(vault).expanduser() / "agent-logs" / "codex-logs"

    icloud_obsidian = Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents"
    if not icloud_obsidian.exists():
        return None

    designc = icloud_obsidian / "DesignC"
    if designc.exists():
        return designc / "개발" / "agent-logs" / "codex-logs"

    vaults = [path for path in icloud_obsidian.iterdir() if path.is_dir()]
    if len(vaults) == 1:
        return vaults[0] / "agent-logs" / "codex-logs"
    return None


def build_launch_agent_plist(
    *,
    label: str,
    python_path: Path,
    exporter_path: Path,
    codex_home: Path,
    output_root: Path,
    interval_seconds: int,
    stdout_path: Path,
    stderr_path: Path,
    working_directory: Path,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
    include_active: bool = False,
    active_within_hours: float = DEFAULT_ACTIVE_WITHIN_HOURS,
    max_active_mb: float = DEFAULT_MAX_ACTIVE_MB,
) -> dict[str, Any]:
    clean_path = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    home = codex_home.parent
    user = home.name
    program_arguments = [
        "/usr/bin/env",
        "-i",
        f"PATH={clean_path}",
        f"HOME={home}",
        f"USER={user}",
        str(python_path),
        str(exporter_path),
        "--codex-home",
        str(codex_home),
        "--output-root",
        str(output_root),
    ]
    if include_active:
        program_arguments.extend(
            [
                "--append-live",
                "--active-within-hours",
                format_number(active_within_hours),
                "--max-active-mb",
                format_number(max_active_mb),
            ]
        )
    program_arguments.extend(["--limit", str(scan_limit)])
    return {
        "Label": label,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(working_directory),
        "RunAtLoad": True,
        "StartInterval": interval_seconds,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "EnvironmentVariables": {
            "PATH": clean_path,
        },
    }


def install_launch_agent(
    *,
    label: str = DEFAULT_LABEL,
    python_path: Path | None = None,
    codex_home: Path = DEFAULT_CODEX_HOME,
    output_root: Path | None = None,
    obsidian_link_path: Path | None = None,
    create_obsidian_link: bool = True,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
    active_within_hours: float = DEFAULT_ACTIVE_WITHIN_HOURS,
    max_active_mb: float = DEFAULT_MAX_ACTIVE_MB,
    include_active: bool = True,
    load: bool = True,
) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent polling is only available on macOS. Use lifecycle hooks instead.")

    repo_root = Path(__file__).resolve().parents[1]
    source_exporter_path = repo_root / "codex_session_exporter" / "exporter.py"
    runtime_dir = codex_home / "codex-session-exporter"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    exporter_path = runtime_dir / "exporter.py"
    shutil.copy2(source_exporter_path, exporter_path)
    resolved_python = python_path or choose_python_path()
    resolved_output_root = output_root or DEFAULT_LAUNCHD_OUTPUT_ROOT
    resolved_obsidian_link = None
    if create_obsidian_link:
        resolved_obsidian_link = obsidian_link_path if obsidian_link_path is not None else default_obsidian_link_path()
    if resolved_obsidian_link is not None:
        ensure_obsidian_symlink(resolved_obsidian_link, resolved_output_root)
    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    launch_agents_dir.mkdir(parents=True, exist_ok=True)
    codex_home.mkdir(parents=True, exist_ok=True)

    plist_path = launch_agents_dir / f"{label}.plist"
    plist = build_launch_agent_plist(
        label=label,
        python_path=resolved_python,
        exporter_path=exporter_path,
        codex_home=codex_home,
        output_root=resolved_output_root,
        interval_seconds=interval_seconds,
        stdout_path=codex_home / "codex-session-exporter.launchd.out.log",
        stderr_path=codex_home / "codex-session-exporter.launchd.err.log",
        working_directory=runtime_dir,
        scan_limit=scan_limit,
        include_active=include_active,
        active_within_hours=active_within_hours,
        max_active_mb=max_active_mb,
    )
    with plist_path.open("wb") as file:
        plistlib.dump(plist, file, sort_keys=False)

    if load:
        reload_launch_agent(plist_path, label)
    return plist_path


def ensure_obsidian_symlink(link_path: Path, target_path: Path, timestamp: str | None = None) -> Path | None:
    target_path.mkdir(parents=True, exist_ok=True)
    target_path = target_path.resolve()
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink():
        if link_path.resolve() == target_path.resolve():
            return None
        link_path.unlink()
    backup_path = None
    if link_path.exists():
        shutil.copytree(link_path, target_path, dirs_exist_ok=True)
        suffix = timestamp or datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_path = link_path.with_name(f"{link_path.name}.backup-{suffix}")
        if backup_path.exists():
            shutil.rmtree(backup_path)
        link_path.rename(backup_path)
    link_path.symlink_to(target_path, target_is_directory=True)
    return backup_path


def choose_python_path(
    *,
    candidates: list[Path] | None = None,
    exists: Any | None = None,
    fallback: Path | None = None,
) -> Path:
    candidate_paths = candidates or [
        Path("/usr/bin/python3"),
        Path("/opt/homebrew/bin/python3"),
        Path("/usr/local/bin/python3"),
    ]
    path_exists = exists or Path.exists
    for candidate in candidate_paths:
        if path_exists(candidate):
            return candidate
    return fallback or Path(shutil.which("python3") or sys.executable)


def format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def uninstall_launch_agent(label: str = DEFAULT_LABEL) -> Path:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    bootout_launch_agent(plist_path)
    if plist_path.exists():
        plist_path.unlink()
    return plist_path


def reload_launch_agent(plist_path: Path, label: str) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent polling is only available on macOS. Use lifecycle hooks instead.")
    bootout_launch_agent(plist_path)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    subprocess.run(["launchctl", "enable", f"{domain}/{label}"], check=False)
    subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{label}"], check=False)


def bootout_launch_agent(plist_path: Path) -> None:
    if sys.platform != "darwin":
        return
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist_path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install or uninstall the Codex session exporter LaunchAgent.")
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--python", dest="python_path", type=Path, default=None)
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--obsidian-link", type=Path, default=None)
    parser.add_argument("--no-obsidian-link", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--scan-limit", type=int, default=DEFAULT_SCAN_LIMIT)
    parser.add_argument("--active-within-hours", type=float, default=DEFAULT_ACTIVE_WITHIN_HOURS)
    parser.add_argument("--max-active-mb", type=float, default=DEFAULT_MAX_ACTIVE_MB)
    parser.add_argument("--no-include-active", action="store_true", help="Only scan archived sessions.")
    parser.add_argument("--no-load", action="store_true", help="Write the plist without loading it into launchd.")
    parser.add_argument("--uninstall", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.uninstall:
        plist_path = uninstall_launch_agent(label=args.label)
        print(f"uninstalled {plist_path}")
        return 0
    plist_path = install_launch_agent(
        label=args.label,
        python_path=args.python_path,
        codex_home=args.codex_home,
        output_root=args.output_root,
        obsidian_link_path=None if args.no_obsidian_link else args.obsidian_link,
        create_obsidian_link=not args.no_obsidian_link,
        interval_seconds=args.interval_seconds,
        scan_limit=args.scan_limit,
        active_within_hours=args.active_within_hours,
        max_active_mb=args.max_active_mb,
        include_active=not args.no_include_active,
        load=not args.no_load,
    )
    print(f"installed {plist_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

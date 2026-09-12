"""Spawn command — the subagent bridge for harnesses without a subagent tool.

Claude Code and OMP spawn subagents in-session (`Task` / `task`). Pi has no
such tool, so the pipeline's parallelism runs through here: each agent becomes
a `pi -p` child process whose system prompt is the installed agent file. One
`--batch` call fans a whole wave out concurrently, which is what the step
skills instruct the orchestrator to do on those harnesses.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer

from hyperresearch.cli._output import console, output
from hyperresearch.core.harnesses import Harness, HarnessError, get_harness
from hyperresearch.models.output import error, success

# The child CLI per bridge harness. Overridable with HPR_<ID>_BIN (e.g.
# HPR_PI_BIN=/opt/pi/bin/pi) for non-PATH installs.
_BRIDGE_BINARIES: dict[str, str] = {"pi": "pi"}


class SpawnError(Exception):
    """A spawn could not be prepared (unknown agent, missing child CLI, ...)."""


def _split_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Split an installed agent file into its frontmatter and system prompt.

    Deliberately minimal: agent frontmatter is flat `key: value` lines plus
    folded `description: >` blocks, and the only keys this bridge reads are
    `tools` and `model`. `core/frontmatter.py` parses vault notes into a
    NoteMeta and cannot be reused here.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content

    meta: dict[str, str] = {}
    body_start = len(lines)
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            body_start = index + 1
            break
        if line.startswith((" ", "\t")) or ":" not in line:
            continue  # continuation line of a folded block
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()

    body = "\n".join(lines[body_start:]).lstrip("\n")
    if content.endswith("\n") and body and not body.endswith("\n"):
        body += "\n"
    return meta, body


def _resolve_agent_file(agent: str, root: Path, harness: Harness) -> Path:
    """The installed agent file: project dir first, then the user-level dir."""
    candidates = [
        harness.agents_dir(root) / f"{agent}.md",
        harness.global_agents_dir() / f"{agent}.md",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    installed = sorted(
        {
            path.stem
            for directory in (harness.agents_dir(root), harness.global_agents_dir())
            if directory.is_dir()
            for path in directory.glob("*.md")
        }
    )
    known = ", ".join(installed) if installed else "none installed"
    raise SpawnError(
        f"unknown agent '{agent}' for harness '{harness.id}' (available: {known}). "
        f"Run `hyperresearch install {root} --harness {harness.id}` first."
    )


def _resolve_binary(harness: Harness) -> str:
    """The child CLI for a bridge harness."""
    name = _BRIDGE_BINARIES[harness.id]
    override = os.environ.get(f"HPR_{harness.id.upper()}_BIN")
    if override:
        return override
    found = shutil.which(name)
    if not found:
        raise SpawnError(
            f"the '{name}' CLI is not on PATH — install {harness.label} or set "
            f"HPR_{harness.id.upper()}_BIN to its executable."
        )
    return found


def _bridge_harness(harness_id: str | None, root: Path) -> Harness:
    """Resolve the harness to bridge through, rejecting native-spawn harnesses."""
    if harness_id:
        harness = get_harness(harness_id)
    else:
        # Prefer a bridge harness that actually has agents installed here.
        installed = [
            candidate
            for candidate in (get_harness(hid) for hid in _BRIDGE_BINARIES)
            if candidate.agents_dir(root).is_dir()
        ]
        harness = installed[0] if installed else get_harness("pi")

    if harness.spawn_mode != "cli-bridge":
        raise SpawnError(
            f"{harness.label} spawns subagents natively (the "
            f"`{harness.tool('task')}` tool) — no bridge needed. Use that tool "
            "instead of `hyperresearch spawn`."
        )
    if harness.id not in _BRIDGE_BINARIES:
        raise SpawnError(f"no child CLI is known for harness '{harness.id}'")
    return harness


def _build_argv(
    binary: str,
    prompt_path: Path,
    task: str,
    meta: dict[str, str],
    model: str | None,
) -> list[str]:
    """The child invocation: system prompt from file, task as the message."""
    argv = [binary, "-p", "--no-session", "--append-system-prompt", str(prompt_path)]

    tools = meta.get("tools", "").strip()
    if tools:
        argv += ["--tools", ",".join(part.strip() for part in tools.split(",") if part.strip())]

    selected = model or meta.get("model", "").strip().strip('"')
    if selected:
        argv += ["--model", selected]

    argv += ["--", task]
    return argv


def _run_one(
    agent: str,
    task: str,
    root: Path,
    harness: Harness,
    binary: str,
    model: str | None,
    timeout: float,
) -> dict:
    """Run one agent as a child process and return its result record."""
    agent_file = _resolve_agent_file(agent, root, harness)
    meta, system_prompt = _split_frontmatter(agent_file.read_text(encoding="utf-8"))

    tmp_dir = Path(tempfile.mkdtemp(prefix="hpr-spawn-"))
    prompt_path = tmp_dir / f"{agent}.md"
    prompt_path.write_text(system_prompt, encoding="utf-8")

    started = time.monotonic()
    try:
        argv = _build_argv(binary, prompt_path, task, meta, model)
        try:
            proc = subprocess.run(
                argv,
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = f"timed out after {timeout:.0f}s"
            code = 124
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        "agent": agent,
        "exit_code": code,
        "output": (stdout or "").strip(),
        "stderr": (stderr or "").strip()[-2000:],
        "duration_s": round(time.monotonic() - started, 1),
    }


def _load_batch(batch_path: Path, root: Path) -> list[tuple[str, str]]:
    """Read a wave file into (agent, task) pairs."""
    try:
        items = json.loads(batch_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpawnError(f"cannot read batch file {batch_path}: {exc}") from None
    if not isinstance(items, list) or not items:
        raise SpawnError("batch file must be a non-empty JSON array of spawn items")

    pairs: list[tuple[str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("agent"):
            raise SpawnError(f"batch item {index} needs an 'agent' field")
        agent = str(item["agent"])
        if item.get("prompt_file"):
            prompt_file = Path(item["prompt_file"])
            if not prompt_file.is_absolute():
                prompt_file = root / prompt_file
            try:
                task = prompt_file.read_text(encoding="utf-8")
            except OSError as exc:
                raise SpawnError(f"batch item {index}: {exc}") from None
        elif item.get("prompt"):
            task = str(item["prompt"])
        else:
            raise SpawnError(f"batch item {index} needs 'prompt' or 'prompt_file'")
        pairs.append((agent, task))
    return pairs


def spawn(
    agent: str | None = typer.Argument(
        None, help="Installed agent to run, e.g. hyperresearch-fetcher"
    ),
    prompt: str | None = typer.Option(None, "--prompt", help="Spawn prompt text"),
    prompt_file: str | None = typer.Option(
        None, "--prompt-file", help="File holding the spawn prompt"
    ),
    batch: str | None = typer.Option(
        None,
        "--batch",
        help='JSON array of spawn items — [{"agent": "...", "prompt_file": "..."}, ...] — run concurrently',
    ),
    concurrency: int = typer.Option(4, "--concurrency", help="Max child processes in flight"),
    harness: str | None = typer.Option(
        None, "--harness", "-H", help="Bridge harness (default: pi)"
    ),
    path: str = typer.Option(
        ".", "--path", help="Project root the children run in (vault + agent files live here)"
    ),
    model: str | None = typer.Option(
        None, "--model", help="Override the child model selector for every spawn"
    ),
    timeout: float = typer.Option(3600, "--timeout", help="Per-agent timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", "-j", help="JSON output"),
) -> None:
    """Run installed subagents as child processes (Pi and other bridge harnesses)."""
    root = Path(path).resolve()

    try:
        target = _bridge_harness(harness, root)
        binary = _resolve_binary(target)

        if batch:
            if agent or prompt or prompt_file:
                raise SpawnError("--batch cannot be combined with a single-agent spawn")
            pairs = _load_batch(Path(batch), root)
        else:
            if not agent:
                raise SpawnError("name an agent, or pass --batch <file.json>")
            if prompt and prompt_file:
                raise SpawnError("pass either --prompt or --prompt-file, not both")
            if prompt_file:
                file_path = Path(prompt_file)
                if not file_path.is_absolute():
                    file_path = root / file_path
                try:
                    task = file_path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise SpawnError(str(exc)) from None
            elif prompt:
                task = prompt
            else:
                raise SpawnError("pass --prompt or --prompt-file")
            pairs = [(agent, task)]
    except (SpawnError, HarnessError) as exc:
        if json_output:
            output(error(str(exc), "SPAWN_ERROR"), json_mode=True)
        else:
            console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)

    workers = max(1, min(concurrency, len(pairs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                lambda pair: _run_one(
                    pair[0], pair[1], root, target, binary, model, timeout
                ),
                pairs,
            )
        )

    ok = all(item["exit_code"] == 0 for item in results)

    if json_output:
        output(
            success({"harness": target.id, "ok": ok, "agents": results}, count=len(results)),
            json_mode=True,
        )
    else:
        for item in results:
            status = "[green]ok[/]" if item["exit_code"] == 0 else "[red]failed[/]"
            console.print(
                f"[bold]{item['agent']}[/] {status} "
                f"[dim]({item['duration_s']}s, exit {item['exit_code']})[/]"
            )
            if item["output"]:
                console.print(item["output"])
            if item["exit_code"] != 0 and item["stderr"]:
                console.print(f"[red]{item['stderr']}[/]")

    if not ok:
        raise typer.Exit(1)

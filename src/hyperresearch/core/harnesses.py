"""Harness adapters — the coding agents hyperresearch installs into.

hyperresearch ships ONE pipeline (18 step skills + 19 subagent prompts) and
installs it into whichever agent harness the user actually runs:

    claude  Claude Code   `.claude/`      native Task subagents, `Skill` tool
    omp     Oh My Pi      `.omp/`         native `task` subagents, `skill://`
    pi      Pi            `.pi/`          no subagent tool — `hpr spawn` bridge

Only four things differ between harnesses, and all four reach the prompts:

  1. install paths — skills dir, agents dir, user-level dir, context file
  2. tool names — `Bash` vs `bash`, `WebSearch` vs `web_search`, `Glob` vs `find`
  3. how a step skill is loaded — `Skill(...)` / `skill://` / a plain file read
  4. how a subagent is spawned — Task tool / task tool / `hpr spawn` CLI bridge

Everything else (procedures, numbers, invariants) is shared and rendered from
the same templates. **Claude Code is the reference rendering**: with the
`claude` harness every template must render byte-identically to the
pre-harness prompts — the golden prompt tests pin exactly that.

A harness object is exposed to templates as `h`:

    tools: << h.tools("bash", "read", "write") >>     -> "Bash, Read, Write"
    << h.model_line(p.models.fetcher) >>              -> "model: sonnet"
    << h.load_skill("hyperresearch-2-width-sweep") >> -> 'Skill(skill: "...")'
    << h.spawn_key >>: hyperresearch-fetcher          -> "subagent_type: ..."
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

# Canonical tool vocabulary used inside the shipped prompts. Each harness maps
# these onto its own tool names; `None` means "this harness has no such tool"
# and the entry is dropped from a rendered `tools:` list.
CANONICAL_TOOLS: tuple[str, ...] = (
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "task",
    "web_search",
    "skill",
    "todo",
    # Claude Code's on-demand MCP tool loader (the Chrome lane needs it).
    "tool_search",
    # Real-browser control surface. On omp that is the `eval` tool (its
    # prelude exposes `browser`); Claude Code reaches Chrome through
    # tool_search + the claude-in-chrome MCP tools instead.
    "browser",
)


class HarnessError(Exception):
    """Unknown or unusable harness id."""


@dataclass(frozen=True)
class Harness:
    """One installation target: paths, tool vocabulary, spawn/skill mechanics."""

    id: str
    label: str
    # Project-level config dir name (".claude" / ".omp" / ".pi").
    config_dir: str
    # Path segments of the user-level config root, relative to $HOME.
    global_segments: tuple[str, ...]
    # Context file this harness auto-loads from the project root.
    context_file: str
    # canonical tool name -> harness tool name (None = unsupported)
    tool_names: Mapping[str, str | None]
    # profile model alias (haiku/sonnet/opus/…) -> harness model selector.
    # A selector may be a fallback chain: harnesses that accept a CSV `model:`
    # try the entries in order and fall back to the parent session's model
    # when none resolves. An empty string means "omit the model: line
    # entirely" (inherit the parent session's model).
    model_aliases: Mapping[str, str]
    # Frontmatter key naming the agent inside a spawn block.
    spawn_key: str
    # "task-tool"  — harness has a native subagent tool
    # "cli-bridge" — subagents run as `hpr spawn` child processes
    spawn_mode: str
    # One-line spawn example, rendered into the spawn contract of the skills.
    spawn_syntax: str
    # How the orchestrator fans out a wave of N agents on this harness.
    parallel_note: str
    # Template for loading a step skill; `{name}` is the skill slug.
    skill_load: str
    # How the user starts a run.
    invoke_command: str
    # Escalation lane that drives a real browser, or None when unavailable.
    browser_lane: str | None
    # "claude-settings" — PreToolUse entry in .claude/settings.json
    # "extension"       — JS/TS extension module under <config>/extensions/
    # "none"            — harness has no web tool worth intercepting
    reminder_hook: str
    # Browser-lane vocabulary, used by the browser-fetcher agent prompt. Empty
    # on harnesses with no lane (the agent is not installed there at all).
    browser_label: str = ""
    browser_surface: str = ""
    browser_navigate: str = ""
    browser_text: str = ""
    browser_screenshot: str = ""
    # Subdirectory names under the config dir.
    skills_dirname: str = "skills"
    agents_dirname: str = "agents"

    # -- paths --------------------------------------------------------------

    def skills_dir(self, root: Path) -> Path:
        """Project-level skills root, e.g. `<root>/.claude/skills`."""
        return root / self.config_dir / self.skills_dirname

    def agents_dir(self, root: Path) -> Path:
        """Project-level agents dir, e.g. `<root>/.omp/agents`."""
        return root / self.config_dir / self.agents_dirname

    def global_root(self, home: Path | None = None) -> Path:
        """User-level config root, e.g. `~/.omp/agent`."""
        base = Path.home() if home is None else home
        return base.joinpath(*self.global_segments)

    def global_skills_dir(self, home: Path | None = None) -> Path:
        return self.global_root(home) / self.skills_dirname

    def global_agents_dir(self, home: Path | None = None) -> Path:
        return self.global_root(home) / self.agents_dirname

    def extensions_dir(self, root: Path) -> Path:
        return root / self.config_dir / "extensions"

    @property
    def skills_rel(self) -> str:
        """Skills root as a display path: `.claude/skills`."""
        return f"{self.config_dir}/{self.skills_dirname}"

    @property
    def agents_rel(self) -> str:
        return f"{self.config_dir}/{self.agents_dirname}"

    def skill_rel(self, name: str) -> str:
        """Installed SKILL.md path for a skill slug, relative to the project."""
        return f"{self.skills_rel}/{name}/SKILL.md"

    # -- prompt vocabulary --------------------------------------------------

    def supports(self, tool: str) -> bool:
        return self.tool_names.get(tool) is not None

    @property
    def has_subagents(self) -> bool:
        """True when the harness itself can spawn subagents in-session."""
        return self.spawn_mode == "task-tool"

    def tool(self, name: str) -> str:
        """One harness tool name. Unsupported tools render as the empty string."""
        if name not in CANONICAL_TOOLS:
            raise HarnessError(f"unknown canonical tool {name!r}")
        return self.tool_names.get(name) or ""

    def tools(self, *names: str) -> str:
        """Render a frontmatter `tools:` value, dropping unsupported tools.

        Order follows the call, not the canonical list, so a prompt keeps the
        emphasis it was written with (`Read, Edit` reads as a tool lock).
        """
        out: list[str] = []
        for name in names:
            mapped = self.tool(name)
            if mapped and mapped not in out:
                out.append(mapped)
        return ", ".join(out)

    def model_line(self, alias: str) -> str:
        """The agent frontmatter `model:` line for a profile model alias.

        Returns the empty string when the harness has no stable selector for
        that tier — the spawned agent then inherits the parent session's
        model, which is always better than failing the spawn on an
        unresolvable model id.
        """
        selector = self.model_aliases.get(alias, alias if self.id == "claude" else "")
        if not selector:
            return ""
        return f"model: {selector}"

    def with_models(self, overrides: Mapping[str, str] | None) -> Harness:
        """A copy whose model aliases are overridden (vault `[harness.models]`).

        Unlisted aliases keep the built-in selector; an explicitly empty value
        omits the `model:` line for that tier.
        """
        if not overrides:
            return self
        merged = {**self.model_aliases, **{k: str(v) for k, v in overrides.items()}}
        return replace(self, model_aliases=merged)

    def load_skill(self, name: str) -> str:
        """How this harness loads step skill `name` into context."""
        return self.skill_load.format(name=name, skills=self.skills_rel)

    def spawn_block_intro(self, agent: str) -> str:
        """The first line of a spawn block: `<spawn_key>: <agent>`."""
        return f"{self.spawn_key}: {agent}"


# ---------------------------------------------------------------------------
# Claude Code — the reference harness. Native Task subagents, Skill tool,
# `.claude/` layout, PreToolUse hook in settings.json.
# ---------------------------------------------------------------------------
CLAUDE = Harness(
    id="claude",
    label="Claude Code",
    config_dir=".claude",
    global_segments=(".claude",),
    context_file="CLAUDE.md",
    tool_names={
        "bash": "Bash",
        "read": "Read",
        "write": "Write",
        "edit": "Edit",
        "glob": "Glob",
        "grep": "Grep",
        "task": "Task",
        "web_search": "WebSearch",
        "skill": "Skill",
        "todo": "TodoWrite",
        "tool_search": "ToolSearch",
        # Chrome arrives as claude-in-chrome MCP tools, loaded via ToolSearch.
        "browser": None,
    },
    model_aliases={"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"},
    spawn_key="subagent_type",
    spawn_mode="task-tool",
    spawn_syntax='Task(subagent_type: "<agent>", prompt: "<the block below>")',
    parallel_note=(
        "Spawn a wave by issuing N Task calls in ONE message — they run "
        "concurrently. Sequential messages run serially and waste wall time."
    ),
    skill_load='Skill(skill: "{name}")',
    invoke_command="/hyperresearch",
    browser_lane="claude-in-chrome",
    reminder_hook="claude-settings",
    browser_label="Claude-in-Chrome",
    browser_surface="the Claude-in-Chrome tools",
    browser_navigate="navigate",
    browser_text="get_page_text",
    browser_screenshot="the computer tool",
)

# ---------------------------------------------------------------------------
# Oh My Pi (omp) — native `task` subagents from `.omp/agents/*.md`, skills
# from `.omp/skills/<name>/SKILL.md` read through `skill://`, lowercase tool
# names, model selectors via `modelRoles` aliases.
# ---------------------------------------------------------------------------
OMP = Harness(
    id="omp",
    label="OMP",
    config_dir=".omp",
    global_segments=(".omp", "agent"),
    context_file="AGENTS.md",
    tool_names={
        "bash": "bash",
        "read": "read",
        "write": "write",
        "edit": "edit",
        "glob": "glob",
        "grep": "grep",
        "task": "task",
        "web_search": "web_search",
        # omp has no Skill tool — skills are read through `skill://<name>`.
        "skill": None,
        "todo": "todo",
        "tool_search": None,
        # omp drives a real browser from the `eval` tool's `browser` global.
        "browser": "eval",
    },
    # GLM through the Zhipu coding plan first, the direct zai endpoint second:
    # omp tries a CSV `model:` in order and falls back to the parent session's
    # model when nothing resolves, so a machine with neither credential
    # degrades instead of failing the spawn. Override per vault with
    # `[harness.models.omp]`.
    model_aliases={
        "haiku": "zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash",
        "sonnet": "zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash",
        "opus": "zhipu-coding-plan/glm-5.3, zai/glm-5.3",
    },
    spawn_key="agent",
    spawn_mode="task-tool",
    spawn_syntax=(
        'task(context: "<shared context>", tasks: [{agent: "<agent>", '
        'task: "<the block below>"}])'
    ),
    parallel_note=(
        "Spawn a wave by putting all N items in ONE `task` call's `tasks[]` "
        "array — they run concurrently. One call per agent serializes the wave."
    ),
    skill_load="read skill://{name}",
    invoke_command="/skill:hyperresearch",
    browser_lane="omp-browser",
    reminder_hook="extension",
    browser_label="the eval tool's relay browser",
    browser_surface="the eval tool's relay browser",
    browser_navigate="tab.goto(url)",
    browser_text="tab.extract()",
    browser_screenshot="tab.screenshot()",
)

# ---------------------------------------------------------------------------
# Pi — skills and context files, but no subagent tool and no web tools. The
# pipeline's parallelism comes from `hpr spawn`, which runs each agent as a
# `pi -p` child process with the installed agent prompt as its system prompt.
# ---------------------------------------------------------------------------
PI = Harness(
    id="pi",
    label="Pi",
    config_dir=".pi",
    global_segments=(".pi", "agent"),
    context_file="AGENTS.md",
    tool_names={
        "bash": "bash",
        "read": "read",
        "write": "write",
        "edit": "edit",
        # pi's glob equivalent is `find`; `ls` has no canonical counterpart.
        "glob": "find",
        "grep": "grep",
        # No subagent tool: the orchestrator shells out to `hpr spawn`.
        "task": None,
        # No web search/fetch tool at all — `hpr fetch` / `hpr scholar` are
        # the only web lane, which is what the pipeline wants anyway.
        "web_search": None,
        "skill": None,
        "todo": None,
        "tool_search": None,
        "browser": None,
    },
    # pi resolves `--model` fuzzily against the catalog, and hyperresearch's
    # tiers are Anthropic aliases: on a Gemini/GLM setup they would not
    # resolve. Inherit pi's own default model instead.
    model_aliases={"haiku": "", "sonnet": "", "opus": ""},
    spawn_key="agent",
    spawn_mode="cli-bridge",
    spawn_syntax='bash: hpr spawn <agent> --prompt-file <file> --json',
    parallel_note=(
        "pi has no subagent tool. Write each agent prompt to a file and fan "
        "the wave out with ONE `hpr spawn --batch <file.json>` call — it runs "
        "the child `pi` processes concurrently and returns every result."
    ),
    skill_load="read {skills}/{name}/SKILL.md",
    invoke_command="/skill:hyperresearch",
    # No Chrome control surface: blocked fetches stay queued as escalations,
    # which is exactly the documented degradation.
    browser_lane=None,
    reminder_hook="none",
)

HARNESSES: dict[str, Harness] = {h.id: h for h in (CLAUDE, OMP, PI)}
DEFAULT_HARNESS_ID = CLAUDE.id


def get_harness(harness_id: str) -> Harness:
    """Look up a harness by id. Unknown ids raise with the valid set."""
    try:
        return HARNESSES[harness_id]
    except KeyError:
        valid = ", ".join(sorted(HARNESSES))
        raise HarnessError(f"unknown harness '{harness_id}' (valid: {valid})") from None


def parse_harness_ids(values: Iterable[str] | None) -> tuple[str, ...]:
    """Normalize CLI/config harness selectors.

    Accepts repeated flags and comma-separated values (`--harness claude,omp`)
    plus the literal `all`. Order is preserved, duplicates dropped.
    """
    if values is None:
        return ()
    out: list[str] = []
    for value in values:
        for part in str(value).replace(";", ",").split(","):
            token = part.strip().lower()
            if not token:
                continue
            if token == "all":
                for hid in HARNESSES:
                    if hid not in out:
                        out.append(hid)
                continue
            get_harness(token)  # validate eagerly, with a helpful message
            if token not in out:
                out.append(token)
    return tuple(out)


def detect_harness_ids(
    root: Path | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Which harnesses this machine/project actually uses.

    A harness counts as present when its project config dir exists, its
    user-level dir exists, or it announced itself in the environment (the
    common case: the agent running `hpr install` is the harness itself).
    Detection never returns an empty tuple — Claude Code is the fallback so a
    bare `hpr install` on a fresh machine behaves exactly as before.
    """
    environ = os.environ if env is None else env
    home_dir = Path.home() if home is None else home
    env_markers: dict[str, tuple[str, ...]] = {
        "claude": ("CLAUDE_PROJECT_DIR", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"),
        "omp": ("OMP_PROFILE", "OMP_SESSION_ID", "OMP_AGENT_ID"),
        "pi": ("PI_CODING_AGENT_DIR", "PI_PROFILE", "PI_SESSION_ID"),
    }

    found: list[str] = []
    for hid, harness in HARNESSES.items():
        present = any(environ.get(name) for name in env_markers[hid])
        if not present and root is not None:
            present = (root / harness.config_dir).is_dir()
        if not present:
            present = harness.global_root(home_dir).is_dir()
        if present:
            found.append(hid)

    return tuple(found) if found else (DEFAULT_HARNESS_ID,)


def resolve_harnesses(
    selected: Sequence[str] | None = None,
    configured: Sequence[str] | None = None,
    root: Path | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Harness, ...]:
    """Resolve the harnesses to install into.

    Precedence: explicit selection (CLI) > vault config `[harness] targets` >
    autodetection. The result is never empty.
    """
    ids = parse_harness_ids(selected)
    if not ids:
        ids = parse_harness_ids(configured)
    if not ids:
        ids = detect_harness_ids(root=root, home=home, env=env)
    return tuple(get_harness(hid) for hid in ids)


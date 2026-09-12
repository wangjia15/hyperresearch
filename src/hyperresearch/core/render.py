"""Prompt-template rendering — profile values into skill/agent prompts.

Skill files and agent prompt bodies are Jinja templates with NON-STANDARD
delimiters, because the prompts themselves legitimately contain `{{ ... }}`
(spawn-template placeholders like `{{paste research/query-<vault_tag>.md}}`)
and `{ ... }` (JSON examples):

    variables:  << p.source_min >>
    blocks:     <% if ... %> ... <% endif %>
    comments:   <# ... #>

Context exposed to templates:
    p          — the primary profile (default: full)
    <name>     — every available profile by name (e.g. `full`, `light`),
                 so tier tables can reference both tiers in one file.
    h          — the target harness (see core/harnesses.py): tool names,
                 skill-load and spawn mechanics, install paths. Defaults to
                 Claude Code, whose rendering is byte-identical to the
                 pre-harness prompts.

Filters:
    dash    — join a (low, high) range with an en dash (U+2013)
    hyphen  — join a (low, high) range with a hyphen: (1, 2) -> "1-2"

Rendering is STRICT: an unknown variable raises instead of silently emitting
an empty string — a typo in a template must fail the install/tests, not ship
a prompt with a hole in it.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, StrictUndefined

from hyperresearch.core.harnesses import DEFAULT_HARNESS_ID, Harness, get_harness
from hyperresearch.core.profiles import list_profiles, resolve_profile

EN_DASH = "–"


def _dash(value) -> str:
    low, high = value
    return f"{low}{EN_DASH}{high}"


def _hyphen(value) -> str:
    low, high = value
    return f"{low}-{high}"


def prompt_env() -> Environment:
    env = Environment(
        variable_start_string="<<",
        variable_end_string=">>",
        block_start_string="<%",
        block_end_string="%>",
        comment_start_string="<#",
        comment_end_string="#>",
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    env.filters["dash"] = _dash
    env.filters["hyphen"] = _hyphen
    return env


def build_render_context(
    config_path: Path | None = None,
    primary: str = "full",
    harness: str | Harness = DEFAULT_HARNESS_ID,
) -> dict[str, object]:
    """Resolve every profile plus the target harness.

    Exposes each profile by name, the primary one as `p`, and the harness as
    `h`. `p`/`h` are assigned last: a user-defined overlay profile with one of
    those names cannot shadow them.
    """
    profiles = {name: resolve_profile(name, config_path) for name in list_profiles(config_path)}
    if primary not in profiles:
        # resolve_profile raises a helpful error for unknown names
        profiles[primary] = resolve_profile(primary, config_path)
    target = harness if isinstance(harness, Harness) else get_harness(harness)
    return {**profiles, "p": profiles[primary], "h": target}


def render_prompt(text: str, context: dict[str, object]) -> str:
    """Render one prompt template with the given profile/harness context."""
    return prompt_env().from_string(text).render(**context)


def render_header(profile_name: str, version: str) -> str:
    """Provenance comment for installed (rendered) prompt files.

    Inserted AFTER the YAML frontmatter block — a comment before the opening
    `---` would break frontmatter parsing.
    """
    return (
        f"<!-- rendered from profile \"{profile_name}\" (hyperresearch {version}) "
        "— edit the profile or the package template, not this file -->"
    )


def insert_after_frontmatter(content: str, line: str) -> str:
    """Insert `line` on its own line after the closing frontmatter delimiter.

    If the content has no leading frontmatter, prepend the line.
    """
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            insert_at = content.find("\n", end + 1)
            if insert_at != -1:
                return content[: insert_at + 1] + line + "\n" + content[insert_at + 1 :]
    return line + "\n" + content


def compact_frontmatter(content: str) -> str:
    """Drop blank lines from the leading YAML frontmatter block.

    A harness without a stable selector for a model tier renders an empty
    `model:` line (see `Harness.model_line`), which would otherwise leave a
    blank line inside the frontmatter — legal YAML, but it also lands inside
    a preceding folded `description: >` block as a stray newline.
    """
    if not content.startswith("---\n"):
        return content
    end = content.find("\n---", 3)
    if end == -1:
        return content
    head, rest = content[4:end], content[end:]
    kept = [line for line in head.split("\n") if line.strip()]
    return "---\n" + "\n".join(kept) + rest

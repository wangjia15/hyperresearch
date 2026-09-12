"""Tests for harness adaptation (core/harnesses.py + the per-harness install).

The contract these pin: one pipeline, three harnesses, and Claude Code's
rendering unchanged. The behaviours worth a test are the ones a plausible bug
breaks silently — a tool name that doesn't exist on the target harness, an
agent written into the wrong directory, a skill-load instruction the harness
can't execute, or a browser-lane agent installed where there is no browser.
"""

from __future__ import annotations

import pytest

from hyperresearch.core.harnesses import (
    CLAUDE,
    HarnessError,
    detect_harness_ids,
    get_harness,
    parse_harness_ids,
    resolve_harnesses,
)
from hyperresearch.core.hooks import (
    install_global_hooks,
    install_hooks,
    install_step_skills,
)
from hyperresearch.core.render import build_render_context, render_prompt

OMP = get_harness("omp")
PI = get_harness("pi")


class TestToolVocabulary:
    def test_tool_names_are_harness_native(self):
        assert CLAUDE.tools("bash", "read", "write") == "Bash, Read, Write"
        assert OMP.tools("bash", "read", "write") == "bash, read, write"

    def test_unsupported_tools_drop_out_of_the_list(self):
        # Pi has no subagent tool and no web search: naming them in a prompt's
        # `tools:` line would advertise tools the harness cannot provide.
        assert PI.tools("bash", "read", "task", "web_search") == "bash, read"
        assert not PI.supports("task")
        assert not PI.supports("web_search")

    def test_glob_maps_to_the_harness_equivalent(self):
        assert PI.tool("glob") == "find"

    def test_unknown_canonical_tool_is_an_error(self):
        with pytest.raises(HarnessError):
            CLAUDE.tool("telepathy")


class TestModelLine:
    def test_claude_renders_the_profile_alias_verbatim(self):
        assert CLAUDE.model_line("sonnet") == "model: sonnet"

    def test_omp_maps_the_two_tiers_to_glm_models(self):
        # Reading/fetching volume runs on flash; judgment steps on glm-5.3.
        # Each selector is a fallback chain omp tries in order.
        assert OMP.model_line("sonnet") == (
            "model: zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash"
        )
        assert OMP.model_line("opus") == "model: zhipu-coding-plan/glm-5.3, zai/glm-5.3"

    def test_pi_omits_the_line_and_inherits_the_parent_model(self):
        # Anthropic tier aliases would not resolve on a Gemini/GLM setup;
        # failing the spawn is worse than inheriting the session's model.
        assert PI.model_line("sonnet") == ""

    def test_vault_overrides_replace_only_the_named_tiers(self):
        tuned = OMP.with_models({"opus": "zhipu-coding-plan/glm-5.3:high"})
        assert tuned.model_line("opus") == "model: zhipu-coding-plan/glm-5.3:high"
        assert tuned.model_line("sonnet") == OMP.model_line("sonnet")
        # The built-in harness object is not mutated by an override.
        assert OMP.model_line("opus") == "model: zhipu-coding-plan/glm-5.3, zai/glm-5.3"

    def test_an_empty_override_omits_the_line(self):
        assert OMP.with_models({"opus": ""}).model_line("opus") == ""


class TestSkillLoading:
    def test_each_harness_gets_an_executable_instruction(self):
        assert CLAUDE.load_skill("hyperresearch-1-decompose") == (
            'Skill(skill: "hyperresearch-1-decompose")'
        )
        assert OMP.load_skill("hyperresearch-1-decompose") == (
            "read skill://hyperresearch-1-decompose"
        )
        # Pi has neither a Skill tool nor the skill:// protocol — only a path.
        assert PI.load_skill("hyperresearch-1-decompose") == (
            "read .pi/skills/hyperresearch-1-decompose/SKILL.md"
        )


class TestSelection:
    def test_comma_and_repeat_forms_normalize(self):
        assert parse_harness_ids(["claude,omp", "pi"]) == ("claude", "omp", "pi")

    def test_all_expands_and_dedupes(self):
        assert parse_harness_ids(["all", "omp"]) == ("claude", "omp", "pi")

    def test_unknown_id_names_the_valid_set(self):
        with pytest.raises(HarnessError) as exc:
            parse_harness_ids(["cursor"])
        assert "claude" in str(exc.value)

    def test_explicit_selection_beats_config_and_detection(self, tmp_path):
        (tmp_path / ".pi").mkdir()
        resolved = resolve_harnesses(
            selected=["omp"], configured=["claude"], root=tmp_path, home=tmp_path
        )
        assert [h.id for h in resolved] == ["omp"]

    def test_config_targets_used_when_no_flag(self, tmp_path):
        resolved = resolve_harnesses(configured=["pi"], root=tmp_path, home=tmp_path)
        assert [h.id for h in resolved] == ["pi"]

    def test_detection_reads_project_config_dirs(self, tmp_path):
        (tmp_path / ".omp").mkdir()
        assert detect_harness_ids(root=tmp_path, home=tmp_path, env={}) == ("omp",)

    def test_detection_reads_the_environment(self, tmp_path):
        ids = detect_harness_ids(
            root=tmp_path, home=tmp_path, env={"PI_CODING_AGENT_DIR": "/x"}
        )
        assert ids == ("pi",)

    def test_detection_falls_back_to_claude(self, tmp_path):
        assert detect_harness_ids(root=tmp_path, home=tmp_path, env={}) == ("claude",)


class TestRenderContext:
    def test_harness_is_exposed_as_h(self):
        ctx = build_render_context(None, primary="full", harness="omp")
        assert render_prompt("<< h.id >>/<< h.tool('bash') >>", ctx) == "omp/bash"

    def test_claude_is_the_default_harness(self):
        ctx = build_render_context(None, primary="full")
        assert render_prompt("<< h.label >>", ctx) == "Claude Code"


class TestProjectInstall:
    """A harness install must land in that harness's own layout."""

    def test_omp_layout_and_lowercase_tools(self, tmp_vault):
        install_hooks(tmp_vault.root, "hpr", harnesses=["omp"])

        entry = tmp_vault.root / ".omp" / "skills" / "hyperresearch" / "SKILL.md"
        fetcher = tmp_vault.root / ".omp" / "agents" / "hyperresearch-fetcher.md"
        assert entry.exists()
        assert fetcher.exists()
        assert not (tmp_vault.root / ".claude").exists()

        body = fetcher.read_text(encoding="utf-8")
        assert "tools: bash, read, write, web_search" in body
        assert "model: zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash" in body
        assert "read skill://" in entry.read_text(encoding="utf-8")

        # Judgment steps get the stronger model, reading volume the flash one.
        patcher = (
            tmp_vault.root / ".omp" / "agents" / "hyperresearch-patcher.md"
        ).read_text(encoding="utf-8")
        assert "model: zhipu-coding-plan/glm-5.3, zai/glm-5.3" in patcher

    def test_pi_layout_drops_tools_it_has_not_got(self, tmp_vault):
        install_hooks(tmp_vault.root, "hpr", harnesses=["pi"])

        body = (
            tmp_vault.root / ".pi" / "agents" / "hyperresearch-fetcher.md"
        ).read_text(encoding="utf-8")
        frontmatter = body.split("---\n")[1]
        assert "tools: bash, read, write" in frontmatter
        assert "web_search" not in frontmatter
        # Pi inherits the child's configured model instead of an alias it
        # cannot resolve — and the omitted line must not leave a blank line
        # inside the frontmatter, where it would land in the folded
        # `description: >` block as a stray newline.
        assert "model:" not in frontmatter
        assert not [line for line in frontmatter.splitlines() if not line.strip()]

    def test_browser_fetcher_only_where_a_browser_lane_exists(self, tmp_vault):
        install_hooks(tmp_vault.root, "hpr", harnesses=["claude", "omp", "pi"])

        assert (
            tmp_vault.root / ".claude" / "agents" / "hyperresearch-browser-fetcher.md"
        ).exists()
        assert (
            tmp_vault.root / ".omp" / "agents" / "hyperresearch-browser-fetcher.md"
        ).exists()
        # Pi cannot drive a browser: escalations stay queued instead.
        assert not (
            tmp_vault.root / ".pi" / "agents" / "hyperresearch-browser-fetcher.md"
        ).exists()

    def test_reminder_lane_is_per_harness(self, tmp_vault):
        install_hooks(tmp_vault.root, "hpr", harnesses=["claude", "omp", "pi"])

        assert (tmp_vault.root / ".claude" / "settings.json").exists()
        extension = tmp_vault.root / ".omp" / "extensions" / "hyperresearch" / "index.ts"
        assert extension.exists()
        source = extension.read_text(encoding="utf-8")
        assert 'event.toolName !== "web_search"' in source
        assert "HYPERRESEARCH" in source
        # Pi has no web tool to intercept — no extension, no settings file.
        assert not (tmp_vault.root / ".pi" / "extensions").exists()
        assert not (tmp_vault.root / ".pi" / "settings.json").exists()

    def test_multi_harness_install_is_idempotent(self, tmp_vault):
        first = install_hooks(tmp_vault.root, "hpr", harnesses=["claude", "omp"])
        assert first
        assert not install_hooks(tmp_vault.root, "hpr", harnesses=["claude", "omp"])

    def test_default_harness_is_claude(self, tmp_vault):
        install_hooks(tmp_vault.root, "hpr")
        assert (tmp_vault.root / ".claude" / "agents").is_dir()
        assert not (tmp_vault.root / ".omp").exists()


class TestStepSkills:
    def test_steps_only_install_targets_the_harness(self, tmp_vault):
        install_step_skills(tmp_vault.root, harnesses=["pi"])
        assert (
            tmp_vault.root / ".pi" / "skills" / "hyperresearch-1-decompose" / "SKILL.md"
        ).exists()
        # Entry skill and agents are NOT part of the lazy bootstrap.
        assert not (tmp_vault.root / ".pi" / "agents").exists()


class TestGlobalInstall:
    def test_each_harness_uses_its_own_user_level_root(self, tmp_path):
        install_global_hooks(tmp_path, "hpr", harnesses=["claude", "omp", "pi"])

        assert (tmp_path / ".claude" / "skills" / "hyperresearch" / "SKILL.md").exists()
        assert (
            tmp_path / ".omp" / "agent" / "skills" / "hyperresearch" / "SKILL.md"
        ).exists()
        assert (tmp_path / ".pi" / "agent" / "agents").is_dir()
        # Step skills stay per-project: global advertising is context noise.
        assert not (
            tmp_path / ".omp" / "agent" / "skills" / "hyperresearch-1-decompose"
        ).exists()


class TestContextFiles:
    def test_claude_writes_claude_md(self, tmp_path):
        from hyperresearch.core.agent_docs import inject_agent_docs

        assert inject_agent_docs(tmp_path) == ["CLAUDE.md (created)"]
        assert (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / "AGENTS.md").exists()

    def test_omp_and_pi_share_one_agents_md_covering_both(self, tmp_path):
        from hyperresearch.core.agent_docs import inject_agent_docs

        assert inject_agent_docs(tmp_path, [OMP, PI]) == ["AGENTS.md (created)"]
        body = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert ".omp/skills/hyperresearch/SKILL.md" in body
        assert ".pi/skills/hyperresearch/SKILL.md" in body
        # Both mechanics must be stated: omp has web_search, pi has none.
        assert "Pi has no web-search tool" in body
        assert "spawn` call" in body

    def test_injection_preserves_surrounding_user_content(self, tmp_path):
        from hyperresearch.core.agent_docs import inject_agent_docs

        target = tmp_path / "AGENTS.md"
        target.write_text("# Mine\n\nkeep me\n", encoding="utf-8")
        inject_agent_docs(tmp_path, [OMP])
        body = target.read_text(encoding="utf-8")
        assert "keep me" in body
        assert "hyperresearch:start" in body
        assert inject_agent_docs(tmp_path, [OMP]) == []

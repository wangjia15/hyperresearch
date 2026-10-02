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
    detect_zcode_model_aliases,
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
ZCODE = get_harness("zcode")


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
        # A single zai selector; omp falls back to the session model if it
        # does not resolve.
        assert OMP.model_line("sonnet") == "model: zai/glm-5.3-flash"
        assert OMP.model_line("opus") == "model: zai/glm-5.3"

    def test_pi_omits_the_line_and_inherits_the_parent_model(self):
        # Anthropic tier aliases would not resolve on a Gemini/GLM setup;
        # failing the spawn is worse than inheriting the session's model.
        assert PI.model_line("sonnet") == ""

    def test_vault_overrides_replace_only_the_named_tiers(self):
        tuned = OMP.with_models({"opus": "zai/glm-5.3:high"})
        assert tuned.model_line("opus") == "model: zai/glm-5.3:high"
        assert tuned.model_line("sonnet") == OMP.model_line("sonnet")
        # The built-in harness object is not mutated by an override.
        assert OMP.model_line("opus") == "model: zai/glm-5.3"

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
        assert parse_harness_ids(["all", "omp"]) == ("claude", "omp", "zcode", "pi")

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
        assert "model: zai/glm-5.3-flash" in body
        assert "read skill://" in entry.read_text(encoding="utf-8")

        # Judgment steps get the stronger model, reading volume the flash one.
        patcher = (
            tmp_vault.root / ".omp" / "agents" / "hyperresearch-patcher.md"
        ).read_text(encoding="utf-8")
        assert "model: zai/glm-5.3" in patcher

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


class TestZCode:
    """ZCode is Claude-shaped (Task/Skill tools) with its own layout, a
    selector grammar that needs `provider/model$level`, and a frontmatter
    parser that cannot read YAML block scalars."""

    def test_tool_vocabulary_is_capitalized_like_claude_minus_what_it_lacks(self):
        assert ZCODE.tools("bash", "read", "write", "web_search") == "Bash, Read, Write, WebSearch"
        assert ZCODE.tool("task") == "Task"
        # No on-demand tool loader and no subagent-reachable browser.
        assert not ZCODE.supports("tool_search")
        assert not ZCODE.supports("browser")
        assert ZCODE.browser_lane is None

    def test_spawns_and_loads_skills_through_native_tools(self):
        assert ZCODE.has_subagents
        assert ZCODE.spawn_block_intro("hyperresearch-fetcher") == (
            "subagent_type: hyperresearch-fetcher"
        )
        assert ZCODE.load_skill("hyperresearch-2-width-sweep") == (
            'Skill(skill: "hyperresearch-2-width-sweep")'
        )

    def test_default_inherits_the_session_model(self):
        # The provider id depends on the user's login, so the static
        # default must not name one: an unresolvable selector fails the spawn.
        for alias in ("haiku", "sonnet", "opus"):
            assert ZCODE.model_line(alias) == ""

    def test_layout_paths(self, tmp_path):
        assert ZCODE.skills_rel == ".zcode/skills"
        assert ZCODE.agents_dir(tmp_path) == tmp_path / ".zcode" / "agents"
        assert ZCODE.global_agents_dir(tmp_path) == tmp_path / ".zcode" / "agents"
        assert ZCODE.context_file == "AGENTS.md"

    def test_detected_from_the_environment_and_project_dir(self, tmp_path):
        assert detect_harness_ids(
            root=tmp_path, home=tmp_path, env={"ZCODE_RUNTIME_ENV": "cli"}
        ) == ("zcode",)
        (tmp_path / ".zcode").mkdir()
        assert detect_harness_ids(root=tmp_path, home=tmp_path / "nohome", env={}) == ("zcode",)


def _zcode_home(tmp_path, config: dict | str | None):
    cli = tmp_path / ".zcode" / "cli"
    cli.mkdir(parents=True)
    if config is not None:
        import json

        text = config if isinstance(config, str) else json.dumps(config)
        (cli / "config.json").write_text(text, encoding="utf-8")
    return tmp_path


def _glm_provider(**overrides):
    levels = {"enabled": True, "levels": ["low", "max", "high"]}
    provider = {
        "models": {
            "glm-5.2": {"reasoning": levels},
            "glm-5.3": {"reasoning": levels},
            "glm-5.3-flash": {"reasoning": levels},
        }
    }
    provider.update(overrides)
    return provider


class TestZCodeModelDetection:
    def test_selectors_come_from_the_provider_the_user_is_logged_into(self, tmp_path):
        home = _zcode_home(
            tmp_path,
            {
                "model": {"main": "bigmodel/glm-5.2"},
                "provider": {"zai": _glm_provider(), "bigmodel": _glm_provider()},
            },
        )
        assert detect_zcode_model_aliases(home) == {
            "haiku": "bigmodel/glm-5.3-flash$low",
            "sonnet": "bigmodel/glm-5.3-flash$low",
            "opus": "bigmodel/glm-5.3$high",
        }

    def test_a_different_login_yields_a_different_provider_prefix(self, tmp_path):
        home = _zcode_home(
            tmp_path,
            {"model": {"main": "zai/glm-5.3"}, "provider": {"zai": _glm_provider()}},
        )
        assert detect_zcode_model_aliases(home)["opus"] == "zai/glm-5.3$high"

    def test_model_id_casing_follows_the_registry(self, tmp_path):
        models = {
            "GLM-5.3": {"reasoning": {"enabled": True, "levels": ["high"]}},
            "GLM-5.3-Flash": {"reasoning": {"enabled": True, "levels": ["low"]}},
        }
        home = _zcode_home(
            tmp_path,
            {"model": {"main": "p/x"}, "provider": {"p": {"models": models}}},
        )
        aliases = detect_zcode_model_aliases(home)
        assert aliases["opus"] == "p/GLM-5.3$high"
        assert aliases["haiku"] == "p/GLM-5.3-Flash$low"

    def test_a_level_the_model_does_not_offer_is_not_suffixed(self, tmp_path):
        # "reasoning-level-not-supported" fails the spawn just like a missing one.
        provider = {
            "models": {
                "glm-5.3": {"reasoning": {"enabled": True, "levels": ["low", "max"]}},
                "glm-5.3-flash": {"reasoning": {"enabled": True, "levels": ["max"]}},
            }
        }
        home = _zcode_home(
            tmp_path, {"model": {"main": "p/x"}, "provider": {"p": provider}}
        )
        aliases = detect_zcode_model_aliases(home)
        assert aliases["opus"] == "p/glm-5.3"
        assert aliases["haiku"] == "p/glm-5.3-flash"

    def test_a_model_without_reasoning_gets_a_plain_selector(self, tmp_path):
        provider = {"models": {"glm-5.3": {}, "glm-5.3-flash": {}}}
        home = _zcode_home(
            tmp_path, {"model": {"main": "p/x"}, "provider": {"p": provider}}
        )
        assert detect_zcode_model_aliases(home)["sonnet"] == "p/glm-5.3-flash"

    @pytest.mark.parametrize(
        "config",
        [
            None,  # no config file at all
            "{not json",
            "[]",
            {},
            {"model": {"main": "no-slash"}},
            {"model": {"main": "ghost/glm-5.2"}, "provider": {}},
            # The live provider lacks one of the two tiers.
            {
                "model": {"main": "p/x"},
                "provider": {"p": {"models": {"glm-5.3": {}}}},
            },
        ],
    )
    def test_anything_unreadable_falls_back_to_inheriting(self, tmp_path, config):
        home = _zcode_home(tmp_path, config)
        assert detect_zcode_model_aliases(home) == {}


class TestZCodeInstall:
    def test_layout_agents_and_no_browser_fetcher(self, tmp_vault):
        install_hooks(tmp_vault.root, "hpr", harnesses=["zcode"])

        assert (tmp_vault.root / ".zcode" / "skills" / "hyperresearch" / "SKILL.md").exists()
        assert (
            tmp_vault.root / ".zcode" / "skills" / "hyperresearch-1-decompose" / "SKILL.md"
        ).exists()
        agents = tmp_vault.root / ".zcode" / "agents"
        assert (agents / "hyperresearch-fetcher.md").exists()
        # No subagent-reachable browser: escalations stay queued.
        assert not (agents / "hyperresearch-browser-fetcher.md").exists()
        assert not (tmp_vault.root / ".claude").exists()

    def test_agent_frontmatter_is_readable_by_zcodes_loose_parser(self, tmp_vault):
        install_hooks(tmp_vault.root, "hpr", harnesses=["zcode"])

        for agent in (tmp_vault.root / ".zcode" / "agents").glob("hyperresearch-*.md"):
            text = agent.read_text(encoding="utf-8")
            frontmatter = text.split("---\n")[1]
            # A block scalar would collapse to the literal ">" in zcode.
            assert not any(
                line.rstrip().endswith((": >", ": |", ": >-", ": |-"))
                for line in frontmatter.splitlines()
            ), agent.name
            description = next(
                line for line in frontmatter.splitlines() if line.startswith("description: ")
            )
            assert len(description) > len("description: ") + 40, agent.name

    def test_skills_keep_their_block_scalar_description(self, tmp_vault):
        # zcode's skill parser reads `>` blocks; only agents need flattening.
        install_hooks(tmp_vault.root, "hpr", harnesses=["zcode"])
        entry = (
            tmp_vault.root / ".zcode" / "skills" / "hyperresearch" / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert "\ndescription: >\n" in entry

    def test_agents_name_zcodes_own_tools(self, tmp_vault):
        install_hooks(tmp_vault.root, "hpr", harnesses=["zcode"])
        fetcher = (
            tmp_vault.root / ".zcode" / "agents" / "hyperresearch-fetcher.md"
        ).read_text(encoding="utf-8")
        assert "tools: Bash, Read, Write, WebSearch" in fetcher
        # The default carries no provider guess — agents inherit the session model.
        assert "\nmodel:" not in fetcher

    def test_reminder_hook_lands_in_zcode_config_and_enables_hooks(self, tmp_vault):
        import json

        install_hooks(tmp_vault.root, "hpr", harnesses=["zcode"])
        config = json.loads(
            (tmp_vault.root / ".zcode" / "config.json").read_text(encoding="utf-8")
        )
        # Config-file hooks are off unless enabled is true.
        assert config["hooks"]["enabled"] is True
        (entry,) = config["hooks"]["events"]["PreToolUse"]
        assert entry["matcher"] == "WebSearch|WebFetch"
        assert (tmp_vault.root / ".hyperresearch" / "hook.js").exists()

    def test_reminder_hook_merges_into_existing_config_idempotently(self, tmp_vault):
        import json

        zcode_dir = tmp_vault.root / ".zcode"
        zcode_dir.mkdir()
        (zcode_dir / "config.json").write_text(
            json.dumps(
                {
                    "webSearch": {"provider": "gemini"},
                    "hooks": {
                        "events": {
                            "PreToolUse": [
                                {"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}
                            ]
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        install_hooks(tmp_vault.root, "hpr", harnesses=["zcode"])
        install_hooks(tmp_vault.root, "hpr", harnesses=["zcode"])

        config = json.loads((zcode_dir / "config.json").read_text(encoding="utf-8"))
        assert config["webSearch"] == {"provider": "gemini"}
        matchers = [e["matcher"] for e in config["hooks"]["events"]["PreToolUse"]]
        assert matchers == ["Bash", "WebSearch|WebFetch"]

    def test_vault_pinned_selectors_reach_the_agents(self, tmp_vault):
        zcode = ZCODE.with_models({"opus": "bigmodel/glm-5.3$high"})
        install_hooks(tmp_vault.root, "hpr", harnesses=[zcode])
        patcher = (
            tmp_vault.root / ".zcode" / "agents" / "hyperresearch-patcher.md"
        ).read_text(encoding="utf-8")
        assert "model: bigmodel/glm-5.3$high" in patcher

    def test_global_install_lands_in_the_user_root(self, tmp_path):
        install_global_hooks(tmp_path, "hpr", harnesses=["zcode"])
        assert (tmp_path / ".zcode" / "skills" / "hyperresearch" / "SKILL.md").exists()
        assert (tmp_path / ".zcode" / "agents" / "hyperresearch-fetcher.md").exists()

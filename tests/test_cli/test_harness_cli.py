"""Tests for the harness-facing CLI surface: `--harness` and `hpr spawn`."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from hyperresearch.cli import app
from hyperresearch.cli import spawn as spawn_mod
from hyperresearch.core.config import VaultConfig
from hyperresearch.core.harnesses import get_harness

runner = CliRunner()


class TestInstallHarnessFlag:
    def test_explicit_harness_installs_that_layout_and_persists_it(self, tmp_vault, monkeypatch):
        monkeypatch.chdir(tmp_vault.root)
        result = runner.invoke(
            app, ["install", str(tmp_vault.root), "--harness", "omp", "--json"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["data"]["harnesses"] == ["omp"]

        assert (tmp_vault.root / ".omp" / "skills" / "hyperresearch" / "SKILL.md").exists()
        assert (tmp_vault.root / "AGENTS.md").exists()
        assert not (tmp_vault.root / ".claude").exists()

        # Persisted, so a later bare install keeps targeting omp.
        assert VaultConfig.load(tmp_vault.config_path).harness_targets == ["omp"]
        bare = runner.invoke(app, ["install", str(tmp_vault.root), "--json"])
        assert json.loads(bare.stdout)["data"]["harnesses"] == ["omp"]

    def test_comma_list_installs_both(self, tmp_vault, monkeypatch):
        monkeypatch.chdir(tmp_vault.root)
        result = runner.invoke(
            app, ["install", str(tmp_vault.root), "--harness", "claude,pi", "--json"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["data"]["harnesses"] == ["claude", "pi"]
        assert (tmp_vault.root / ".claude" / "agents").is_dir()
        assert (tmp_vault.root / ".pi" / "agents").is_dir()
        assert (tmp_vault.root / "CLAUDE.md").exists()
        assert (tmp_vault.root / "AGENTS.md").exists()

    def test_unknown_harness_fails_cleanly(self, tmp_vault, monkeypatch):
        monkeypatch.chdir(tmp_vault.root)
        result = runner.invoke(
            app, ["install", str(tmp_vault.root), "--harness", "cursor", "--json"]
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["error_code"] == "UNKNOWN_HARNESS"
        assert not (tmp_vault.root / ".cursor").exists()

    def test_fresh_project_gets_only_its_harness_context_file(self, tmp_path, monkeypatch):
        # Vault init used to write CLAUDE.md unconditionally, so an omp-only
        # install left a stray Claude Code context file in a fresh project.
        project = tmp_path / "fresh"
        project.mkdir()
        monkeypatch.chdir(project)
        result = runner.invoke(
            app, ["install", str(project), "--harness", "omp", "--json"]
        )
        assert result.exit_code == 0, result.output
        assert (project / "AGENTS.md").exists()
        assert not (project / "CLAUDE.md").exists()

    def test_steps_only_respects_the_harness(self, tmp_vault, monkeypatch):
        monkeypatch.chdir(tmp_vault.root)
        result = runner.invoke(
            app,
            ["install", str(tmp_vault.root), "--steps-only", "--harness", "pi", "--json"],
        )
        assert result.exit_code == 0, result.output
        assert (
            tmp_vault.root / ".pi" / "skills" / "hyperresearch-1-decompose" / "SKILL.md"
        ).exists()

    def test_vault_model_overrides_reach_the_installed_agents(self, tmp_vault, monkeypatch):
        monkeypatch.chdir(tmp_vault.root)
        config = VaultConfig.load(tmp_vault.config_path)
        config.harness_models = {"omp": {"opus": "zhipu-coding-plan/glm-5.3:high"}}
        config.save(tmp_vault.config_path)

        result = runner.invoke(
            app, ["install", str(tmp_vault.root), "--harness", "omp", "--json"]
        )
        assert result.exit_code == 0, result.output

        patcher = (
            tmp_vault.root / ".omp" / "agents" / "hyperresearch-patcher.md"
        ).read_text(encoding="utf-8")
        fetcher = (
            tmp_vault.root / ".omp" / "agents" / "hyperresearch-fetcher.md"
        ).read_text(encoding="utf-8")
        assert "model: zhipu-coding-plan/glm-5.3:high" in patcher
        # Untouched tier keeps the built-in selector.
        assert "model: zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash" in fetcher
        # And the override survives the config round-trip.
        assert VaultConfig.load(tmp_vault.config_path).harness_models == {
            "omp": {"opus": "zhipu-coding-plan/glm-5.3:high"}
        }


class TestSpawnBridge:
    """The bridge's observable contract is the child invocation it builds."""

    def _write_agent(self, root, frontmatter: str) -> None:
        agents = root / ".pi" / "agents"
        agents.mkdir(parents=True, exist_ok=True)
        (agents / "hyperresearch-fetcher.md").write_text(
            f"---\n{frontmatter}---\n\nYou are a fetcher.\n", encoding="utf-8"
        )

    def test_argv_carries_system_prompt_tools_and_task(self, tmp_path):
        self._write_agent(
            tmp_path, "name: hyperresearch-fetcher\ndescription: x\ntools: bash, read, write\n"
        )
        agent_file = tmp_path / ".pi" / "agents" / "hyperresearch-fetcher.md"
        meta, body = spawn_mod._split_frontmatter(agent_file.read_text(encoding="utf-8"))

        assert meta["tools"] == "bash, read, write"
        assert body == "You are a fetcher.\n"

        argv = spawn_mod._build_argv("pi", tmp_path / "p.md", "fetch these", meta, None)
        assert argv == [
            "pi",
            "-p",
            "--no-session",
            "--append-system-prompt",
            str(tmp_path / "p.md"),
            "--tools",
            "bash,read,write",
            "--",
            "fetch these",
        ]

    def test_model_override_wins_over_frontmatter(self, tmp_path):
        argv = spawn_mod._build_argv(
            "pi", tmp_path / "p.md", "x", {"model": "haiku"}, "openai/gpt-5-mini"
        )
        assert argv[argv.index("--model") + 1] == "openai/gpt-5-mini"

    def test_unknown_agent_lists_what_is_installed(self, tmp_path):
        self._write_agent(tmp_path, "name: hyperresearch-fetcher\ndescription: x\n")
        with pytest.raises(spawn_mod.SpawnError) as exc:
            spawn_mod._resolve_agent_file("hyperresearch-nope", tmp_path, get_harness("pi"))
        assert "hyperresearch-fetcher" in str(exc.value)

    def test_native_spawn_harness_is_rejected(self, tmp_path):
        with pytest.raises(spawn_mod.SpawnError) as exc:
            spawn_mod._bridge_harness("claude", tmp_path)
        assert "natively" in str(exc.value)

    def test_batch_reads_prompt_files_relative_to_the_project(self, tmp_path):
        (tmp_path / "spawn").mkdir()
        (tmp_path / "spawn" / "a.md").write_text("task A", encoding="utf-8")
        batch = tmp_path / "wave.json"
        batch.write_text(
            json.dumps(
                [
                    {"agent": "hyperresearch-fetcher", "prompt_file": "spawn/a.md"},
                    {"agent": "hyperresearch-loci-analyst", "prompt": "task B"},
                ]
            ),
            encoding="utf-8",
        )

        pairs = spawn_mod._load_batch(batch, tmp_path)
        assert pairs == [
            ("hyperresearch-fetcher", "task A"),
            ("hyperresearch-loci-analyst", "task B"),
        ]

    def test_batch_item_without_a_prompt_is_an_error(self, tmp_path):
        batch = tmp_path / "wave.json"
        batch.write_text(json.dumps([{"agent": "x"}]), encoding="utf-8")
        with pytest.raises(spawn_mod.SpawnError):
            spawn_mod._load_batch(batch, tmp_path)

    def test_missing_child_cli_is_reported_not_crashed(self, tmp_vault, monkeypatch):
        monkeypatch.setattr(spawn_mod.shutil, "which", lambda _name: None)
        monkeypatch.delenv("HPR_PI_BIN", raising=False)
        result = runner.invoke(
            app,
            [
                "spawn",
                "hyperresearch-fetcher",
                "--prompt",
                "x",
                "--harness",
                "pi",
                "--path",
                str(tmp_vault.root),
                "--json",
            ],
        )
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error_code"] == "SPAWN_ERROR"

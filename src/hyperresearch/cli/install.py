"""Install command — one-step setup: vault init + agent hooks + docs injection."""

from __future__ import annotations

from pathlib import Path

import typer

from hyperresearch.cli._output import console, output
from hyperresearch.models.output import error, success


def install(
    path: str = typer.Argument(".", help="Path to install in"),
    name: str = typer.Option("Research Base", "--name", "-n", help="Vault name"),
    json_output: bool = typer.Option(False, "--json", "-j", help="JSON output"),
    global_install: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Install the entry skill + agents into each harness's user-level dir (~/.claude/, ~/.omp/agent/, ~/.pi/agent/) so the pipeline is available in every session anywhere. Skips vault init, the context file, and the 18 step skills (those happen per-project on first run).",
    ),
    steps_only: bool = typer.Option(
        False,
        "--steps-only",
        help="Install only the 18 step skills into the harness's project skills dir. Used internally by the entry skill bootstrap on the first run in a project. Not normally invoked by users.",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Pipeline profile to render skill/agent prompts from (built-in gears: full, premier; plus any [profile.*] defined in .hyperresearch/config.toml). Defaults to the gear persisted by `hyperresearch profile use` (or 'full'). See `hyperresearch profile list`.",
    ),
    harness: list[str] | None = typer.Option(
        None,
        "--harness",
        "-H",
        help="Harnesses to install into: claude, omp, pi, or all (repeatable, comma-separated ok). Default: the vault's [harness] targets, else autodetected from the project and user config dirs. An explicit choice is persisted for later installs.",
    ),
) -> None:
    """Install hyperresearch: init vault + inject the context file + install harness skills/agents."""
    import sys

    from hyperresearch.cli._harness import (
        harness_ids,
        harness_labels,
        persist_harness_targets,
        resolve_cli_harnesses,
    )
    from hyperresearch.core.hooks import (
        install_global_hooks,
        install_hooks,
        install_step_skills,
    )
    from hyperresearch.core.profiles import ProfileError
    from hyperresearch.core.vault import Vault, VaultError

    # No explicit --profile → use the gear persisted by `hpr profile use`
    # in the target's config (falling back to "full").
    def _default_profile(config_path: Path | None) -> str:
        if profile is not None:
            return profile
        if config_path is not None and config_path.exists():
            from hyperresearch.core.config import VaultConfig

            return VaultConfig.load(config_path).pipeline_profile
        return "full"

    # Validate the profile early so a typo fails before any files are written.
    def _check_profile(resolved: str, config_path: Path | None) -> None:
        from hyperresearch.core.profiles import resolve_profile

        try:
            resolve_profile(resolved, config_path)
        except ProfileError as e:
            if json_output:
                output(error(str(e), "UNKNOWN_PROFILE"), json_mode=True)
            else:
                console.print(f"[red]Error:[/] {e}")
            raise typer.Exit(1)

    # Steps-only path: lazy install of the 18 step skills into the harness's
    # project skills dir. Called by the entry skill's bootstrap on the first
    # run in a project (after a global install). Cheap no-op on subsequent
    # invocations.
    if steps_only:
        target = Path(path).resolve()
        steps_config = target / ".hyperresearch" / "config.toml"
        steps_config_path = steps_config if steps_config.exists() else None
        steps_profile = _default_profile(steps_config_path)
        _check_profile(steps_profile, steps_config_path)
        targets = resolve_cli_harnesses(
            harness, root=target, config_path=steps_config_path, json_output=json_output
        )
        actions = install_step_skills(target, profile=steps_profile, harnesses=targets)
        dirs = ", ".join(f"{target}/{h.skills_rel}" for h in targets)
        if json_output:
            output(
                success(
                    {
                        "steps_installed": actions,
                        "target": str(target),
                        "harnesses": harness_ids(targets),
                    },
                    vault=None,
                ),
                json_mode=True,
            )
            return
        if actions:
            console.print(f"[green]Step skills installed:[/] {dirs}")
            for action in actions:
                console.print(f"  {action}")
        else:
            console.print(f"[dim]Step skills already installed at {dirs}[/]")
        return

    # Global install path: only the user-level entry skill + agents. No vault,
    # no context file, no step skills — pure "make the pipeline available
    # everywhere" mode. Step skills install per-project, lazily, when the
    # entry skill bootstrap calls `hyperresearch install --steps-only .` on
    # the first run there.
    if global_install:
        from hyperresearch.core.agent_docs import _resolve_executable

        hpr_path = _resolve_executable()
        home = Path.home()
        global_profile = profile if profile is not None else "full"
        _check_profile(global_profile, None)
        targets = resolve_cli_harnesses(harness, json_output=json_output)
        hook_actions = install_global_hooks(
            home, hpr_path=hpr_path, profile=global_profile, harnesses=targets
        )

        if json_output:
            output(
                success(
                    {
                        "global": True,
                        "home": str(home),
                        "hooks_installed": hook_actions,
                        "harnesses": harness_ids(targets),
                    },
                    vault=None,
                ),
                json_mode=True,
            )
            return

        roots = ", ".join(str(h.global_root(home)) for h in targets)
        console.print(f"[green]Global install ({harness_labels(targets)}):[/] {roots}")
        if hook_actions:
            for action in hook_actions:
                console.print(f"  {action}")
        else:
            console.print("[dim]All skills and agents already installed.[/]")
        commands = ", ".join(sorted({h.invoke_command for h in targets}))
        console.print(
            f"\n[bold]Ready.[/] {commands} is now available in every session."
        )
        console.print(
            "[dim]On the first run in a project, the vault, research/ folder, "
            "and the 18 step skills are created in that project.[/]"
        )
        return

    root = Path(path).resolve()

    # First-time install in an interactive terminal → run the setup TUI instead
    is_new = not (root / ".hyperresearch").exists()
    is_interactive = not json_output and sys.stdin.isatty()
    if is_new and is_interactive:
        from hyperresearch.cli.setup import setup

        setup(path=path, json_output=False, harness=harness)
        return

    # Step 1: Resolve the harnesses first — the vault's context file depends
    # on them, so a `--harness omp` install must not leave a stray CLAUDE.md.
    from hyperresearch.core.agent_docs import _resolve_executable, inject_agent_docs

    project_config = root / ".hyperresearch" / "config.toml"
    project_config_path = project_config if project_config.exists() else None
    targets = resolve_cli_harnesses(
        harness, root=root, config_path=project_config_path, json_output=json_output
    )

    # Step 2: Init vault (skip if already exists)
    try:
        vault = Vault.discover(root)
        vault_action = "existing"
    except VaultError:
        try:
            vault = Vault.init(root, name=name, harnesses=targets)
            vault_action = "created"
        except VaultError as e:
            if json_output:
                output(error(str(e), "INIT_ERROR"), json_mode=True)
            else:
                console.print(f"[red]Error:[/] {e}")
            raise typer.Exit(1)

    # Step 3: Persist an explicit harness choice so later bare installs keep
    # targeting the same ones, and resolve the hyperresearch executable path.
    persist_harness_targets(vault.config_path, harness)
    hpr_path = _resolve_executable()

    # Step 4: Always re-inject each harness's context file (blurb + CLI path)
    doc_actions = inject_agent_docs(root, harnesses=targets)

    # Step 5: Install each harness's skills + subagents + reminder (rendered
    # from the gear profile — explicit --profile, else the gear in config)
    profile_config_path = vault.config_path if vault.config_path.exists() else None
    project_profile = _default_profile(profile_config_path)
    _check_profile(project_profile, profile_config_path)
    hook_actions = install_hooks(
        root, hpr_path=hpr_path, profile=project_profile, harnesses=targets
    )

    # Step 6: Auto-configure crawl4ai if installed
    crawl4ai_status = _setup_crawl4ai(vault)

    # Step 7: Report
    data = {
        "vault_path": str(vault.root),
        "vault": vault_action,
        "harnesses": harness_ids(targets),
        "agent_docs": doc_actions,
        "hooks_installed": hook_actions,
        "crawl4ai": crawl4ai_status,
    }

    if json_output:
        output(success(data, vault=str(vault.root)), json_mode=True)
    else:
        if vault_action == "created":
            console.print(f"[green]Vault created:[/] {vault.root}")
        else:
            console.print(f"[dim]Vault exists:[/] {vault.root}")

        console.print(f"[green]Harnesses:[/] {harness_labels(targets)}")

        if doc_actions:
            console.print("[green]Agent docs:[/]")
            for action in doc_actions:
                console.print(f"  {action}")

        if hook_actions:
            console.print("[green]Hooks installed:[/]")
            for action in hook_actions:
                console.print(f"  {action}")
        else:
            console.print("[dim]All hooks already installed.[/]")

        if crawl4ai_status == "configured":
            console.print("[green]crawl4ai:[/] detected, set as default provider + browser ready")
        elif crawl4ai_status == "browser_installed":
            console.print("[green]crawl4ai:[/] browser installed + set as default provider")
        elif crawl4ai_status == "not_installed":
            console.print(
                "[dim]crawl4ai:[/] not installed. "
                "For local headless browsing: pip install hyperresearch[crawl4ai]"
            )

        console.print("\n[bold]Ready.[/] Agents will now check the research base before web searches.")
        console.print("[dim]Tip: Run 'hyperresearch setup' for interactive configuration (profile, stealth, etc.)[/]")


def _setup_crawl4ai(vault) -> str:
    """Detect crawl4ai, install browser if needed, set as default provider.

    Returns: 'configured' (already ready), 'browser_installed' (just set up),
             'not_installed' (crawl4ai not available).
    """
    try:
        import crawl4ai  # noqa: F401
    except ImportError:
        return "not_installed"

    # Set crawl4ai as the default provider if still on builtin
    if vault.config.web_provider == "builtin":
        vault.config.web_provider = "crawl4ai"
        vault.config.save(vault.config_path)

    # Check if the browser is already installed -- against the stack the
    # provider will actually launch: patchright (stealth adapter) pins its
    # own chromium build, so a passing plain-playwright check can mask a
    # missing patchright browser.
    try:
        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        browser.close()
        pw.stop()
        return "configured"
    except Exception:
        pass

    # Try to install the browser. The stealth (patchright) adapter pins its
    # OWN chromium build with a separate registry -- `playwright install`
    # does not provide it, and the provider then dies at launch with a
    # missing-executable error. Install patchright's browser when patchright
    # is present; plain playwright is the fallback for non-stealth setups.
    import importlib.util
    import subprocess
    import sys

    installers = []
    if importlib.util.find_spec("patchright") is not None:
        installers.append("patchright")
    installers.append("playwright")
    for mod in installers:
        try:
            subprocess.run(
                [sys.executable, "-m", mod, "install", "chromium"],
                check=True,
                capture_output=True,
            )
            return "browser_installed"
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return "configured"  # best effort — user can install manually

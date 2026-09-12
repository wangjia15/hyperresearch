"""Harness selection shared by the installing commands.

`--harness` is accepted by `install`, `setup`, and `repair`. Resolution order
is explicit flag > the vault's `[harness] targets` > autodetection. An
explicit choice is persisted so later bare runs keep targeting the same
harnesses.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import typer

from hyperresearch.cli._output import console, output
from hyperresearch.core.harnesses import (
    Harness,
    HarnessError,
    parse_harness_ids,
    resolve_harnesses,
)
from hyperresearch.models.output import error


def resolve_cli_harnesses(
    selected: Sequence[str] | None,
    root: Path | None = None,
    config_path: Path | None = None,
    json_output: bool = False,
    home: Path | None = None,
) -> tuple[Harness, ...]:
    """Resolve `--harness` values, exiting cleanly on an unknown harness id.

    Vault `[harness.models.<id>]` overrides are applied to the resolved
    harnesses, so a project can pin the model selectors its harness spawns
    agents with.
    """
    configured: list[str] = []
    model_overrides: dict = {}
    if config_path is not None and config_path.exists():
        from hyperresearch.core.config import VaultConfig

        config = VaultConfig.load(config_path)
        configured = list(config.harness_targets)
        model_overrides = config.harness_models

    try:
        resolved = resolve_harnesses(
            selected=selected,
            configured=configured,
            root=root,
            home=home,
        )
    except HarnessError as exc:
        if json_output:
            output(error(str(exc), "UNKNOWN_HARNESS"), json_mode=True)
        else:
            console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1)

    return tuple(h.with_models(model_overrides.get(h.id)) for h in resolved)


def persist_harness_targets(
    config_path: Path | None,
    selected: Sequence[str] | None,
) -> None:
    """Persist an explicit `--harness` choice into the vault config."""
    ids = parse_harness_ids(selected)
    if not ids or config_path is None:
        return
    from hyperresearch.core.config import VaultConfig

    config = VaultConfig.load(config_path)
    if list(config.harness_targets) == list(ids):
        return
    config.harness_targets = list(ids)
    config.save(config_path)


def harness_labels(harnesses: Sequence[Harness]) -> str:
    """Human-readable harness list: `Claude Code, OMP`."""
    return ", ".join(h.label for h in harnesses)


def harness_ids(harnesses: Sequence[Harness]) -> list[str]:
    return [h.id for h in harnesses]

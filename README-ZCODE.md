# hyperresearch on ZCode

How the pipeline is installed, invoked, and — the point of this document — **which model every step runs on** when the harness is [ZCode](https://github.com/wangjia15/ZCode) (Z.ai's coding agent).

Released package:

```bash
pip install hyperresearch
hyperresearch install --harness zcode     # writes .zcode/, AGENTS.md, .hyperresearch/
```

Current development version instead of the published package: see
[README → Install the current development version](README.md#install-the-current-development-version) —
clone, venv, `pip install -e .`, then run that interpreter's `hyperresearch`
entry point with the same `install --harness zcode` command.

Then `/skill hyperresearch <your query>` (or just ask for deep research — the
entry skill's description triggers it) in a ZCode session started in that project.

---

## What the install writes

| Path | What it is |
|---|---|
| `.zcode/skills/hyperresearch/SKILL.md` | Entry skill — the router. Rendered for ZCode: `Skill(skill: …)` loads, `subagent_type:` spawn blocks, one-message Task waves. |
| `.zcode/skills/hyperresearch-*/SKILL.md` | The 18 step procedures, loaded on demand with the `Skill` tool. |
| `.zcode/agents/hyperresearch-*.md` | 15 task agents, discovered from workspace `.zcode/agents/` (frontmatter: `name`, `description`, `tools`, `model`, `color`). |
| `.zcode/config.json` | The web-search reminder: a `PreToolUse` entry on `WebSearch\|WebFetch` plus `hooks.enabled: true` — config-file hooks are off without it. Existing keys are preserved. |
| `.hyperresearch/hook.js` | The script that entry runs (same one Claude Code uses); it walks up from the session cwd looking for `.hyperresearch/` and prints the reminder as `additionalContext`. |
| `AGENTS.md` | Project context file (the blurb lives between `<!-- hyperresearch:start -->` markers; the rest of the file is yours). |
| `.hyperresearch/` | The vault: SQLite index, config, run manifests. |

Global variant: `hyperresearch install --harness zcode --global` writes the entry skill + agents to `~/.zcode/`. Step skills stay per-project (18 globally-advertised skills would cost every unrelated session system-reminder noise); the entry skill's bootstrap runs `hyperresearch install --steps-only . --harness zcode` on the first run in a project.

---

## Model per step

Two models cover the whole pipeline — both ZCode built-ins:

- **`glm-5.3-flash`** — reading volume. Fetching, extraction, per-locus investigation, citation verification. Many parallel instances, large inputs, mechanical-to-moderate judgment.
- **`glm-5.3`** — writing and judgment. Drafting, synthesis, adversarial critique, patching, polish. Few instances, load-bearing output.

ZCode agent selectors are **`provider/model$level`**, and the provider id is whichever coding plan you logged into (`bigmodel`, `zai`, …). The install reads your own `~/.zcode/cli/config.json` — `model.main` names the live provider, the provider block gives the exact model-id casing and the reasoning levels it offers — and writes `<provider>/glm-5.3-flash$low` for the reading tiers and `<provider>/glm-5.3$high` for judgment. The `$level` suffix is not optional: ZCode's model registry rejects a reasoning-capable model with no level.

| Step | Runs as | Instances | Model |
|---|---|---|---|
| 1 Decompose | orchestrator | — | your session model |
| 1.5 Chapter partition (dissertation) | orchestrator | — | your session model |
| 2 Width sweep | `hyperresearch-fetcher` | 10–12 per wave (`full`), 14–18 (`premier`) | `<provider>/glm-5.3-flash$low` |
| 2 · long-source reads | `hyperresearch-source-analyst` | on demand, 1 per source | `<provider>/glm-5.3-flash$low` |
| 2.8 Escalation drain | — (no browser lane on ZCode) | — | blocked fetches stay queued |
| 3 Contradiction graph | orchestrator | — | your session model |
| 4 Loci analysis | `hyperresearch-loci-analyst` | 2 (`full`), 3 (`premier`) | `<provider>/glm-5.3-flash$low` |
| 5 Depth investigation | `hyperresearch-depth-investigator` | up to 6 (`full`), 10 (`premier`) | `<provider>/glm-5.3-flash$low` |
| 6 Cross-locus reconcile | orchestrator | — | your session model |
| 7 Source tensions | orchestrator | — | your session model |
| 8 Corpus critic | `hyperresearch-corpus-critic` + gap-fill `hyperresearch-fetcher` | 1 + N | `<provider>/glm-5.3-flash$low` |
| 9 Evidence digest | orchestrator | — | your session model |
| 10 Triple draft | `hyperresearch-draft-orchestrator` | 3, parallel | `<provider>/glm-5.3$high` |
| 11 Synthesize | `hyperresearch-synthesizer` | 1 (two-pass) | `<provider>/glm-5.3$high` |
| 12 Critics | `hyperresearch-{dialectic,depth,width,instruction}-critic` | 4, parallel | `<provider>/glm-5.3$high` |
| 13 Gap fetch | `hyperresearch-fetcher` | N | `<provider>/glm-5.3-flash$low` |
| 14 Patcher | `hyperresearch-patcher` | 1 | `<provider>/glm-5.3$high` |
| 14.5 Cite check | `hyperresearch-cite-checker` (+ second patcher pass) | 1–2 | `<provider>/glm-5.3-flash$low` |
| 15 Polish | `hyperresearch-polish-auditor` | 1 | `<provider>/glm-5.3$high` |
| 16 Readability audit | `hyperresearch-readability-recommender` | 1 | `<provider>/glm-5.3$high` |

Steps marked "orchestrator" have no subagent: the ZCode session you started does that work itself, on whatever model that session is running.

The **scale gear** (`hyperresearch profile use full|premier`) changes source targets, wave sizes, and budgets — **not** the models.

### Check what is actually installed

```bash
grep -H '^model:' .zcode/agents/hyperresearch-*.md
```

No `model:` lines at all means detection found nothing usable and every agent inherits the session model — the pipeline still runs, one tier poorer.

---

## Changing the models

Per vault, in `.hyperresearch/config.toml` — applied on the next install. A pin always beats autodetection:

```toml
[harness.models.zcode]
sonnet = "bigmodel/glm-5.3-flash$low"   # the reading tier
opus   = "bigmodel/glm-5.3$high"        # the judgment tier
```

```bash
hyperresearch install --harness zcode    # re-render the agent files
```

Keys are the profile's tier aliases (`haiku`, `sonnet`, `opus`), not agent names. Values are `provider/model` plus a `$level` suffix when the model is reasoning-capable (`low`, `high`, `max` — GLM-5.3 offers all three). An **empty value omits the `model:` line entirely**, so that tier inherits the parent session's model.

Worth knowing before you tune:

- **The cheap tier carries the comprehension load.** `hyperresearch-depth-investigator` and `hyperresearch-cite-checker` sit in the reading tier because that is where token volume lives, but both do real judgment. If reports come back with thin depth sections or sloppy citation verdicts, move that tier up: `sonnet = "bigmodel/glm-5.3$low"`.
- **`$max` on everything is tempting and slow.** The judgment tier defaults to `$high`, not `$max`, for wall-time; raise it per vault when quality matters more than latency.
- **There is no fallback-chain form.** Unlike OMP, a ZCode selector names exactly one provider. An unresolvable one fails the spawn, which is why detection prefers inheriting over guessing.

---

## ZCode-specific behavior

- **Skill loading.** `Skill(skill: "hyperresearch-N-…")` — ZCode has the same tool as Claude Code. Skills are also auto-triggered by their descriptions.
- **Spawning.** The `Task` tool with `subagent_type:` — issue all N calls of a wave in **one message**; that is what makes them concurrent.
- **Web search.** `WebSearch` is ZCode's own tool. To run it on `gemini-2.5-flash` (Google Search grounding) instead of the active model's native search, set it once in `~/.zcode/cli/config.json` — user scope only, a workspace config cannot set it, and the key comes from `GEMINI_API_KEY` / `GOOGLE_API_KEY`:
  ```json
  { "webSearch": { "provider": "gemini", "gemini": { "model": "gemini-2.5-flash" } } }
  ```
  This is a ZCode setting; `hyperresearch install` does not write it.
- **Browser lane: none.** ZCode's browser-use plugin drives the browser from the main agent only, not from spawned subagents — so `hyperresearch-browser-fetcher` is not installed, and blocked fetches (login walls, CAPTCHAs, bot walls) stay queued as escalations for you to drain by hand: `hyperresearch escalation list --status queued -j`.
- **Agent frontmatter is flattened.** ZCode's agent parser reads no YAML block scalars (`description: >` would load as the literal `>`), so installed agent files carry single-line descriptions. Skills keep their folded descriptions — the skill parser accepts them, and all stay under its 1024-character limit.
- **The reminder hook needs `hooks.enabled: true`.** ZCode config-file hooks are disabled by default; the install sets the flag while merging its `PreToolUse` entry, and leaves every other key in `.zcode/config.json` alone.
- **Tool names** in the installed agents are ZCode's own: `Bash, Read, Write, Edit, Glob, Grep, Task, WebSearch, TodoWrite`.

---

## Troubleshooting

**Skills or agents not offered.** Start ZCode from the project root — `.zcode/skills` and `.zcode/agents` are workspace-scoped. Verify discovery:

```bash
zcode skills list | grep -c hyperresearch   # 19: entry skill + 18 steps
ls .zcode/agents | wc -l                    # 15 agents (no browser-fetcher by design)
```

**"Provider Registry 中不存在 Provider: X".** An agent's `model:` names a provider you are not logged into. Either pin the tier to your provider — `[harness.models.zcode] opus = "<your provider>/glm-5.3$high"` — or re-run `hyperresearch install --harness zcode` so detection re-reads `~/.zcode/cli/config.json`.

**"Reasoning level is required for …".** The selector lost its `$level` suffix. Fix it the same way: pin the tier with the suffix, or re-run install.

**`model:` lines missing after install.** Detection found no usable `model.main`/provider pair (fresh login, custom provider without a `/`, provider missing one of the two GLM tiers). Agents inherit the session model; pin the tiers to pin the models.

**Web-search reminder missing.** Config-file hooks are off unless enabled. Check `.zcode/config.json` has `"hooks": { "enabled": true, … }` with the `PreToolUse` entry, and that `.hyperresearch/hook.js` exists (re-run install to restore both).

**Spawns failing with 429 / retry-after.** The coding-plan quota is exhausted (the session itself keeps working on whatever it has left). Wait out the window and resume: `hyperresearch run resume <vault_tag> -j`.

**Blocked fetches piling up.** `hyperresearch escalation list --status queued -j`. On ZCode there is no browser-fetcher to drain them — open the URLs yourself, then `hyperresearch fetch "<url>" --tag <topic> -j` and continue the run.

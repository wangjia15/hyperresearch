# hyperresearch on OMP

How the pipeline is installed, invoked, and — the point of this document — **which model every step runs on** when the harness is OMP.

Released package:

```bash
pip install hyperresearch
hyperresearch install --harness omp       # writes .omp/, AGENTS.md, .hyperresearch/
```

Current development version instead of the published package: see
[README → Install the current development version](README.md#install-the-current-development-version) —
clone, venv, `pip install -e .`, then run that interpreter's `hyperresearch`
entry point with the same `install --harness omp` command.

Then `/skill:hyperresearch <your query>` in an OMP session started in that project.

---

## What the install writes

| Path | What it is |
|---|---|
| `.omp/skills/hyperresearch/SKILL.md` | Entry skill — the router. Rendered for OMP: `skill://` loads, `agent:` spawn blocks, `task`-tool wave mechanics. |
| `.omp/skills/hyperresearch-*/SKILL.md` | The 18 step procedures, loaded on demand with `read skill://hyperresearch-N-…`. |
| `.omp/agents/hyperresearch-*.md` | 16 task agents, discovered by OMP's task subsystem (`name`, `description`, `tools`, `model` frontmatter). |
| `.omp/extensions/hyperresearch/index.ts` | Reminder extension: appends "check the research base first" to every `web_search` result. |
| `AGENTS.md` | Project context file (the blurb lives between `<!-- hyperresearch:start -->` markers; the rest of the file is yours). |
| `.hyperresearch/` | The vault: SQLite index, config, run manifests. |

Global variant: `hyperresearch install --harness omp --global` writes the entry skill + agents to `~/.omp/agent/`. Step skills stay per-project (18 globally-advertised skills would cost every unrelated session ~3K tokens of system-reminder noise); the entry skill's bootstrap runs `hyperresearch install --steps-only . --harness omp` on the first run in a project.

---

## Model per step

Two models cover the whole pipeline:

- **`glm-5.3-flash`** — reading volume. Fetching, extraction, per-locus investigation, citation verification. Many parallel instances, large inputs, mechanical-to-moderate judgment.
- **`glm-5.3`** — writing and judgment. Drafting, synthesis, adversarial critique, patching, polish. Few instances, load-bearing output.

Every selector is a **fallback chain**: OMP tries the entries left to right and drops back to the parent session's model if none resolves. That is why the Zhipu coding plan comes first and the direct `zai` endpoint second — a machine with only one of those credentials still runs, and a machine with neither degrades to whatever model the session is already on instead of failing the spawn.

| Step | Runs as | Instances | Model |
|---|---|---|---|
| 1 Decompose | orchestrator | — | your session model |
| 1.5 Chapter partition (dissertation) | orchestrator | — | your session model |
| 2 Width sweep | `hyperresearch-fetcher` | 10–12 per wave (`full`), 14–18 (`premier`) | `zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash` |
| 2 · long-source reads | `hyperresearch-source-analyst` | on demand, 1 per source | `zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash` |
| 2.8 Escalation drain | `hyperresearch-browser-fetcher` | exactly 1 (one browser) | `zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash` |
| 3 Contradiction graph | orchestrator | — | your session model |
| 4 Loci analysis | `hyperresearch-loci-analyst` | 2 (`full`), 3 (`premier`) | `zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash` |
| 5 Depth investigation | `hyperresearch-depth-investigator` | up to 6 (`full`), 10 (`premier`) | `zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash` |
| 6 Cross-locus reconcile | orchestrator | — | your session model |
| 7 Source tensions | orchestrator | — | your session model |
| 8 Corpus critic | `hyperresearch-corpus-critic` + gap-fill `hyperresearch-fetcher` | 1 + N | `zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash` |
| 9 Evidence digest | orchestrator | — | your session model |
| 10 Triple draft | `hyperresearch-draft-orchestrator` | 3, parallel | `zhipu-coding-plan/glm-5.3, zai/glm-5.3` |
| 11 Synthesize | `hyperresearch-synthesizer` | 1 (two-pass) | `zhipu-coding-plan/glm-5.3, zai/glm-5.3` |
| 12 Critics | `hyperresearch-{dialectic,depth,width,instruction}-critic` | 4, parallel | `zhipu-coding-plan/glm-5.3, zai/glm-5.3` |
| 13 Gap fetch | `hyperresearch-fetcher` | N | `zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash` |
| 14 Patcher | `hyperresearch-patcher` | 1 | `zhipu-coding-plan/glm-5.3, zai/glm-5.3` |
| 14.5 Cite check | `hyperresearch-cite-checker` (+ second patcher pass) | 1–2 | `zhipu-coding-plan/glm-5.3-flash, zai/glm-5.3-flash` |
| 15 Polish | `hyperresearch-polish-auditor` | 1 | `zhipu-coding-plan/glm-5.3, zai/glm-5.3` |
| 16 Readability audit | `hyperresearch-readability-recommender` | 1 | `zhipu-coding-plan/glm-5.3, zai/glm-5.3` |

Steps marked "orchestrator" have no subagent: the OMP session you started does that work itself, on whatever model that session is running.

The **scale gear** (`hyperresearch profile use full|premier`) changes source targets, wave sizes, and budgets — **not** the models. `full`, `premier`, `light` and `dissertation` all share the same tier assignment above.

### Check what is actually installed

```bash
grep -H '^model:' .omp/agents/hyperresearch-*.md
```

---

## Changing the models

Per vault, in `.hyperresearch/config.toml` — the table is round-tripped on every config save and applied on the next install:

```toml
[harness.models.omp]
sonnet = "zhipu-coding-plan/glm-5.3-flash"   # the reading tier
opus   = "zhipu-coding-plan/glm-5.3:high"    # the judgment tier, forced high thinking
```

```bash
hyperresearch install --harness omp    # re-render the agent files
```

Keys are the profile's tier aliases (`haiku`, `sonnet`, `opus`), not agent names. Values are anything OMP accepts as a model selector: `provider/model`, a comma-separated fallback chain, a role alias such as `"@slow"`, optionally with a thinking suffix (`:low`, `:high`, `:max`). An **empty value omits the `model:` line entirely**, so that tier inherits the parent session's model.

Worth knowing before you tune:

- **The cheap tier carries the comprehension load.** `hyperresearch-depth-investigator` and `hyperresearch-cite-checker` sit in the reading tier because that is where token volume lives, but both do real judgment. If reports come back with thin depth sections or sloppy citation verdicts, move that tier up: `sonnet = "zhipu-coding-plan/glm-5.3"`.
- **Per-agent, not per-tier, overrides belong to OMP.** `task.agentModelOverrides` in `~/.omp/agent/config.yml` (or `.omp/config.yml`) takes precedence over the frontmatter, so you can pin one agent without moving its whole tier:
  ```yaml
  task:
    agentModelOverrides:
      hyperresearch-cite-checker: zhipu-coding-plan/glm-5.3
  ```
- **Role aliases work too.** `opus = "@slow"` routes through your own `modelRoles.slow`, which is the right move if you already curate roles in OMP.

---

## OMP-specific behavior

- **Skill loading.** `read skill://hyperresearch-N-…`. OMP has no `Skill` tool; the entry skill instructs `read` against the skill protocol.
- **Spawning.** One `task` call with every item in `tasks[]` — that is what makes a wave concurrent. The spawn blocks in the step skills name the agent with `agent:` (not `subagent_type:`).
- **Browser lane.** `hyperresearch-browser-fetcher` drives your real Chrome through the `eval` tool's browser prelude (`browser.open({ app: { relay: true } })`), one tab at a time, and never solves CAPTCHAs/logins/2FA — those become `needs_human` and are consolidated into one prompt for you.
- **Web reminder.** The extension appends the vault reminder to `web_search` results instead of Claude Code's PreToolUse hook, because OMP has no pre-tool injection channel. Same text, one turn later.
- **Tool names** in the installed agents are OMP's own: `bash, read, write, edit, grep, glob, task, web_search, todo`, plus `eval` for the browser lane.

---

## Troubleshooting

**Skills or agents not offered.** Start OMP from the project root — `.omp/skills` and `.omp/agents` are cwd-scoped. Verify discovery:

```bash
ls .omp/skills | head        # 19 dirs: entry skill + 18 steps
ls .omp/agents | wc -l       # 16 agents
```

**A spawn runs on the wrong model.** Precedence is `task.agentModelOverrides` → agent frontmatter chain → parent session model. Check the first two; an unresolvable chain silently falls through to the third.

**401 from a GLM selector.** The chain's providers are not authenticated. Either log in to that provider in OMP or point the tier at one you have: `[harness.models.omp] sonnet = "<your provider>/<model>"`.

**`web_search` reminder missing.** The extension is project-scoped; confirm `.omp/extensions/hyperresearch/index.ts` exists and OMP was not started with `--no-extensions`.

**Blocked fetches piling up.** `hyperresearch escalation list --status queued -j`. Step 2.8 drains them with one browser-fetcher; CAPTCHA/login items are yours by design.

# Model Researcher (heimdall issue #9)

Weekly LLM catalog researcher/scout that lived only on VPS disk at
`/root/workspace/model-researcher/` with no git anywhere — a disk loss would
silently reset the new-model diff baseline (`state.json`). This directory is
the git home for its code; the VPS copy stays the live, running copy.

## Files

- `fetch_models_v2.py` — pulls the current OpenRouter model catalog and
  computes what changed since the last run (new/removed/repriced models).
- `model_scout.py` — filters/ranks models (e.g. `value_leaders_table`: top
  intelligence-per-dollar) and renders the markdown tables the weekly
  briefing embeds.
- `sync_models_to_hermes.py` — pushes the curated model list into the local
  LightLLM proxy config Hermes routes through (`127.0.0.1:4000`), restarting
  it so the new list is live.
- `fix_categorization.py` — model categorization + cost-lookup helpers,
  imported by `fetch_models_v2.py` (`get_model_cost`, `categorize_model`).
- `state.json` — the diff baseline: what the researcher already knows about
  as of the last run. See `backup-state.sh` below.
- `output/latest-briefing.md` — one sample of the generated weekly briefing,
  committed for reference. The VPS accumulates many more of these
  (`model-data-*.json`, `model-comparison-*.md`, etc.); those are gitignored,
  see below.

## Where it runs

Host: **openclaw-vps**, path `/root/workspace/model-researcher/`. To update the
live code, `scp` the changed `.py` file(s) over the VPS copy — there's no
deploy script yet (unlike `bridge/deploy.sh`) because these run as one-shot
Hermes cron jobs, not a long-lived daemon; the next scheduled tick just picks
up the new file.

## Hermes cron jobs that run this code

Defined in `/root/.hermes/cron/jobs.json` (see the redacted snapshot at
`../hermes/cron/jobs.json`). Two jobs drive this code:

- **`model-researcher-weekly`** (id `38883e4027a5`) — Mondays 06:00. Runs
  `python3 fetch_models_v2.py`, then has the Hermes agent turn the output into
  a 6-section weekly briefing (new models, models to retire, humanizer
  scores, recommended stack, cost tiers, value leaders) and publish it to the
  Outline "Model Researcher Reports" collection. Also reads
  `output/value-leaders-YYYY-MM-DD.md` (from `model_scout.py`'s
  `value_leaders_table`) for the value-leaders section.
- **`Light LLM Discovery Sync`** (id `458f2e0778e5`) — Mondays 08:00, after
  the researcher job. Runs `python3 sync_models_to_hermes.py`, verifies the
  LightLLM proxy restarted (`ss -tlnp | grep 4000`), and reports the new
  model/tier counts. Told to stop and report rather than self-repair if the
  sync script fails.

A third job, `global-content-model-routing-review` (id `0cb5ce8274c2`, Mondays
06:30), runs immediately after the researcher scan but operates on a separate
control plane (`content-writing-system/model-routing.yaml`) — it consumes the
researcher's output but isn't part of this code.

## state.json backup strategy

`state.json` is the researcher's only memory of what it's already seen; losing
it just means the next run's "NEW MODELS" section re-announces everything,
which is annoying but not destructive — still worth not losing.

`backup-state.sh` copies the live `state.json` from the VPS into this
directory. Run it:

- **Manually** after any run you care about preserving:
  `vps/model-researcher/backup-state.sh`
- **Or fold into the existing weekly job** — add a step to
  `model-researcher-weekly`'s prompt (or a cron step) that scp's the file to
  a path this repo can pick up, if you want it fully hands-off. Not wired up
  yet; manual is fine for now given the low blast radius of losing it.

Commit the updated `state.json` after running the backup script so history is
preserved in git, not just overwritten in place.

## What's NOT committed

`output/` is gitignored except `latest-briefing.md` (kept as one sample of
the generated report format). The VPS's `output/` directory accumulates dated
JSON/markdown artifacts every run (`model-data-YYYY-MM-DD.json`,
`model-comparison-*.md`, etc.) — those are regenerable from `state.json` +
the OpenRouter API and aren't worth the repo bloat.

Two unrelated sibling directories on the VPS
(`/root/workspace/model-researcher/content-writing/`,
`/root/workspace/model-researcher/vrp-routing/`) are out of scope for this
issue and were not copied here.

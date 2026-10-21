---
name: eval-charts
description: >-
  Generates LLM and agent benchmark evaluation charts and token/tool usage summaries (`token_usage_summary.csv`,
  `token_usage_summary.json`) from `results.jsonl` benchmark runs using the `altair-charts` skill.
  Use this skill when the user wants to analyze, summarize, or plot agent/model benchmark evaluation
  results across tasks (`case_id`), models, and effort tiers (`low`, `medium`, `high`), including
  stacked token breakdowns (`Cached Input`, `Input`, `Output`), stacked tool invocation breakdowns,
  cost (`cost_usd`), duration (`duration_sec`), turns (`num_turns`), or pass rates (`pass_rate`).
---

# Benchmark Evaluation Visualization Skill (`eval-charts`)

## Overview

The `eval-charts` skill automates the aggregation and visualization of agent/LLM benchmark evaluation datasets (`results.jsonl`), building directly on the generic [altair-charts](../altair-charts/SKILL.md) skill ([chart_theme.py](../altair-charts/scripts/chart_theme.py) and [generate_bar_charts.py](../altair-charts/scripts/generate_bar_charts.py)).

For any benchmark evaluation dataset, [generate_eval_charts.py](./scripts/generate_eval_charts.py) produces:
1. **Token & Tool Usage Summary Exports (`token_usage_summary.csv`, `token_usage_summary.json`)**:
   - Always exports the aggregated token breakdown (`cache_read_tokens`, `input_tokens`, `output_tokens`, `total_tokens`), per-tool invocation breakdown (`tool__<name>`), `cost_usd`, `duration_sec`, `tool_invocations`, `run_count`, and `pass_rate` per `(case_id, base_model, effort)`.
2. **Per-Task Grouped & Stacked Bar Charts (`<case_id>_<metric>.{png,svg,html}` & `<case_id>/<case_id>_<metric>.{png,svg,html}`)**:
   - **Averaged Across Repetitions**: Averages individual runs (`run 1`, `run 2`, etc.) per `(case_id, base_model, effort)` combination.
   - **One Column Group per Model**: Groups columns by base model (`Gemini 3.8 Flash`, `Gemini 4 Argon`, `Gemini 3.1 Pro`, or any dynamically discovered model).
   - **Bars per Effort Tier (`Low`, `Medium`, `High`, ...)**: Places effort tiers side-by-side along the X-axis within each model group.
   - **Supported Metrics (`--metrics`, defaults to `all`)**:
     - `tokens` *(always included)*: Stacks `Cached Input` (`cache_read_tokens`), `Input` (`input_tokens`), and `Output` (`output_tokens`) within each effort bar using a light-to-dark shade scale per model palette.
     - `tools`: Stacks average tool invocations by tool kind (`run_command`, `view_file`, `replace_file_content`, `write_to_file`, etc.) within each effort bar.
     - `cost` / `duration` / `turns` / `pass_rate`: Non-stacked solid bars comparing financial cost ($ USD), execution duration (seconds), turn count, or pass rate across models and effort tiers.
     - `all` *(default)*: Generates `tokens`, `tools`, `cost`, and `duration` charts plus the CSV/JSON summary exports in one pass.
3. **Multi-Task Overview Grid Charts (`overview_<metric>.{png,svg,html}`)**:
   - Arranges all tasks into a 2-column grid (`--grid-cols 2`) with each task's name centered above its panel and a shared legend on the right.

---

## Step-by-Step Workflow

### Step 1: Verify the Python Virtual Environment (`.venv`)

Always use the local `.venv` Python environment to avoid PEP 668 `externally-managed-environment` errors:

```bash
.venv/bin/python -c "import altair, pandas, vl_convert" || (python3 -m venv .venv && .venv/bin/pip install altair pandas vl-convert-python)
```

### Step 2: Run the Evaluation Chart Generator

Execute [generate_eval_charts.py](./scripts/generate_eval_charts.py) against one or more benchmark run directories or `results.jsonl` files:

```bash
.venv/bin/python .agents/skills/eval-charts/scripts/generate_eval_charts.py \
  --input <path-to-run-dir-or-results.jsonl> [<additional-run-dirs>...] \
  --output-dir <target-output-directory> \
  --metrics all
```

#### CLI Options (`generate_eval_charts.py`)

- `--input` / `-i` *(required)*: One or more paths to `results.jsonl` files, CSV/JSON files, or `benchmark-runs/<timestamp>` directories.
- `--output-dir` / `-o` *(default: `charts`)*: Destination directory for generated charts and `token_usage_summary.{csv,json}`.
- `--metrics` / `-m` *(default: `all`)*: Metrics to plot (`tokens`, `tools`, `cost`, `duration`, `turns`, `pass_rate`, or `all`).
- `--task-col` *(default: `case_id`)*: Field/column name identifying the task or scenario.
- `--model-col` *(default: `model`)*: Field/column name identifying the model.
- `--effort-col` *(default: `effort`)*: Field/column name identifying the effort tier.
- `--model-labels`: Optional `raw_model=DisplayName` overrides.
- `--palettes`: Optional `ModelDisplayName=palette` overrides (`teal`, `purple`, `amber`, `blue`, `rose`, `emerald`, `slate`).
- `--grid-cols` *(default: `2`)*: Number of columns in the multi-task `overview_<metric>` grid.
- `--formats` / `-f` *(default: `png svg html`)*: Output formats (`png`, `svg`, `html`, `json`).
- `--scale-factor` *(default: `2.0`)*: High-DPI scaling factor for PNG exports.

### Step 3: Verify & Embed in Reports

1. Inspect the generated `.png` charts using `read_file` to verify white background, clean labels, and color palettes.
2. When embedding charts in Markdown reports, **always use relative Markdown links** (or copy into the artifact directory if embedding in an Antigravity artifact).

#!/usr/bin/env python3
"""
Effort-Evaluation Chart & Summary Generator (`eval-charts` skill).

Uses the generic `altair-charts` skill (`../../altair-charts/scripts/chart_theme.py` and
`generate_bar_charts.py`) to turn benchmark `results.jsonl` (or CSV/JSON) runs into:
1. Aggregated summary exports (`token_usage_summary.csv`, `token_usage_summary.json`).
2. Per-task grouped & stacked bar charts (`<task>_<metric>.{png,svg,html}`).
3. Multi-task overview grid charts (`overview_<metric>.{png,svg,html}`).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
import altair as alt
import pandas as pd

# Import shared generic charting library from the sibling `altair-charts` skill
ALTAIR_CHARTS_SCRIPTS = Path(__file__).resolve().parents[2] / "altair-charts" / "scripts"
if str(ALTAIR_CHARTS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(ALTAIR_CHARTS_SCRIPTS))

from chart_theme import (
    PALETTES,
    build_grouped_color_scale,
    humanize_identifier,
    parse_kv_Mappings,
    save_chart,
)
from generate_bar_charts import (
    build_grouped_bar_chart,
    melt_wide_stack_columns,
)

# Canonical Effort Ordering (Low -> Max), with fallback for custom effort/tier names
EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max"]
EFFORT_RANK = {e: idx for idx, e in enumerate(EFFORT_ORDER)}

# Preferred Model Ordering & Default Palette Hints (new/unknown models are auto-formatted & assigned palettes)
PREFERRED_MODEL_ORDER = ["gemini-3.8-flash", "gemini-4-argon", "gemini-3.1-pro"]
DEFAULT_MODEL_PALETTES = {
    "Gemini 3.8 Flash": "teal",
    "Gemini 4 Argon": "purple",
    "Gemini 3.1 Pro": "amber",
}

# Default Token Stack Components (Column, Display Label)
DEFAULT_TOKEN_COMPONENTS = [
    ("cache_read_tokens", "Cached Input"),
    ("input_tokens", "Input"),
    ("output_tokens", "Output"),
]

# Preferred Tool Ordering (any additional discovered tools are appended alphabetically)
PREFERRED_TOOLS = [
    "run_command",
    "view_file",
    "replace_file_content",
    "write_to_file",
    "manage_task",
    "schedule",
    "search_web",
]

SCALAR_METRICS = {
    "cost": {
        "field": "cost_usd",
        "title": "Average Cost ($ USD)",
        "format": "$.3f",
        "axis_format": "$.2f",
        "suffix": "cost",
    },
    "duration": {
        "field": "duration_sec",
        "title": "Average Duration (Seconds)",
        "format": ".1f",
        "axis_format": "~s",
        "suffix": "duration",
    },
    "turns": {
        "field": "num_turns",
        "title": "Average Turns",
        "format": ".1f",
        "axis_format": "~s",
        "suffix": "turns",
    },
    "pass_rate": {
        "field": "pass_rate",
        "title": "Pass Rate",
        "format": ".1%",
        "axis_format": ".0%",
        "suffix": "pass_rate",
    },
}

METRIC_ALIASES = {
    "tokens": "tokens",
    "cost": "cost",
    "cost_usd": "cost",
    "duration": "duration",
    "duration_sec": "duration",
    "tools": "tools",
    "tool_calls": "tools",
    "tool_invocations": "tools",
    "turns": "turns",
    "num_turns": "turns",
    "pass_rate": "pass_rate",
    "passed": "pass_rate",
}


def strip_effort_suffix(model_name: str, effort: Optional[str] = None) -> str:
    """Extract base model identifier by stripping effort suffix if present."""
    if effort and model_name.endswith(f"-{effort}"):
        return model_name[: -len(f"-{effort}")]
    for suffix in [f"-{e}" for e in EFFORT_ORDER]:
        if model_name.endswith(suffix):
            return model_name[: -len(suffix)]
    return model_name


def format_model_display(base_model: str, custom_labels: Optional[Dict[str, str]] = None) -> str:
    """Convert raw model identifier (e.g., `gemini-3.8-flash`) into clean display name (`Gemini 3.8 Flash`)."""
    if custom_labels and base_model in custom_labels:
        return custom_labels[base_model]
    return " ".join(part.capitalize() for part in str(base_model).split("-") if part)


def load_pricing_models() -> Dict[str, Dict[str, float]]:
    """Load model token pricing from `api_list_prices.json` if present."""
    candidates = [
        Path.cwd() / "api_list_prices.json",
        Path(__file__).resolve().parents[4] / "api_list_prices.json",
    ]
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return data.get("models", {}) or {}
            except Exception:
                pass
    return {}


def compute_fallback_cost(
    raw_model: str,
    base_model: str,
    input_tokens: float,
    cache_read_tokens: float,
    output_tokens: float,
    pricing_models: Dict[str, Dict[str, float]],
) -> float:
    """Compute USD cost from token counts using `api_list_prices.json` when `cost_usd` is missing."""
    if not pricing_models:
        return 0.0
    p = pricing_models.get(raw_model) or pricing_models.get(base_model)
    if not p:
        for k, v in pricing_models.items():
            if raw_model.startswith(k) or base_model.startswith(k):
                p = v
                break
    if not p:
        return 0.0
    in_cost = (float(input_tokens or 0.0) / 1_000_000.0) * float(p.get("input", 0.0))
    cache_cost = (float(cache_read_tokens or 0.0) / 1_000_000.0) * float(p.get("cached_input", 0.0))
    out_cost = (float(output_tokens or 0.0) / 1_000_000.0) * float(p.get("output", 0.0))
    return round(in_cost + cache_cost + out_cost, 6)


def load_and_aggregate(
    inputs: Sequence[str],
    task_col: str = "case_id",
    model_col: str = "model",
    effort_col: str = "effort",
    custom_model_labels: Optional[Dict[str, str]] = None,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Load evaluation records from `results.jsonl` (or `.json` / `.csv`) files/directories
    and average runs per `(case_id, base_model, effort)`.
    """
    records = []
    discovered_tools = set()
    pricing_models = load_pricing_models()

    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            files = sorted(p.glob("**/results.jsonl"))
            if not files:
                files = sorted(p.glob("**/*.jsonl")) + sorted(p.glob("**/*.csv"))
        elif p.is_file():
            files = [p]
        else:
            raise FileNotFoundError(f"Input path not found: {inp}")

        if not files:
            raise FileNotFoundError(f"No evaluation results found at: {inp}")

        for fpath in files:
            if fpath.suffix.lower() == ".csv":
                raw_records = pd.read_csv(fpath).to_dict(orient="records")
            else:
                raw_records = []
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            raw_records.append(json.loads(line))

            for rec in raw_records:
                effort = str(rec.get(effort_col) or "default").lower()
                raw_model = str(rec.get("base_model") or rec.get(model_col) or "unknown")
                base_model = strip_effort_suffix(raw_model, effort)
                rec["case_id"] = str(rec.get(task_col, "default_task"))
                rec["base_model"] = base_model
                rec["model_display"] = format_model_display(base_model, custom_model_labels)
                rec["effort"] = effort
                rec["effort_display"] = humanize_identifier(effort)
                rec["effort_order"] = EFFORT_RANK.get(effort, 99)

                raw_cost = rec.get("cost_usd")
                if raw_cost is None or pd.isna(raw_cost) or float(raw_cost or 0.0) <= 0.0:
                    rec["cost_usd"] = compute_fallback_cost(
                        raw_model=raw_model,
                        base_model=base_model,
                        input_tokens=rec.get("input_tokens", 0.0),
                        cache_read_tokens=rec.get("cache_read_tokens", 0.0),
                        output_tokens=rec.get("output_tokens", 0.0),
                        pricing_models=pricing_models,
                    )

                tool_calls = rec.get("tool_calls")
                if isinstance(tool_calls, dict):
                    for t_name, t_count in tool_calls.items():
                        discovered_tools.add(t_name)
                        rec[f"tool__{t_name}"] = float(t_count or 0.0)
                else:
                    for k, v in list(rec.items()):
                        if k.startswith("tool__") and v is not None:
                            t_name = k[len("tool__") :]
                            discovered_tools.add(t_name)
                            rec[k] = float(v or 0.0)

                records.append(rec)

    if not records:
        raise ValueError("No valid evaluation records found.")

    ordered_tools = [t for t in PREFERRED_TOOLS if t in discovered_tools] + sorted(
        [t for t in discovered_tools if t not in PREFERRED_TOOLS]
    )

    df = pd.DataFrame(records)
    for col_default in [
        "cache_read_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "duration_sec",
        "cost_usd",
        "tool_invocations",
        "num_turns",
        "passed",
        "run",
    ]:
        if col_default not in df.columns:
            df[col_default] = 0.0
        else:
            df[col_default] = pd.to_numeric(df[col_default], errors="coerce").fillna(0.0)

    for t_name in ordered_tools:
        col = f"tool__{t_name}"
        if col not in df.columns:
            df[col] = 0.0
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    agg_spec = dict(
        cache_read_tokens=("cache_read_tokens", "mean"),
        input_tokens=("input_tokens", "mean"),
        output_tokens=("output_tokens", "mean"),
        total_tokens=("total_tokens", "mean"),
        duration_sec=("duration_sec", "mean"),
        cost_usd=("cost_usd", "mean"),
        tool_invocations=("tool_invocations", "mean"),
        num_turns=("num_turns", "mean"),
        run_count=("run", "count"),
        pass_rate=("passed", "mean"),
    )
    for t_name in ordered_tools:
        agg_spec[f"tool__{t_name}"] = (f"tool__{t_name}", "mean")

    grouped = df.groupby(
        ["case_id", "base_model", "model_display", "effort", "effort_display", "effort_order"],
        as_index=False,
    ).agg(**agg_spec)
    grouped["color_key"] = grouped["model_display"]
    return grouped, ordered_tools


def get_sorted_orders(
    df: pd.DataFrame,
    custom_model_labels: Optional[Dict[str, str]] = None,
) -> Tuple[List[str], List[str], List[str]]:
    """Return ordered lists of `base_model`, `model_display`, and `effort_display`."""
    present_models = df["base_model"].unique().tolist()
    ordered_models = [m for m in PREFERRED_MODEL_ORDER if m in present_models] + [
        m for m in present_models if m not in PREFERRED_MODEL_ORDER
    ]
    model_disp_order = [format_model_display(m, custom_model_labels) for m in ordered_models]

    effort_pairs = (
        df[["effort_display", "effort_order"]]
        .drop_duplicates()
        .sort_values(["effort_order", "effort_display"])
    )
    effort_disp_order = effort_pairs["effort_display"].tolist()
    return ordered_models, model_disp_order, effort_disp_order


def melt_tool_components(agg_df: pd.DataFrame, ordered_tools: Sequence[str]) -> pd.DataFrame:
    """Unpivot per-tool invocation columns (`tool__<name>`) for stacked tool bar charts."""
    rows = []
    for _, row in agg_df.iterrows():
        for stack_order, t_name in enumerate(ordered_tools):
            val = float(row.get(f"tool__{t_name}", 0.0))
            if val <= 0:
                continue
            rows.append(
                {
                    **row.to_dict(),
                    "component_type": t_name,
                    "stack_order": stack_order,
                    "value": val,
                    "color_key": f"{row['model_display']} • {t_name}",
                }
            )
    return pd.DataFrame(rows)


def export_token_usage_summary(
    agg_df: pd.DataFrame,
    ordered_tools: Sequence[str],
    out_dir: Path,
) -> List[Path]:
    """Export aggregated token usage, tool call breakdown, cost, and duration to CSV and JSON."""
    out_dir.mkdir(parents=True, exist_ok=True)
    base_cols = [
        "case_id",
        "base_model",
        "model_display",
        "effort",
        "effort_order",
        "run_count",
        "pass_rate",
        "cache_read_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_usd",
        "duration_sec",
        "tool_invocations",
    ]
    tool_cols = [f"tool__{t}" for t in ordered_tools]
    sorted_df = agg_df.sort_values(["case_id", "base_model", "effort_order"])[base_cols + tool_cols].copy()

    csv_path = out_dir / "token_usage_summary.csv"
    json_path = out_dir / "token_usage_summary.json"

    sorted_df.to_csv(csv_path, index=False, float_format="%.4f")
    sorted_df.to_json(json_path, orient="records", indent=2)
    return [csv_path, json_path]


def main():
    parser = argparse.ArgumentParser(
        description="Generate grouped & stacked evaluation bar charts and summary exports using the generic altair-eval-charts skill."
    )
    parser.add_argument(
        "--input",
        "-i",
        nargs="+",
        required=True,
        help="Path(s) to results.jsonl file(s), CSV/JSON files, or benchmark run directories.",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="charts",
        help="Directory to write generated charts and token usage exports (default: charts).",
    )
    parser.add_argument(
        "--metrics",
        "-m",
        nargs="+",
        default=["all"],
        help="Metrics to plot: tokens, tools, cost, duration, turns, pass_rate, or all (default: all).",
    )
    parser.add_argument(
        "--task-col",
        default="case_id",
        help="Column/field name identifying the evaluation task or scenario (default: case_id).",
    )
    parser.add_argument(
        "--model-col",
        default="model",
        help="Column/field name identifying the model (default: model).",
    )
    parser.add_argument(
        "--effort-col",
        default="effort",
        help="Column/field name identifying the effort or X-axis tier (default: effort).",
    )
    parser.add_argument(
        "--model-labels",
        nargs="+",
        default=None,
        help="Optional raw_model=DisplayName overrides (e.g., my-model='My Custom Model').",
    )
    parser.add_argument(
        "--palettes",
        nargs="+",
        default=None,
        help=f"Optional ModelDisplayName=palette overrides. Available: {', '.join(PALETTES.keys())}.",
    )
    parser.add_argument(
        "--grid-cols",
        type=int,
        default=2,
        help="Number of columns in the multi-task overview grid (default: 2).",
    )
    parser.add_argument(
        "--formats",
        "-f",
        nargs="+",
        default=["png", "svg", "html"],
        choices=["png", "svg", "html", "json"],
        help="Output file formats (default: png svg html).",
    )
    parser.add_argument(
        "--scale-factor",
        type=float,
        default=2.0,
        help="PNG render scaling factor for high-DPI output (default: 2.0).",
    )
    args = parser.parse_args()

    requested = ["tokens"]
    for m in args.metrics:
        if m == "all":
            requested.extend(["tokens", "tools", "cost", "duration"])
        elif m in METRIC_ALIASES:
            requested.append(METRIC_ALIASES[m])
        else:
            raise ValueError(
                f"Unsupported metric '{m}'. Choose from: {', '.join(sorted(set(METRIC_ALIASES.values())))} or all"
            )
    metrics_to_plot = list(dict.fromkeys(requested))

    custom_model_labels = parse_kv_Mappings(args.model_labels)
    palette_map = {**DEFAULT_MODEL_PALETTES, **parse_kv_Mappings(args.palettes)}

    agg_df, ordered_tools = load_and_aggregate(
        inputs=args.input,
        task_col=args.task_col,
        model_col=args.model_col,
        effort_col=args.effort_col,
        custom_model_labels=custom_model_labels,
    )
    _, model_disp_order, effort_disp_order = get_sorted_orders(agg_df, custom_model_labels)
    tasks = sorted(agg_df["case_id"].unique().tolist())
    out_dir = Path(args.output_dir)
    all_saved: List[Path] = []

    extra_tooltips = [
        alt.Tooltip("cost_usd:Q", title="Total Cost ($)", format="$.3f"),
        alt.Tooltip("duration_sec:Q", title="Duration (s)", format=".1f"),
        alt.Tooltip("run_count:Q", title="Runs Averaged"),
    ]

    # 1. Export structured summary (CSV & JSON)
    all_saved.extend(export_token_usage_summary(agg_df, ordered_tools, out_dir))

    # 2. Generate per-task & multi-task overview charts via generic bar chart builder
    for metric_key in metrics_to_plot:
        if metric_key == "tokens":
            token_cols = [c for c, _ in DEFAULT_TOKEN_COMPONENTS]
            token_labels = [lbl for _, lbl in DEFAULT_TOKEN_COMPONENTS]
            melted_df, _ = melt_wide_stack_columns(
                df=agg_df,
                group_col="model_display",
                stack_cols=token_cols,
                stack_labels=token_labels,
            )
            color_scale = build_grouped_color_scale(
                groups=model_disp_order,
                stacked_components=token_labels,
                palette_map=palette_map,
            )

            for case_id in tasks:
                task_df = melted_df[melted_df["case_id"] == case_id]
                chart = build_grouped_bar_chart(
                    df=task_df,
                    x_field="effort_display",
                    y_field="value",
                    y_title="Average Tokens",
                    y_format=",.0f",
                    color_scale=color_scale,
                    legend_title="Model • Token Type",
                    group_field="model_display",
                    group_sort_order=model_disp_order,
                    x_sort_order=effort_disp_order,
                    x_title="Effort Level",
                    title=f"Task: {case_id} — Token Usage",
                    subtitle="Grouped by Model • Bars per Effort Level • Stacked by Cached Input, Input & Output Tokens",
                    stacked=True,
                    component_label_title="Token Type",
                    total_field="total_tokens",
                    total_title="Total Bar Tokens",
                    extra_tooltips=extra_tooltips,
                )
                all_saved.extend(
                    save_chart(chart, out_dir / case_id / f"{case_id}_tokens", args.formats, args.scale_factor)
                )
                all_saved.extend(
                    save_chart(chart, out_dir / f"{case_id}_tokens", args.formats, args.scale_factor)
                )

            overview = build_grouped_bar_chart(
                df=melted_df,
                x_field="effort_display",
                y_field="value",
                y_title="Average Tokens",
                y_format=",.0f",
                color_scale=color_scale,
                legend_title="Model • Token Type",
                group_field="model_display",
                group_sort_order=model_disp_order,
                x_sort_order=effort_disp_order,
                x_title="Effort Level",
                title="Token Usage by Task, Model & Effort Level",
                subtitle="Averaged across runs • Grouped by Model • Stacked by Cached Input, Input & Output Tokens",
                stacked=True,
                component_label_title="Token Type",
                total_field="total_tokens",
                total_title="Total Bar Tokens",
                facet_field="case_id",
                facet_order=tasks,
                grid_columns=args.grid_cols,
                extra_tooltips=extra_tooltips,
            )
            all_saved.extend(save_chart(overview, out_dir / "overview_tokens", args.formats, args.scale_factor))

        elif metric_key == "tools":
            melted_tools_df = melt_tool_components(agg_df, ordered_tools)
            if melted_tools_df.empty:
                continue

            for case_id in tasks:
                task_df = melted_tools_df[melted_tools_df["case_id"] == case_id]
                if task_df.empty:
                    continue
                active_keys = set(task_df[task_df["value"] > 0]["color_key"].unique())
                task_color_scale = build_grouped_color_scale(
                    groups=model_disp_order,
                    stacked_components=ordered_tools,
                    palette_map=palette_map,
                    active_keys=active_keys,
                )
                chart = build_grouped_bar_chart(
                    df=task_df,
                    x_field="effort_display",
                    y_field="value",
                    y_title="Average Tool Calls",
                    y_format=".1f",
                    color_scale=task_color_scale,
                    legend_title="Model • Tool Kind",
                    group_field="model_display",
                    group_sort_order=model_disp_order,
                    x_sort_order=effort_disp_order,
                    x_title="Effort Level",
                    title=f"Task: {case_id} — Tool Invocations by Kind",
                    subtitle="Grouped by Model • Bars per Effort Level • Stacked by Tool Type",
                    stacked=True,
                    component_label_title="Tool Name",
                    total_field="tool_invocations",
                    total_title="Total Tool Calls",
                    extra_tooltips=extra_tooltips,
                )
                all_saved.extend(
                    save_chart(chart, out_dir / case_id / f"{case_id}_tools", args.formats, args.scale_factor)
                )
                all_saved.extend(
                    save_chart(chart, out_dir / f"{case_id}_tools", args.formats, args.scale_factor)
                )

            overview_active = set(melted_tools_df[melted_tools_df["value"] > 0]["color_key"].unique())
            overview_color_scale = build_grouped_color_scale(
                groups=model_disp_order,
                stacked_components=ordered_tools,
                palette_map=palette_map,
                active_keys=overview_active,
            )
            overview = build_grouped_bar_chart(
                df=melted_tools_df,
                x_field="effort_display",
                y_field="value",
                y_title="Average Tool Calls",
                y_format=".1f",
                color_scale=overview_color_scale,
                legend_title="Model • Tool Kind",
                group_field="model_display",
                group_sort_order=model_disp_order,
                x_sort_order=effort_disp_order,
                x_title="Effort Level",
                title="Tool Invocations by Task, Model, Effort Level & Tool Kind",
                subtitle="Averaged across runs • Grouped by Model • Stacked by Tool Type",
                stacked=True,
                component_label_title="Tool Name",
                total_field="tool_invocations",
                total_title="Total Tool Calls",
                facet_field="case_id",
                facet_order=tasks,
                grid_columns=args.grid_cols,
                extra_tooltips=extra_tooltips,
            )
            all_saved.extend(save_chart(overview, out_dir / "overview_tools", args.formats, args.scale_factor))

        else:
            cfg = SCALAR_METRICS[metric_key]
            color_scale = build_grouped_color_scale(
                groups=model_disp_order,
                stacked_components=None,
                palette_map=palette_map,
            )
            suffix = cfg["suffix"]

            for case_id in tasks:
                task_df = agg_df[agg_df["case_id"] == case_id]
                chart = build_grouped_bar_chart(
                    df=task_df,
                    x_field="effort_display",
                    y_field=cfg["field"],
                    y_title=cfg["title"],
                    y_format=cfg["axis_format"],
                    color_scale=color_scale,
                    legend_title="Model",
                    group_field="model_display",
                    group_sort_order=model_disp_order,
                    x_sort_order=effort_disp_order,
                    x_title="Effort Level",
                    title=f"Task: {case_id} — {cfg['title']}",
                    subtitle="Grouped by Model • Direct Comparison Across Effort Levels",
                    stacked=False,
                    extra_tooltips=extra_tooltips,
                )
                all_saved.extend(
                    save_chart(chart, out_dir / case_id / f"{case_id}_{suffix}", args.formats, args.scale_factor)
                )
                all_saved.extend(
                    save_chart(chart, out_dir / f"{case_id}_{suffix}", args.formats, args.scale_factor)
                )

            overview = build_grouped_bar_chart(
                df=agg_df,
                x_field="effort_display",
                y_field=cfg["field"],
                y_title=cfg["title"],
                y_format=cfg["axis_format"],
                color_scale=color_scale,
                legend_title="Model",
                group_field="model_display",
                group_sort_order=model_disp_order,
                x_sort_order=effort_disp_order,
                x_title="Effort Level",
                title=f"Benchmark Evaluation: {cfg['title']} by Task & Model",
                subtitle="Averaged across runs • Grouped by Model • Direct Comparison Across Effort Levels",
                stacked=False,
                facet_field="case_id",
                facet_order=tasks,
                grid_columns=args.grid_cols,
                extra_tooltips=extra_tooltips,
            )
            all_saved.extend(save_chart(overview, out_dir / f"overview_{suffix}", args.formats, args.scale_factor))

    print(f"Successfully generated {len(all_saved)} files in '{out_dir}':")
    for p in all_saved:
        print(f"  - {p}")


if __name__ == "__main__":
    main()

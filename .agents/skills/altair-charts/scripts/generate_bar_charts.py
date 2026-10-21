#!/usr/bin/env python3
"""
Generic Altair Grouped, Stacked, and Multi-Panel Grid Bar Chart Generator.

Can be used both as a standalone CLI tool on any CSV / JSON / JSONL dataset and as an
importable Python library for domain-specific evaluation scripts.

Key Features:
- Column grouping (`--group-col`, e.g., model, provider, category) with bars along the X-axis (`--x-col`).
- Supports both non-stacked scalar metrics (`--y-col`) and stacked component bars:
  - Wide-format stacking (`--stack-cols col1 col2 col3` with optional `--stack-labels`)
  - Long-format stacking (`--stack-col component_col --y-col value_col`)
- Optional multi-panel splitting (`--facet-col`): generates individual charts per facet value
  plus a multi-panel grid overview chart (`--grid-cols 2`).
- Automatic aggregation (`mean`, `sum`, `median`, `min`, `max`, `none`) across repeated observations.
- Uses `chart_theme.py` for pure white backgrounds (`#ffffff`) and light-to-dark shade gradients
  per group palette family.
"""

import argparse
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
import altair as alt
import pandas as pd

from chart_theme import (
    DEFAULT_PALETTE_ORDER,
    PALETTES,
    apply_white_theme,
    build_grouped_color_scale,
    humanize_identifier,
    load_tabular_data,
    parse_kv_Mappings,
    save_chart,
)


def melt_wide_stack_columns(
    df: pd.DataFrame,
    group_col: str,
    stack_cols: Sequence[str],
    stack_labels: Optional[Sequence[str]] = None,
    sep: str = " • ",
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Unpivot wide columns (`stack_cols`) into long rows with `component_type`, `stack_order`,
    `value`, and `color_key` (`<group> • <component_type>`).
    """
    labels = (
        list(stack_labels)
        if stack_labels and len(stack_labels) == len(stack_cols)
        else [humanize_identifier(c) for c in stack_cols]
    )
    rows = []
    for _, row in df.iterrows():
        base_dict = row.to_dict()
        grp_val = str(row[group_col]) if group_col and group_col in row else ""
        for stack_order, (col_name, comp_label) in enumerate(zip(stack_cols, labels)):
            val = float(row.get(col_name, 0.0) or 0.0)
            color_key = f"{grp_val}{sep}{comp_label}" if grp_val else comp_label
            rows.append(
                {
                    **base_dict,
                    "component_type": comp_label,
                    "stack_order": stack_order,
                    "value": val,
                    "color_key": color_key,
                }
            )
    return pd.DataFrame(rows), labels


def prepare_long_stack_columns(
    df: pd.DataFrame,
    group_col: str,
    stack_col: str,
    y_col: str,
    stack_order_list: Optional[Sequence[str]] = None,
    drop_zero: bool = True,
    sep: str = " • ",
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Prepare a long-format DataFrame that already has a component column (`stack_col`)
    and numeric value column (`y_col`) for stacked bar chart rendering.
    """
    present = df[stack_col].dropna().astype(str).unique().tolist()
    if stack_order_list:
        ordered_components = [c for c in stack_order_list if c in present] + [
            c for c in present if c not in stack_order_list
        ]
    else:
        ordered_components = present

    comp_rank = {c: idx for idx, c in enumerate(ordered_components)}
    out = df.copy()
    if drop_zero:
        out = out[out[y_col].fillna(0.0) > 0].copy()

    out["component_type"] = out[stack_col].astype(str)
    out["stack_order"] = out["component_type"].map(lambda c: comp_rank.get(c, 999))
    out["value"] = out[y_col].astype(float)
    if group_col and group_col in out.columns:
        out["color_key"] = out[group_col].astype(str) + sep + out["component_type"]
    else:
        out["color_key"] = out["component_type"]

    return out, ordered_components


def build_grouped_bar_chart(
    df: pd.DataFrame,
    x_field: str,
    y_field: str,
    y_title: str,
    color_scale: alt.Scale,
    legend_title: Optional[str] = None,
    group_field: Optional[str] = "model_display",
    group_sort_order: Optional[Sequence[str]] = None,
    x_sort_order: Optional[Sequence[str]] = None,
    x_title: Optional[str] = None,
    y_format: str = ",.0f",
    title: str = "",
    subtitle: str = "",
    stacked: bool = False,
    component_field: str = "component_type",
    component_label_title: str = "Component",
    stack_order_field: str = "stack_order",
    color_field: str = "color_key",
    total_field: Optional[str] = None,
    total_title: Optional[str] = None,
    facet_field: Optional[str] = None,
    facet_order: Optional[Sequence[str]] = None,
    grid_columns: int = 2,
    extra_tooltips: Optional[Sequence[alt.Tooltip]] = None,
    bar_size: Optional[int] = None,
    panel_width: Optional[int] = None,
    panel_height: Optional[int] = None,
) -> alt.TopLevelMixin:
    """
    Build a white-background Altair grouped/stacked bar chart, optionally faceted into
    a multi-panel grid across `facet_field`.
    """
    is_overview = facet_field is not None and facet_order is not None
    resolved_bar_size = bar_size or (26 if is_overview else 38)
    resolved_panel_w = panel_width or (120 if is_overview else 155)
    resolved_panel_h = panel_height or (220 if is_overview else 310)
    resolved_x_title = None if is_overview else (x_title if x_title is not None else humanize_identifier(x_field))

    tooltips: List[alt.Tooltip] = []
    if facet_field and facet_field in df.columns:
        tooltips.append(alt.Tooltip(f"{facet_field}:N", title=humanize_identifier(facet_field)))
    if group_field and group_field in df.columns:
        tooltips.append(alt.Tooltip(f"{group_field}:N", title=humanize_identifier(group_field)))
    tooltips.append(alt.Tooltip(f"{x_field}:N", title=x_title or humanize_identifier(x_field)))

    if stacked:
        tooltips.append(alt.Tooltip(f"{component_field}:N", title=component_label_title))
        tooltips.append(
            alt.Tooltip(f"{y_field}:Q", title=f"{component_label_title} Value", format=y_format)
        )
        if total_field and total_title and total_field in df.columns:
            tooltips.append(alt.Tooltip(f"{total_field}:Q", title=total_title, format=y_format))
    else:
        tooltips.append(alt.Tooltip(f"{y_field}:Q", title=y_title, format=y_format))

    if extra_tooltips:
        tooltips.extend(extra_tooltips)

    axis_y_format = "~s" if (stacked and y_format == ",.0f") else y_format

    encode_kwargs = dict(
        x=alt.X(
            f"{x_field}:N",
            title=resolved_x_title,
            sort=list(x_sort_order) if x_sort_order else None,
            axis=alt.Axis(
                labelAngle=0,
                labelFontSize=11 if is_overview else 12,
                titleFontSize=12,
                titlePadding=8,
                labelPadding=5,
                domainColor="#cbd5e1",
                tickColor="#cbd5e1",
            ),
        ),
        y=alt.Y(
            f"{y_field}:Q",
            title=y_title,
            stack="zero" if stacked else None,
            axis=alt.Axis(
                grid=True,
                gridColor="#f1f5f9",
                domainColor="#cbd5e1",
                tickColor="#cbd5e1",
                labelFontSize=10 if is_overview else 11,
                titleFontSize=11 if is_overview else 12,
                titlePadding=8,
                format=axis_y_format,
            ),
        ),
        color=alt.Color(
            f"{color_field}:N",
            scale=color_scale,
            legend=alt.Legend(
                orient="right",
                title=legend_title,
                titleFontSize=11 if is_overview else 12,
                labelFontSize=10 if is_overview else 11,
                labelLimit=280,
                symbolLimit=60,
                symbolType="square",
                symbolSize=100 if is_overview else 120,
                rowPadding=3 if is_overview else 4,
                columns=2 if (stacked and len(color_scale.domain) > 18) else 1,
            ),
        ),
        tooltip=tooltips,
    )
    if stacked and stack_order_field in df.columns:
        encode_kwargs["order"] = alt.Order(f"{stack_order_field}:O", sort="ascending")

    def _make_panel(sub_df: pd.DataFrame, sub_title: Optional[alt.TitleParams] = None) -> alt.TopLevelMixin:
        base_bar = (
            alt.Chart(sub_df)
            .mark_bar(
                cornerRadiusTopLeft=3,
                cornerRadiusTopRight=3,
                size=resolved_bar_size,
                stroke="#ffffff",
                strokeWidth=1.2,
            )
            .encode(**encode_kwargs)
            .properties(width=resolved_panel_w, height=resolved_panel_h)
        )
        if group_field and group_field in sub_df.columns:
            panel: alt.TopLevelMixin = base_bar.facet(
                column=alt.Column(
                    f"{group_field}:N",
                    title=None,
                    sort=list(group_sort_order) if group_sort_order else None,
                    header=alt.Header(
                        labelFontSize=12 if is_overview else 13,
                        labelFontWeight="bold",
                        labelColor="#1e293b",
                        labelPadding=6,
                    ),
                ),
                spacing=16 if is_overview else 24,
            )
        else:
            panel = base_bar
        if sub_title is not None:
            panel = panel.properties(title=sub_title)
        return panel

    title_kwargs = dict(
        text=title,
        fontSize=16 if is_overview else 15,
        fontWeight="bold",
        subtitleFontSize=12,
        subtitleColor="#64748b",
        anchor="start",
        offset=16,
    )
    if subtitle:
        title_kwargs["subtitle"] = subtitle
    main_title = alt.TitleParams(**title_kwargs) if title else None

    if is_overview and facet_field and facet_order:
        subcharts = []
        for f_val in facet_order:
            f_df = df[df[facet_field] == f_val]
            f_title = alt.TitleParams(
                text=str(f_val),
                fontSize=14,
                fontWeight="bold",
                color="#0f172a",
                anchor="middle",
                offset=10,
            )
            subcharts.append(_make_panel(f_df, sub_title=f_title))

        chart = alt.concat(*subcharts, columns=grid_columns, spacing=36)
        if main_title is not None:
            chart = chart.properties(title=main_title)
        chart = chart.resolve_scale(color="shared")
    else:
        chart = _make_panel(df, sub_title=main_title)

    return apply_white_theme(chart, padding=18)


def main():
    parser = argparse.ArgumentParser(
        description="Generate clean, white-background Altair grouped, stacked, and grid-faceted bar charts from any CSV/JSON/JSONL data."
    )
    parser.add_argument(
        "--input",
        "-i",
        nargs="+",
        required=True,
        help="Input file(s) or directory (.csv, .json, .jsonl).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="charts",
        help="Output directory for generated charts (default: charts).",
    )
    parser.add_argument(
        "--output-name",
        "-n",
        default="bar_chart",
        help="Output filename stem (default: bar_chart).",
    )
    parser.add_argument(
        "--x-col",
        "-x",
        required=True,
        help="Column name for the X-axis bars within each group (e.g., effort, tier, size, step).",
    )
    parser.add_argument(
        "--x-order",
        nargs="+",
        default=None,
        help="Explicit sort order for X-axis values.",
    )
    parser.add_argument(
        "--x-title",
        default=None,
        help="Custom X-axis title (defaults to humanized --x-col).",
    )
    parser.add_argument(
        "--group-col",
        "-g",
        default=None,
        help="Optional column name to group/facet bar columns by (e.g., model, provider, series).",
    )
    parser.add_argument(
        "--group-order",
        nargs="+",
        default=None,
        help="Explicit sort order for group columns.",
    )
    parser.add_argument(
        "--y-col",
        "-y",
        default=None,
        help="Numeric column for bar height (required unless --stack-cols is used).",
    )
    parser.add_argument(
        "--y-title",
        default=None,
        help="Custom Y-axis title.",
    )
    parser.add_argument(
        "--y-format",
        default=",.2f",
        help="D3 number format for Y-axis and tooltips (default: ,.2f).",
    )
    parser.add_argument(
        "--stack-cols",
        nargs="+",
        default=None,
        help="Multiple numeric columns to melt and stack within each bar (wide format).",
    )
    parser.add_argument(
        "--stack-labels",
        nargs="+",
        default=None,
        help="Custom display labels corresponding to --stack-cols.",
    )
    parser.add_argument(
        "--stack-col",
        default=None,
        help="Category column for stacked bar segments when data is already in long format (used with --y-col).",
    )
    parser.add_argument(
        "--stack-order",
        nargs="+",
        default=None,
        help="Explicit bottom-to-top ordering of values in --stack-col.",
    )
    parser.add_argument(
        "--facet-col",
        default=None,
        help="Optional column to split into per-facet charts and a multi-panel overview grid (e.g., task, case_id, region).",
    )
    parser.add_argument(
        "--grid-cols",
        type=int,
        default=2,
        help="Number of columns in the multi-panel overview grid when --facet-col is set (default: 2).",
    )
    parser.add_argument(
        "--agg",
        default="mean",
        choices=["mean", "sum", "median", "min", "max", "none"],
        help="Aggregation function across repeated rows per group/x/facet combination (default: mean).",
    )
    parser.add_argument(
        "--palettes",
        nargs="+",
        default=None,
        help=f"Optional Group=palette mappings. Available palettes: {', '.join(PALETTES.keys())}.",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Main chart title.",
    )
    parser.add_argument(
        "--subtitle",
        default="",
        help="Chart subtitle.",
    )
    parser.add_argument(
        "--legend-title",
        default=None,
        help="Custom legend title.",
    )
    parser.add_argument(
        "--formats",
        "-f",
        nargs="+",
        default=["png", "svg", "html"],
        choices=["png", "svg", "html", "json"],
        help="Output formats (default: png svg html).",
    )
    parser.add_argument(
        "--scale-factor",
        type=float,
        default=2.0,
        help="High-DPI scale factor for PNG output (default: 2.0).",
    )
    args = parser.parse_args()

    if not args.y_col and not args.stack_cols:
        raise ValueError("Either --y-col or --stack-cols must be specified.")

    df = load_tabular_data(args.input)
    palette_map = parse_kv_Mappings(args.palettes)

    group_col = args.group_col
    if not group_col:
        group_col = "_default_group"
        df[group_col] = "Series"

    group_keys = [c for c in [args.facet_col, group_col, args.x_col, args.stack_col] if c]
    num_cols = list(args.stack_cols) if args.stack_cols else [args.y_col]

    if args.agg != "none":
        df = df.groupby(group_keys, as_index=False)[num_cols].agg(args.agg)

    present_groups = df[group_col].astype(str).unique().tolist()
    ordered_groups = (
        [g for g in args.group_order if g in present_groups]
        + [g for g in present_groups if g not in args.group_order]
        if args.group_order
        else present_groups
    )

    present_x = df[args.x_col].astype(str).unique().tolist()
    ordered_x = (
        [x for x in args.x_order if x in present_x] + [x for x in present_x if x not in args.x_order]
        if args.x_order
        else present_x
    )

    is_stacked = bool(args.stack_cols or args.stack_col)
    if args.stack_cols:
        plot_df, comp_labels = melt_wide_stack_columns(
            df=df,
            group_col=group_col,
            stack_cols=args.stack_cols,
            stack_labels=args.stack_labels,
        )
        y_field = "value"
        y_title = args.y_title or "Value"
        legend_title = args.legend_title or (
            f"{humanize_identifier(args.group_col)} • Component" if args.group_col else "Component"
        )
    elif args.stack_col:
        plot_df, comp_labels = prepare_long_stack_columns(
            df=df,
            group_col=group_col,
            stack_col=args.stack_col,
            y_col=args.y_col,
            stack_order_list=args.stack_order,
        )
        y_field = "value"
        y_title = args.y_title or humanize_identifier(args.y_col)
        legend_title = args.legend_title or (
            f"{humanize_identifier(args.group_col)} • {humanize_identifier(args.stack_col)}"
            if args.group_col
            else humanize_identifier(args.stack_col)
        )
    else:
        plot_df = df.copy()
        plot_df["color_key"] = plot_df[group_col].astype(str)
        comp_labels = None
        y_field = args.y_col
        y_title = args.y_title or humanize_identifier(args.y_col)
        legend_title = args.legend_title or (humanize_identifier(args.group_col) if args.group_col else "Series")

    active_keys = set(plot_df["color_key"].unique()) if is_stacked else None
    color_scale = build_grouped_color_scale(
        groups=ordered_groups,
        stacked_components=comp_labels,
        palette_map=palette_map,
        active_keys=active_keys,
    )

    out_dir = Path(args.output_dir)
    all_saved: List[Path] = []

    if args.facet_col:
        facets = sorted(plot_df[args.facet_col].unique().tolist())
        for f_val in facets:
            sub_df = plot_df[plot_df[args.facet_col] == f_val]
            sub_chart = build_grouped_bar_chart(
                df=sub_df,
                x_field=args.x_col,
                y_field=y_field,
                y_title=y_title,
                color_scale=color_scale,
                legend_title=legend_title,
                group_field=args.group_col,
                group_sort_order=ordered_groups if args.group_col else None,
                x_sort_order=ordered_x,
                x_title=args.x_title,
                y_format=args.y_format,
                title=f"{f_val} — {args.title or y_title}",
                subtitle=args.subtitle,
                stacked=is_stacked,
            )
            all_saved.extend(
                save_chart(sub_chart, out_dir / f"{f_val}_{args.output_name}", args.formats, args.scale_factor)
            )

        overview_chart = build_grouped_bar_chart(
            df=plot_df,
            x_field=args.x_col,
            y_field=y_field,
            y_title=y_title,
            color_scale=color_scale,
            legend_title=legend_title,
            group_field=args.group_col,
            group_sort_order=ordered_groups if args.group_col else None,
            x_sort_order=ordered_x,
            x_title=args.x_title,
            y_format=args.y_format,
            title=args.title or f"{y_title} Overview",
            subtitle=args.subtitle,
            stacked=is_stacked,
            facet_field=args.facet_col,
            facet_order=facets,
            grid_columns=args.grid_cols,
        )
        all_saved.extend(
            save_chart(overview_chart, out_dir / f"overview_{args.output_name}", args.formats, args.scale_factor)
        )
    else:
        chart = build_grouped_bar_chart(
            df=plot_df,
            x_field=args.x_col,
            y_field=y_field,
            y_title=y_title,
            color_scale=color_scale,
            legend_title=legend_title,
            group_field=args.group_col,
            group_sort_order=ordered_groups if args.group_col else None,
            x_sort_order=ordered_x,
            x_title=args.x_title,
            y_format=args.y_format,
            title=args.title,
            subtitle=args.subtitle,
            stacked=is_stacked,
        )
        all_saved.extend(save_chart(chart, out_dir / args.output_name, args.formats, args.scale_factor))

    print(f"Successfully generated {len(all_saved)} bar chart files in '{out_dir}':")
    for p in all_saved:
        print(f"  - {p}")


if __name__ == "__main__":
    main()

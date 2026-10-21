#!/usr/bin/env python3
"""
Generic Altair Spider / Radar Chart Generator for Multi-Axis Comparisons.

Features:
- Converts any multi-axis tabular dataset (`.csv`, `.json`, `.jsonl`) into publication-ready,
  white-background Altair spider (radar) charts.
- Auto-detects axis column and numeric series/profile columns when not explicitly provided.
- Dynamically scales concentric polygonal grid rings and 1:1 coordinate geometry for any `--max-val`.
- Supports multiple generation modes (`--mode`):
  - `progressive` (default): Generates `chart1` (1st series), `chart2` (1st + 2nd series overlaid),
    ..., `chartN` (all N series overlaid) with identical axis labels and coordinate geometry.
  - `combined`: Generates a single chart overlaying all selected series.
  - `individual`: Generates one standalone chart per series.
- Uses `chart_theme.py` for shared palettes (`teal`, `purple`, `amber`, `blue`, `rose`, `emerald`, `slate`).
"""

import argparse
import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
import altair as alt
import pandas as pd

from chart_theme import (
    PALETTES,
    apply_white_theme,
    load_tabular_data,
    parse_kv_Mappings,
    resolve_group_palette,
    save_chart,
)


def compute_default_rings(max_val: float, count: int = 5) -> Tuple[float, ...]:
    """Compute `count` evenly spaced concentric ring values up to `max_val`."""
    step = max_val / count
    rings = []
    for i in range(1, count + 1):
        val = step * i
        rings.append(int(round(val)) if abs(val - round(val)) < 1e-6 else round(val, 2))
    return tuple(rings)


def build_spider_chart(
    df: pd.DataFrame,
    axis_col: str,
    profile_cols: Sequence[str],
    all_profile_cols: Optional[Sequence[str]] = None,
    max_val: float = 10.0,
    rings: Optional[Sequence[float]] = None,
    tooltip_desc_col: Optional[str] = "Core Meaning of the Axis",
    show_legend: bool = True,
    palette_map: Optional[Dict[str, str]] = None,
    title: str = "",
    subtitle: str = "",
) -> alt.TopLevelMixin:
    """
    Build a white-background Altair spider/radar chart with true 1:1 coordinate aspect ratio
    and consistent radial axis labels.
    """
    all_cols = list(all_profile_cols) if all_profile_cols else list(profile_cols)
    resolved_rings = tuple(rings) if rings else compute_default_rings(max_val, count=5)

    axes = df[axis_col].astype(str).tolist()
    n_axes = len(axes)
    if n_axes < 3:
        raise ValueError(f"Spider chart requires at least 3 axes (found {n_axes}).")

    # Coordinate domains scaled proportionally to max_val (maintaining 35:27 = 700:540 1:1 aspect ratio)
    x_bound = 1.75 * max_val
    y_bound = 1.35 * max_val

    # Angles starting at 12 o'clock (0 rad) moving clockwise:
    # x = r * sin(theta), y = r * cos(theta)
    angles = [2.0 * math.pi * i / n_axes for i in range(n_axes)]

    # 1. Concentric polygonal grid rings (web lines)
    ring_rows = []
    for r_val in resolved_rings:
        for idx in range(n_axes + 1):
            i = idx % n_axes
            theta = angles[i]
            ring_rows.append(
                {
                    "ring": r_val,
                    "order": idx,
                    "x": r_val * math.sin(theta),
                    "y": r_val * math.cos(theta),
                    "is_outer": abs(r_val - max_val) < 1e-6,
                }
            )
    ring_df = pd.DataFrame(ring_rows)

    inner_rings_chart = (
        alt.Chart(ring_df[~ring_df["is_outer"]])
        .mark_line(stroke="#e2e8f0", strokeWidth=1.2, strokeDash=[3, 3])
        .encode(
            x=alt.X("x:Q", scale=alt.Scale(domain=[-x_bound, x_bound]), axis=None),
            y=alt.Y("y:Q", scale=alt.Scale(domain=[-y_bound, y_bound]), axis=None),
            detail="ring:N",
            order="order:O",
        )
    )

    outer_ring_chart = (
        alt.Chart(ring_df[ring_df["is_outer"]])
        .mark_line(stroke="#cbd5e1", strokeWidth=1.6)
        .encode(
            x=alt.X("x:Q", scale=alt.Scale(domain=[-x_bound, x_bound]), axis=None),
            y=alt.Y("y:Q", scale=alt.Scale(domain=[-y_bound, y_bound]), axis=None),
            detail="ring:N",
            order="order:O",
        )
    )

    # 2. Radial spokes from center (0, 0) to outer vertex
    spoke_rows = []
    for i, theta in enumerate(angles):
        spoke_rows.append({"axis_idx": i, "order": 0, "x": 0.0, "y": 0.0})
        spoke_rows.append(
            {
                "axis_idx": i,
                "order": 1,
                "x": max_val * math.sin(theta),
                "y": max_val * math.cos(theta),
            }
        )
    spoke_df = pd.DataFrame(spoke_rows)
    spokes_chart = (
        alt.Chart(spoke_df)
        .mark_line(stroke="#cbd5e1", strokeWidth=1.2)
        .encode(
            x="x:Q",
            y="y:Q",
            detail="axis_idx:N",
            order="order:O",
        )
    )

    # 3. Radial ring numeric tick labels along the top vertical axis (12 o'clock)
    tick_x_offset = 0.035 * max_val
    tick_rows = [
        {"ring": r_val, "label": f"{r_val:g}", "x": tick_x_offset, "y": float(r_val)}
        for r_val in resolved_rings
    ]
    tick_df = pd.DataFrame(tick_rows)
    ticks_halo_chart = (
        alt.Chart(tick_df)
        .mark_text(
            align="left",
            baseline="middle",
            dx=5,
            dy=-1,
            fontSize=10,
            fontWeight="bold",
            stroke="#ffffff",
            strokeWidth=3.0,
            strokeJoin="round",
            strokeCap="round",
            color="#ffffff",
        )
        .encode(x="x:Q", y="y:Q", text="label:N")
    )
    ticks_chart = (
        alt.Chart(tick_df)
        .mark_text(
            align="left",
            baseline="middle",
            dx=5,
            dy=-1,
            fontSize=10,
            fontWeight="normal",
            color="#64748b",
        )
        .encode(x="x:Q", y="y:Q", text="label:N")
    )

    # 4. Outer Axis Labels (identical across all charts)
    label_radius = max_val * 1.12
    label_rows = []
    for axis_name, theta in zip(axes, angles):
        sin_t = math.sin(theta)
        cos_t = math.cos(theta)
        lx = label_radius * sin_t
        ly = label_radius * cos_t

        if abs(sin_t) < 0.15:
            h_align = "center"
            v_base = "bottom" if cos_t > 0 else "top"
        elif sin_t > 0:
            h_align = "left"
            v_base = "middle"
        else:
            h_align = "right"
            v_base = "middle"

        label_rows.append(
            {
                "axis": axis_name,
                "x": lx,
                "y": ly,
                "h_align": h_align,
                "v_base": v_base,
            }
        )
    label_df = pd.DataFrame(label_rows)

    label_layers = []
    for (h_align, v_base), grp in label_df.groupby(["h_align", "v_base"]):
        dx = 6 if h_align == "left" else (-6 if h_align == "right" else 0)
        dy = -4 if v_base == "bottom" else (6 if v_base == "top" else 0)
        lbl_layer = (
            alt.Chart(grp)
            .mark_text(
                align=h_align,
                baseline=v_base,
                dx=dx,
                dy=dy,
                fontSize=13,
                fontWeight="bold",
                color="#1e293b",
            )
            .encode(x="x:Q", y="y:Q", text="axis:N")
        )
        label_layers.append(lbl_layer)

    # 5. Profile Polygons & Vertex Points
    polygon_layers = []
    point_rows = []

    for prof in profile_cols:
        prof_idx = all_cols.index(prof) if prof in all_cols else 0
        style = resolve_group_palette(prof, prof_idx, palette_map=palette_map)
        poly_rows = []

        for idx in range(n_axes + 1):
            i = idx % n_axes
            theta = angles[i]
            val = float(df.iloc[i][prof])
            px = val * math.sin(theta)
            py = val * math.cos(theta)
            desc = (
                str(df.iloc[i][tooltip_desc_col])
                if (tooltip_desc_col and tooltip_desc_col in df.columns)
                else ""
            )

            poly_rows.append(
                {
                    "profile": prof,
                    "axis": axes[i],
                    "value": val,
                    "order": idx,
                    "x": px,
                    "y": py,
                    "meaning": desc,
                }
            )
            if idx < n_axes:
                point_rows.append(
                    {
                        "profile": prof,
                        "axis": axes[i],
                        "value": val,
                        "x": px,
                        "y": py,
                        "meaning": desc,
                    }
                )

        poly_df = pd.DataFrame(poly_rows)
        poly_layer = (
            alt.Chart(poly_df)
            .mark_line(
                stroke=style["stroke"],
                strokeWidth=2.8,
                fill=style["fill"],
                fillOpacity=0.25,
                strokeJoin="round",
            )
            .encode(x="x:Q", y="y:Q", order="order:O")
        )
        polygon_layers.append(poly_layer)

    points_df = pd.DataFrame(point_rows)
    tooltips = [
        alt.Tooltip("profile:N", title="Series"),
        alt.Tooltip("axis:N", title="Axis"),
        alt.Tooltip("value:Q", title="Score"),
    ]
    if tooltip_desc_col and tooltip_desc_col in df.columns:
        tooltips.append(alt.Tooltip("meaning:N", title=tooltip_desc_col))

    active_domain = [p for p in all_cols if p in profile_cols]
    active_range = [
        resolve_group_palette(p, all_cols.index(p), palette_map=palette_map)["stroke"]
        for p in active_domain
    ]

    points_chart = (
        alt.Chart(points_df)
        .mark_point(
            filled=True,
            size=95,
            opacity=1.0,
            stroke="#ffffff",
            strokeWidth=1.5,
        )
        .encode(
            x="x:Q",
            y="y:Q",
            color=alt.Color(
                "profile:N",
                scale=alt.Scale(domain=active_domain, range=active_range),
                legend=alt.Legend(
                    orient="bottom",
                    direction="horizontal",
                    title=None,
                    labelFontSize=12,
                    labelFontWeight="bold",
                    labelColor="#1e293b",
                    labelLimit=300,
                    symbolType="circle",
                    symbolSize=140,
                    columnPadding=24,
                    offset=8,
                )
                if show_legend
                else None,
            ),
            tooltip=tooltips,
        )
    )

    chart = alt.layer(
        inner_rings_chart,
        outer_ring_chart,
        spokes_chart,
        *polygon_layers,
        ticks_halo_chart,
        ticks_chart,
        points_chart,
        *label_layers,
    ).properties(width=700, height=540)

    if title:
        title_kwargs = dict(
            text=title,
            fontSize=15,
            fontWeight="bold",
            subtitleFontSize=12,
            subtitleColor="#64748b",
            anchor="middle",
            offset=12,
        )
        if subtitle:
            title_kwargs["subtitle"] = subtitle
        chart = chart.properties(title=alt.TitleParams(**title_kwargs))

    return apply_white_theme(chart, padding=16)


def detect_columns(
    df: pd.DataFrame,
    axis_col: Optional[str],
    profiles: Optional[Sequence[str]],
    tooltip_col: Optional[str],
) -> Tuple[str, List[str], Optional[str]]:
    """Auto-detect axis column, numeric series columns, and optional tooltip description column."""
    resolved_axis = axis_col
    if not resolved_axis or resolved_axis not in df.columns:
        if "Axis" in df.columns:
            resolved_axis = "Axis"
        else:
            non_num = df.select_dtypes(exclude=["number"]).columns.tolist()
            if not non_num:
                raise ValueError(f"Could not auto-detect axis column in {list(df.columns)}")
            resolved_axis = non_num[0]

    if profiles:
        for col in profiles:
            if col not in df.columns:
                raise ValueError(f"Series column '{col}' not found in data. Available: {list(df.columns)}")
        resolved_profiles = list(profiles)
    else:
        num_cols = df.select_dtypes(include=["number"]).columns.tolist()
        resolved_profiles = [c for c in num_cols if c != resolved_axis]
        if not resolved_profiles:
            raise ValueError(f"No numeric series columns found in {list(df.columns)}")

    resolved_tooltip = tooltip_col
    if resolved_tooltip and resolved_tooltip not in df.columns:
        other_text = [
            c
            for c in df.select_dtypes(exclude=["number"]).columns
            if c != resolved_axis and c not in resolved_profiles
        ]
        resolved_tooltip = other_text[0] if other_text else None

    return resolved_axis, resolved_profiles, resolved_tooltip


def main():
    parser = argparse.ArgumentParser(
        description="Generate clean, white-background Altair spider/radar charts from any multi-axis CSV/JSON/JSONL data."
    )
    parser.add_argument(
        "--input",
        "-i",
        default="developer/spiderchart.csv",
        help="Path to input CSV/JSON/JSONL file (default: developer/spiderchart.csv).",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="developer",
        help="Directory to write generated charts (default: developer).",
    )
    parser.add_argument(
        "--output-prefix",
        default="chart",
        help="Filename prefix for generated charts (default: chart -> chart1, chart2, ...).",
    )
    parser.add_argument(
        "--axis-col",
        default="Axis",
        help="Column name for spider chart axes (default: Axis, or auto-detected first text column).",
    )
    parser.add_argument(
        "--profiles",
        "--series",
        "-p",
        nargs="+",
        default=None,
        help="Series/profile column names to plot (default: auto-detect all numeric columns).",
    )
    parser.add_argument(
        "--mode",
        choices=["progressive", "combined", "individual", "all"],
        default="progressive",
        help=(
            "Chart generation mode: "
            "'progressive' (chart1=1st series, chart2=1st+2nd overlaid, ...), "
            "'combined' (single chart with all series), "
            "'individual' (one chart per series), or 'all' (default: progressive)."
        ),
    )
    parser.add_argument(
        "--max-val",
        type=float,
        default=None,
        help="Maximum radial scale value (default: 10.0 if max score <= 10, else auto-scaled).",
    )
    parser.add_argument(
        "--rings",
        type=float,
        nargs="+",
        default=None,
        help="Explicit concentric ring values (default: 5 evenly spaced rings up to --max-val).",
    )
    parser.add_argument(
        "--tooltip-col",
        default="Core Meaning of the Axis",
        help="Optional column containing axis descriptions for interactive tooltips.",
    )
    parser.add_argument(
        "--palettes",
        nargs="+",
        default=None,
        help=f"Optional Series=palette mappings. Available palettes: {', '.join(PALETTES.keys())}.",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Optional chart title (default: none / title-free).",
    )
    parser.add_argument(
        "--subtitle",
        default="",
        help="Optional chart subtitle.",
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

    df = load_tabular_data([args.input], default_glob="*.csv")
    axis_col, profiles, tooltip_col = detect_columns(df, args.axis_col, args.profiles, args.tooltip_col)
    palette_map = parse_kv_Mappings(args.palettes)

    data_max = float(df[profiles].max().max())
    max_val = args.max_val if args.max_val is not None else (10.0 if data_max <= 10.0 else math.ceil(data_max))

    out_dir = Path(args.output_dir)
    all_saved: List[Path] = []

    if args.mode in ("progressive", "all"):
        for idx in range(len(profiles)):
            sub_profiles = profiles[: idx + 1]
            chart = build_spider_chart(
                df=df,
                axis_col=axis_col,
                profile_cols=sub_profiles,
                all_profile_cols=profiles,
                max_val=max_val,
                rings=args.rings,
                tooltip_desc_col=tooltip_col,
                palette_map=palette_map,
                title=args.title,
                subtitle=args.subtitle,
            )
            stem = out_dir / f"{args.output_prefix}{idx + 1}"
            all_saved.extend(save_chart(chart, stem, args.formats, args.scale_factor))

    if args.mode in ("individual", "all"):
        for idx, prof in enumerate(profiles):
            chart = build_spider_chart(
                df=df,
                axis_col=axis_col,
                profile_cols=[prof],
                all_profile_cols=profiles,
                max_val=max_val,
                rings=args.rings,
                tooltip_desc_col=tooltip_col,
                palette_map=palette_map,
                title=args.title,
                subtitle=args.subtitle,
            )
            safe_name = prof.lower().replace(" ", "_")
            stem = out_dir / f"{args.output_prefix}_{safe_name}"
            all_saved.extend(save_chart(chart, stem, args.formats, args.scale_factor))

    if args.mode in ("combined", "all"):
        chart = build_spider_chart(
            df=df,
            axis_col=axis_col,
            profile_cols=profiles,
            all_profile_cols=profiles,
            max_val=max_val,
            rings=args.rings,
            tooltip_desc_col=tooltip_col,
            palette_map=palette_map,
            title=args.title,
            subtitle=args.subtitle,
        )
        stem = out_dir / f"{args.output_prefix}_combined" if args.mode == "all" else out_dir / args.output_prefix
        all_saved.extend(save_chart(chart, stem, args.formats, args.scale_factor))

    print(f"Successfully generated {len(all_saved)} spider chart files in '{out_dir}':")
    for p in all_saved:
        print(f"  - {p}")


if __name__ == "__main__":
    main()

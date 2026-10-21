#!/usr/bin/env python3
"""
Shared Altair styling, color palettes, data loading, and multi-format export utilities.

Provides:
- Named 7-shade light-to-dark color palettes (`teal`, `purple`, `amber`, `blue`, `rose`, `emerald`, `slate`)
  for consistent multi-series grouped and stacked charts as well as spider/radar charts.
- Automatic palette assignment for arbitrary series/groups (with optional explicit overrides).
- Light-to-dark shade selection for stacked bar components within a group hue.
- Pure white-background (`#ffffff`) Altair theme configuration.
- Generic tabular data loading (`.csv`, `.json`, `.jsonl`) and multi-format chart saving (`.png`, `.svg`, `.html`, `.vl.json`).
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import altair as alt
import pandas as pd

FONT_STACK = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"

# Named 7-step light-to-dark color palettes.
# - `shades`: 7 hex colors from light (index 0) to dark (index 6) for stacked components.
# - `solid`: middle shade (index 3) for non-stacked / scalar bars.
# - `stroke`, `fill`, `point`: coordinated colors for spider/radar polygons and line charts.
PALETTES: Dict[str, Dict[str, Any]] = {
    "teal": {
        "label": "Teal / Cyan",
        "shades": ["#b2dfdb", "#80cbc4", "#4db6ac", "#26a69a", "#00897b", "#00695c", "#004d40"],
        "solid": "#26a69a",
        "stroke": "#00897b",
        "fill": "#26a69a",
        "point": "#00695c",
    },
    "purple": {
        "label": "Purple / Indigo",
        "shades": ["#d1c4e9", "#b39ddb", "#9575cd", "#7e57c2", "#5e35b1", "#4527a0", "#1a0066"],
        "solid": "#7e57c2",
        "stroke": "#5e35b1",
        "fill": "#7e57c2",
        "point": "#4527a0",
    },
    "amber": {
        "label": "Amber / Orange",
        "shades": ["#ffe0b2", "#ffcc80", "#ffb74d", "#fb8c00", "#ef6c00", "#d84315", "#870000"],
        "solid": "#fb8c00",
        "stroke": "#ef6c00",
        "fill": "#fb8c00",
        "point": "#d84315",
    },
    "blue": {
        "label": "Blue / Cobalt",
        "shades": ["#bbdefb", "#90caf9", "#64b5f6", "#1e88e5", "#1565c0", "#0d47a1", "#062356"],
        "solid": "#1e88e5",
        "stroke": "#1565c0",
        "fill": "#1e88e5",
        "point": "#0d47a1",
    },
    "rose": {
        "label": "Rose / Crimson",
        "shades": ["#f8bbd0", "#f48fb1", "#f06292", "#e91e63", "#c2185b", "#880e4f", "#4a0025"],
        "solid": "#e91e63",
        "stroke": "#c2185b",
        "fill": "#e91e63",
        "point": "#880e4f",
    },
    "emerald": {
        "label": "Emerald / Green",
        "shades": ["#c8e6c9", "#a5d6a7", "#81c784", "#43a047", "#2e7d32", "#1b5e20", "#0a2e0d"],
        "solid": "#43a047",
        "stroke": "#2e7d32",
        "fill": "#43a047",
        "point": "#1b5e20",
    },
    "slate": {
        "label": "Slate / Neutral",
        "shades": ["#e2e8f0", "#cbd5e1", "#94a3b8", "#64748b", "#475569", "#334155", "#0f172a"],
        "solid": "#64748b",
        "stroke": "#475569",
        "fill": "#64748b",
        "point": "#334155",
    },
}

DEFAULT_PALETTE_ORDER: List[str] = [
    "teal",
    "purple",
    "amber",
    "blue",
    "rose",
    "emerald",
    "slate",
]


def parse_kv_Mappings(items: Optional[Sequence[str]]) -> Dict[str, str]:
    """Parse CLI key=value pairs (e.g. `SeriesA=teal SeriesB=purple`) into a dictionary."""
    result: Dict[str, str] = {}
    if not items:
        return result
    for item in items:
        if "=" in item:
            k, v = item.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def humanize_identifier(value: str) -> str:
    """Convert raw snake_case or kebab-case identifiers into readable labels when no explicit map is given."""
    if not value:
        return ""
    # Preserve strings that already have spaces and uppercase letters
    if " " in value and any(c.isupper() for c in value):
        return value
    tokens = re.split(r"[_-]+", str(value).strip())
    return " ".join(t.capitalize() for t in tokens if t)


def pick_shades(shades: Sequence[str], count: int) -> List[str]:
    """Select `count` well-separated light-to-dark hex colors from a palette's shade list."""
    if count <= 0:
        return []
    if count == 1:
        return [shades[len(shades) // 2]]
    if count == 3 and len(shades) >= 7:
        return [shades[0], shades[3], shades[6]]
    n = len(shades)
    return [shades[min(int(round(i * (n - 1) / (count - 1))), n - 1)] for i in range(count)]


def resolve_group_palette(
    group_name: str,
    group_index: int,
    palette_map: Optional[Dict[str, str]] = None,
    palette_order: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Resolve the palette dictionary for a given group/series name.
    Checks `palette_map` first (matching raw or display group name, or palette name),
    then falls back to cycling through `palette_order`.
    """
    order = list(palette_order) if palette_order else DEFAULT_PALETTE_ORDER
    if palette_map:
        mapped = palette_map.get(group_name)
        if mapped and mapped in PALETTES:
            return PALETTES[mapped]
    palette_key = order[group_index % len(order)]
    return PALETTES[palette_key]


def build_grouped_color_scale(
    groups: Sequence[str],
    stacked_components: Optional[Sequence[str]] = None,
    palette_map: Optional[Dict[str, str]] = None,
    palette_order: Optional[Sequence[str]] = None,
    active_keys: Optional[set] = None,
    sep: str = " • ",
) -> alt.Scale:
    """
    Build a deterministic Altair color Scale across groups and optional stacked components.
    - When `stacked_components` is provided, each `(group, component)` pair gets a shade
      from light to dark within that group's assigned palette family.
    - When `stacked_components` is None, each `group` gets the solid mid-tone shade of its palette.
    """
    domain: List[str] = []
    color_range: List[str] = []

    for idx, grp in enumerate(groups):
        pal = resolve_group_palette(grp, idx, palette_map=palette_map, palette_order=palette_order)
        shades = pal["shades"]
        if stacked_components:
            chosen = pick_shades(shades, len(stacked_components))
            for comp_label, hex_color in zip(stacked_components, chosen):
                key = f"{grp}{sep}{comp_label}" if len(groups) > 1 or grp else str(comp_label)
                if active_keys is None or key in active_keys:
                    domain.append(key)
                    color_range.append(hex_color)
        else:
            if active_keys is None or grp in active_keys:
                domain.append(grp)
                color_range.append(pal["solid"])

    return alt.Scale(domain=domain, range=color_range)


def apply_white_theme(
    chart: alt.TopLevelMixin,
    padding: int = 18,
) -> alt.TopLevelMixin:
    """Apply clean, publication-ready pure white background (`#ffffff`) and Inter typography."""
    return (
        chart.configure(
            background="#ffffff",
            font=FONT_STACK,
            padding={"left": padding, "top": padding, "right": padding, "bottom": padding},
        )
        .configure_view(stroke=None, fill="#ffffff")
    )


def save_chart(
    chart: alt.TopLevelMixin,
    stem: Path,
    formats: Sequence[str] = ("png", "svg", "html"),
    scale_factor: float = 2.0,
) -> List[Path]:
    """Save an Altair chart in the requested output formats (`.png`, `.svg`, `.html`, `.vl.json`)."""
    saved: List[Path] = []
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        fmt_clean = fmt.lower().lstrip(".")
        if fmt_clean in ("json", "vl.json"):
            out_file = stem.with_suffix(".vl.json")
            chart.save(str(out_file), format="json")
        elif fmt_clean == "html":
            out_file = stem.with_suffix(".html")
            chart.save(str(out_file), embed_options={"renderer": "svg", "actions": False})
        elif fmt_clean == "png":
            out_file = stem.with_suffix(".png")
            chart.save(str(out_file), scale_factor=scale_factor)
        elif fmt_clean == "svg":
            out_file = stem.with_suffix(".svg")
            chart.save(str(out_file))
        else:
            continue
        saved.append(out_file)
    return saved


def load_tabular_data(
    inputs: Sequence[str],
    default_glob: str = "*.jsonl",
) -> pd.DataFrame:
    """
    Load one or more `.csv`, `.json`, or `.jsonl` files (or directories containing them)
    into a unified pandas DataFrame.
    """
    frames: List[pd.DataFrame] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            files = sorted(p.glob(f"**/{default_glob}"))
            if not files:
                for pattern in ("*.csv", "*.jsonl", "*.json"):
                    files = sorted(p.glob(f"**/{pattern}"))
                    if files:
                        break
        elif p.is_file():
            files = [p]
        else:
            raise FileNotFoundError(f"Input path not found: {inp}")

        if not files:
            raise FileNotFoundError(f"No data files found in directory: {inp}")

        for fpath in files:
            suffix = fpath.suffix.lower()
            if suffix == ".csv":
                frames.append(pd.read_csv(fpath))
            elif suffix == ".jsonl":
                records = []
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            records.append(json.loads(line))
                if records:
                    frames.append(pd.DataFrame(records))
            elif suffix == ".json":
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    frames.append(pd.DataFrame(data))
                elif isinstance(data, dict):
                    frames.append(pd.DataFrame([data]))
            else:
                raise ValueError(f"Unsupported file format '{suffix}' for {fpath}")

    if not frames:
        raise ValueError("No valid records loaded from input paths.")
    return pd.concat(frames, ignore_index=True)

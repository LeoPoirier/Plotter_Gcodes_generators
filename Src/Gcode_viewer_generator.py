#!/usr/bin/env python3
"""
Gcode_viewer_generator.py — Headless G-code preview renderer

Parses a G-code file and renders a PNG preview image with configurable
visualization options. Designed to be called from Gcode_viewer_GUI.py
or directly from the command line.

Usage:
    python Gcode_viewer_generator.py input.gcode output.png [options]
    python Gcode_viewer_generator.py --get-params

Supports:
    - G0 (rapid/travel) and G1 (draw) moves
    - G2/G3 (arc) moves approximated as line segments
    - G90 (absolute) / G91 (relative) positioning
    - Color modes: solid, gradient (start→end), feedrate heatmap
    - Nth segment highlighting
    - Start/end point markers
    - Configurable line thickness, color, transparency for both G0 and G1
"""

import argparse
import json
import math
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")  # headless backend
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.collections import LineCollection
from matplotlib.patches import Circle
import numpy as np


# ══════════════════════════════════════════════════════════════════════════════
# G-CODE PARSER
# ══════════════════════════════════════════════════════════════════════════════

def parse_gcode(filepath):
    """
    Parse a G-code file and return a list of move segments.

    Each segment is a dict:
        {
            "type": "G0" | "G1",
            "start": (x0, y0),
            "end": (x1, y1),
            "feedrate": float or None,
            "index": int  (sequential segment number)
        }
    """
    segments = []
    current_x = 0.0
    current_y = 0.0
    current_z = 0.0
    current_f = None
    absolute = True  # G90 default
    segment_index = 0

    # Regex for extracting coordinates
    re_param = re.compile(r'([XYZIJKFR])\s*([+-]?\d*\.?\d+)')

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            # Remove comments
            line = line.split(";")[0].split("%")[0].strip()
            if not line:
                continue

            # Detect G-command
            g_match = re.match(r'G(\d+)', line, re.IGNORECASE)
            if not g_match:
                # Could be a line with just coordinates (modal G1)
                # Check if it has X or Y
                params = dict(re_param.findall(line.upper()))
                if params and ("X" in params or "Y" in params):
                    # Treat as modal G1
                    g_code = 1
                else:
                    continue
            else:
                g_code = int(g_match.group(1))

            # Handle positioning mode
            if g_code == 90:
                absolute = True
                continue
            elif g_code == 91:
                absolute = False
                continue
            elif g_code not in (0, 1, 2, 3):
                continue

            # Extract parameters
            params = dict(re_param.findall(line.upper()))

            # Update feedrate
            if "F" in params:
                current_f = float(params["F"])

            if g_code in (0, 1):
                # Linear move
                if absolute:
                    new_x = float(params["X"]) if "X" in params else current_x
                    new_y = float(params["Y"]) if "Y" in params else current_y
                    new_z = float(params["Z"]) if "Z" in params else current_z
                else:
                    new_x = current_x + (float(params["X"]) if "X" in params else 0.0)
                    new_y = current_y + (float(params["Y"]) if "Y" in params else 0.0)
                    new_z = current_z + (float(params["Z"]) if "Z" in params else 0.0)

                # Only record if there's actual XY movement
                if new_x != current_x or new_y != current_y:
                    segments.append({
                        "type": "G0" if g_code == 0 else "G1",
                        "start": (current_x, current_y),
                        "end": (new_x, new_y),
                        "feedrate": current_f,
                        "index": segment_index,
                    })
                    segment_index += 1

                current_x, current_y, current_z = new_x, new_y, new_z

            elif g_code in (2, 3):
                # Arc move — approximate with line segments
                arc_segments = _parse_arc(
                    current_x, current_y, params, absolute, g_code, current_f, segment_index
                )
                for seg in arc_segments:
                    segments.append(seg)
                    segment_index += 1

                if arc_segments:
                    current_x, current_y = arc_segments[-1]["end"]
                # Update Z if present
                if absolute:
                    current_z = float(params["Z"]) if "Z" in params else current_z
                else:
                    current_z += float(params["Z"]) if "Z" in params else 0.0

    return segments


def _parse_arc(cx, cy, params, absolute, g_code, feedrate, start_index):
    """
    Approximate a G2/G3 arc as short line segments.
    Returns a list of segment dicts.
    """
    # Target position
    if absolute:
        ex = float(params.get("X", cx))
        ey = float(params.get("Y", cy))
    else:
        ex = cx + float(params.get("X", 0))
        ey = cy + float(params.get("Y", 0))

    # Arc center (I, J are always relative to start)
    i_off = float(params.get("I", 0))
    j_off = float(params.get("J", 0))

    center_x = cx + i_off
    center_y = cy + j_off

    # Radius
    r = math.sqrt(i_off ** 2 + j_off ** 2)
    if r < 1e-9:
        # Fallback: treat as linear
        return [{
            "type": "G1",
            "start": (cx, cy),
            "end": (ex, ey),
            "feedrate": feedrate,
            "index": start_index,
        }]

    # Start and end angles
    start_angle = math.atan2(cy - center_y, cx - center_x)
    end_angle = math.atan2(ey - center_y, ex - center_x)

    # Determine sweep direction
    if g_code == 2:  # CW
        if end_angle >= start_angle:
            end_angle -= 2 * math.pi
    else:  # G3, CCW
        if end_angle <= start_angle:
            end_angle += 2 * math.pi

    sweep = end_angle - start_angle
    # Number of segments: ~1 segment per 5 degrees
    n_segs = max(4, int(abs(sweep) / math.radians(5)))

    segments = []
    prev_x, prev_y = cx, cy

    for step in range(1, n_segs + 1):
        t = step / n_segs
        angle = start_angle + sweep * t
        nx = center_x + r * math.cos(angle)
        ny = center_y + r * math.sin(angle)

        segments.append({
            "type": "G1",
            "start": (prev_x, prev_y),
            "end": (nx, ny),
            "feedrate": feedrate,
            "index": start_index + step - 1,
        })
        prev_x, prev_y = nx, ny

    return segments


# ══════════════════════════════════════════════════════════════════════════════
# RENDERER
# ══════════════════════════════════════════════════════════════════════════════

def hex_to_rgba(hex_color, alpha=1.0):
    """Convert hex color string to RGBA tuple (0-1 range)."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 6:
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    elif len(hex_color) == 3:
        r, g, b = int(hex_color[0]*2, 16), int(hex_color[1]*2, 16), int(hex_color[2]*2, 16)
    else:
        r, g, b = 0, 0, 0
    return (r / 255.0, g / 255.0, b / 255.0, alpha)


def render_gcode(segments, output_path, **kwargs):
    """
    Render parsed G-code segments to a PNG file.

    Keyword arguments:
        line_width      (float)  : G1 line width in mm (data-space) (default 0.5)
        line_color      (str)    : G1 hex color (default "#000000")
        line_alpha      (float)  : G1 transparency 0-1 (default 1.0)
        bg_color        (str)    : background hex color (default "#FFFFFF")
        show_g0         (bool)   : show rapid/travel moves (default True)
        g0_color        (str)    : G0 hex color (default "#FF0000")
        g0_alpha        (float)  : G0 transparency 0-1 (default 0.3)
        g0_width        (float)  : G0 line width in mm (data-space) (default 0.3)
        color_mode      (str)    : "solid" | "gradient" | "feedrate" (default "solid")
        show_start      (bool)   : show start point marker (default False)
        show_end        (bool)   : show end point marker (default False)
        nth_segment     (int)    : highlight every Nth segment, 0=disabled (default 0)
        nth_color       (str)    : Nth segment hex color (default "#00FF00")
        dpi             (int)    : output DPI (default 150)
        max_size        (int)    : max image dimension in pixels (default 2000)
    """
    # Unpack parameters with defaults
    line_width_mm = kwargs.get("line_width", 0.5)
    line_color    = kwargs.get("line_color", "#000000")
    line_alpha    = kwargs.get("line_alpha", 1.0)
    bg_color      = kwargs.get("bg_color", "#FFFFFF")
    show_g0       = kwargs.get("show_g0", True)
    g0_color      = kwargs.get("g0_color", "#FF0000")
    g0_alpha      = kwargs.get("g0_alpha", 0.3)
    g0_width_mm   = kwargs.get("g0_width", 0.3)
    color_mode    = kwargs.get("color_mode", "solid")
    show_start    = kwargs.get("show_start", False)
    show_end      = kwargs.get("show_end", False)
    nth_segment   = kwargs.get("nth_segment", 0)
    nth_color     = kwargs.get("nth_color", "#00FF00")
    dpi           = kwargs.get("dpi", 150)
    max_size      = kwargs.get("max_size", 2000)

    if not segments:
        print("Warning: No segments found in G-code file.", file=sys.stderr)
        # Create a blank image with message
        fig, ax = plt.subplots(1, 1, figsize=(4, 4), dpi=dpi)
        ax.set_facecolor(bg_color)
        ax.text(0.5, 0.5, "No moves found", ha="center", va="center",
                transform=ax.transAxes, fontsize=14, color="#888888")
        ax.set_xticks([])
        ax.set_yticks([])
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight",
                    facecolor=bg_color, pad_inches=0.1)
        plt.close(fig)
        return {"x_min": 0, "x_max": 0, "y_min": 0, "y_max": 0,
                "total_segments": 0, "g0_count": 0, "g1_count": 0}

    # Separate G0 and G1 segments
    g0_segs = [s for s in segments if s["type"] == "G0"]
    g1_segs = [s for s in segments if s["type"] == "G1"]

    # Calculate bounding box from all segments
    all_x = []
    all_y = []
    for s in segments:
        all_x.extend([s["start"][0], s["end"][0]])
        all_y.extend([s["start"][1], s["end"][1]])

    x_min, x_max = min(all_x), max(all_x)
    y_min, y_max = min(all_y), max(all_y)

    # Add margin (5%)
    x_range = x_max - x_min if x_max != x_min else 1.0
    y_range = y_max - y_min if y_max != y_min else 1.0
    margin = max(x_range, y_range) * 0.05
    x_min -= margin
    x_max += margin
    y_min -= margin
    y_max += margin
    x_range = x_max - x_min
    y_range = y_max - y_min

    # Calculate figure size to maintain aspect ratio
    aspect = x_range / y_range if y_range > 0 else 1.0
    max_inches = max_size / dpi
    if aspect >= 1:
        fig_w = min(max_inches, 12)
        fig_h = fig_w / aspect
    else:
        fig_h = min(max_inches, 12)
        fig_w = fig_h * aspect

    # Ensure minimum size
    fig_w = max(fig_w, 3)
    fig_h = max(fig_h, 3)

    # Create figure
    fig, ax = plt.subplots(1, 1, figsize=(fig_w, fig_h), dpi=dpi)
    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(bg_color)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_aspect("equal")
    ax.axis("off")

    # ── Convert mm line widths to points ──
    # The figure displays x_range data-units across fig_w inches.
    # 1 inch = 72 points.  So:
    #   data_units_per_point = x_range / (fig_w * 72)
    #   points_per_mm = 1mm / data_units_per_point
    #                 = (fig_w * 72) / x_range
    # This makes line width DPI-independent (always the same fraction of data).
    points_per_mm = (fig_w * 72.0) / x_range
    line_width_pt = line_width_mm * points_per_mm
    g0_width_pt   = g0_width_mm * points_per_mm

    # ── Draw G0 (travel) moves ──
    if show_g0 and g0_segs:
        g0_lines = [[(s["start"][0], s["start"][1]),
                     (s["end"][0], s["end"][1])] for s in g0_segs]
        g0_rgba = hex_to_rgba(g0_color, g0_alpha)
        g0_lc = LineCollection(
            g0_lines,
            colors=[g0_rgba] * len(g0_lines),
            linewidths=g0_width_pt,
            linestyles="dashed",
            zorder=1
        )
        ax.add_collection(g0_lc)

    # ── Draw G1 (draw) moves ──
    if g1_segs:
        g1_lines = [[(s["start"][0], s["start"][1]),
                     (s["end"][0], s["end"][1])] for s in g1_segs]

        # Determine colors based on color mode
        g1_colors = _compute_g1_colors(g1_segs, color_mode, line_color, line_alpha)

        g1_lc = LineCollection(
            g1_lines,
            colors=g1_colors,
            linewidths=line_width_pt,
            zorder=2
        )
        ax.add_collection(g1_lc)

    # ── Draw Nth segment highlights ──
    if nth_segment > 0 and g1_segs:
        nth_rgba = hex_to_rgba(nth_color, 1.0)
        nth_lines = []
        for i, s in enumerate(g1_segs):
            if (i + 1) % nth_segment == 0:
                nth_lines.append([(s["start"][0], s["start"][1]),
                                  (s["end"][0], s["end"][1])])
        if nth_lines:
            nth_lc = LineCollection(
                nth_lines,
                colors=[nth_rgba] * len(nth_lines),
                linewidths=line_width_pt * 2.5,
                zorder=3
            )
            ax.add_collection(nth_lc)

    # ── Draw start point ──
    if show_start and segments:
        first_draw = g1_segs[0] if g1_segs else segments[0]
        sx, sy = first_draw["start"]
        marker_size = max(x_range, y_range) * 0.012
        start_circle = Circle(
            (sx, sy), marker_size,
            facecolor="#00CC44", edgecolor="#004400",
            linewidth=1.2, zorder=5
        )
        ax.add_patch(start_circle)

    # ── Draw end point ──
    if show_end and segments:
        last_draw = g1_segs[-1] if g1_segs else segments[-1]
        ex, ey = last_draw["end"]
        marker_size = max(x_range, y_range) * 0.012
        end_circle = Circle(
            (ex, ey), marker_size,
            facecolor="#CC0000", edgecolor="#440000",
            linewidth=1.2, zorder=5
        )
        ax.add_patch(end_circle)

    # ── Save ──
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight",
                facecolor=bg_color, pad_inches=0.02)
    plt.close(fig)

    # Return metadata for the GUI
    return {
        "x_min": round(min(all_x), 3),
        "x_max": round(max(all_x), 3),
        "y_min": round(min(all_y), 3),
        "y_max": round(max(all_y), 3),
        "width": round(max(all_x) - min(all_x), 3),
        "height": round(max(all_y) - min(all_y), 3),
        "total_segments": len(segments),
        "g0_count": len(g0_segs),
        "g1_count": len(g1_segs),
    }


def _compute_g1_colors(g1_segs, color_mode, line_color, line_alpha):
    """Compute per-segment RGBA colors based on the selected color mode."""
    n = len(g1_segs)

    if color_mode == "gradient":
        # Blue → Red gradient based on draw order
        colors = []
        for i in range(n):
            t = i / max(n - 1, 1)
            # Interpolate from blue to red through purple
            r = t
            g = 0.0
            b = 1.0 - t
            colors.append((r, g, b, line_alpha))
        return colors

    elif color_mode == "feedrate":
        # Heatmap based on feedrate values
        feedrates = []
        for s in g1_segs:
            f = s["feedrate"]
            feedrates.append(f if f is not None else 0.0)

        f_arr = np.array(feedrates, dtype=float)
        f_min = f_arr.min()
        f_max = f_arr.max()

        if f_max - f_min < 1e-9:
            # All same feedrate — just use solid color
            rgba = hex_to_rgba(line_color, line_alpha)
            return [rgba] * n

        # Normalize to 0-1
        f_norm = (f_arr - f_min) / (f_max - f_min)

        # Use a perceptually uniform colormap (viridis)
        cmap = plt.cm.get_cmap("plasma")
        colors = []
        for val in f_norm:
            c = cmap(val)
            colors.append((c[0], c[1], c[2], line_alpha))
        return colors

    else:
        # Solid color (default)
        rgba = hex_to_rgba(line_color, line_alpha)
        return [rgba] * n


# ══════════════════════════════════════════════════════════════════════════════
# --get-params (for compatibility with plotter_gui ecosystem)
# ══════════════════════════════════════════════════════════════════════════════

PARAM_SPEC = {
    "name": "G-code Viewer Generator",
    "description": "Renders a G-code file as a PNG preview image with configurable visualization options.",
    "parameters": [
        {
            "name": "line_width",
            "label": "Line Width",
            "flag": "--line-width",
            "type": "float",
            "default": 0.5,
            "min": 0.1,
            "max": 5.0,
            "step": 0.1,
            "unit": "mm"
        },
        {
            "name": "line_color",
            "label": "Line Color",
            "flag": "--line-color",
            "type": "color",
            "default": "#000000"
        },
        {
            "name": "line_alpha",
            "label": "Line Transparency",
            "flag": "--line-alpha",
            "type": "float",
            "default": 1.0,
            "min": 0.0,
            "max": 1.0,
            "step": 0.05,
            "unit": ""
        },
        {
            "name": "bg_color",
            "label": "Background Color",
            "flag": "--bg-color",
            "type": "color",
            "default": "#FFFFFF"
        },
        {
            "name": "show_g0",
            "label": "Show G0 (Travel)",
            "flag": "--show-g0",
            "type": "bool",
            "default": True
        },
        {
            "name": "g0_color",
            "label": "G0 Line Color",
            "flag": "--g0-color",
            "type": "color",
            "default": "#FF0000"
        },
        {
            "name": "g0_alpha",
            "label": "G0 Transparency",
            "flag": "--g0-alpha",
            "type": "float",
            "default": 0.3,
            "min": 0.0,
            "max": 1.0,
            "step": 0.05,
            "unit": ""
        },
        {
            "name": "g0_width",
            "label": "G0 Line Width",
            "flag": "--g0-width",
            "type": "float",
            "default": 0.3,
            "min": 0.1,
            "max": 5.0,
            "step": 0.1,
            "unit": "mm"
        },
        {
            "name": "color_mode",
            "label": "Color Mode",
            "flag": "--color-mode",
            "type": "choice",
            "choices": ["solid", "gradient", "feedrate"],
            "default": "solid"
        },
        {
            "name": "show_start",
            "label": "Show Start Point",
            "flag": "--show-start",
            "type": "bool",
            "default": False
        },
        {
            "name": "show_end",
            "label": "Show End Point",
            "flag": "--show-end",
            "type": "bool",
            "default": False
        },
        {
            "name": "nth_segment",
            "label": "Highlight Every Nth",
            "flag": "--nth-segment",
            "type": "int",
            "default": 0,
            "min": 0,
            "max": 1000,
            "step": 1,
            "unit": ""
        },
        {
            "name": "nth_color",
            "label": "Nth Segment Color",
            "flag": "--nth-color",
            "type": "color",
            "default": "#00FF00"
        },
        {
            "name": "dpi",
            "label": "Output DPI",
            "flag": "--dpi",
            "type": "int",
            "default": 150,
            "min": 72,
            "max": 1500,
            "step": 1,
            "unit": "dpi"
        }
    ]
}


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def build_argparser():
    parser = argparse.ArgumentParser(
        description="Render G-code to PNG preview image."
    )
    parser.add_argument("input", nargs="?", help="Input .gcode file path")
    parser.add_argument("output", nargs="?", help="Output .png file path")

    # Rendering options
    parser.add_argument("--line-width", type=float, default=0.5,
                        help="G1 line width in mm (data-space) (default: 0.5)")
    parser.add_argument("--line-color", type=str, default="#000000",
                        help="G1 line hex color (default: #000000)")
    parser.add_argument("--line-alpha", type=float, default=1.0,
                        help="G1 line transparency 0-1 (default: 1.0)")
    parser.add_argument("--bg-color", type=str, default="#FFFFFF",
                        help="Background hex color (default: #FFFFFF)")
    parser.add_argument("--show-g0", type=str, default="true",
                        help="Show G0 travel moves: true/false (default: true)")
    parser.add_argument("--g0-color", type=str, default="#FF0000",
                        help="G0 line hex color (default: #FF0000)")
    parser.add_argument("--g0-alpha", type=float, default=0.3,
                        help="G0 line transparency 0-1 (default: 0.3)")
    parser.add_argument("--g0-width", type=float, default=0.3,
                        help="G0 line width in mm (data-space) (default: 0.3)")
    parser.add_argument("--color-mode", type=str, default="solid",
                        choices=["solid", "gradient", "feedrate"],
                        help="Color mode: solid, gradient, feedrate (default: solid)")
    parser.add_argument("--show-start", type=str, default="false",
                        help="Show start point marker: true/false (default: false)")
    parser.add_argument("--show-end", type=str, default="false",
                        help="Show end point marker: true/false (default: false)")
    parser.add_argument("--nth-segment", type=int, default=0,
                        help="Highlight every Nth G1 segment, 0=disabled (default: 0)")
    parser.add_argument("--nth-color", type=str, default="#00FF00",
                        help="Nth segment highlight hex color (default: #00FF00)")
    parser.add_argument("--dpi", type=int, default=150,
                        help="Output image DPI (default: 150)")
    parser.add_argument("--get-params", action="store_true",
                        help="Print parameter spec as JSON and exit")
    return parser


def str_to_bool(s):
    """Convert string to boolean."""
    return s.lower() in ("true", "1", "yes", "on")


def main():
    parser = build_argparser()
    args = parser.parse_args()

    # --get-params mode
    if args.get_params:
        print(json.dumps(PARAM_SPEC, indent=2))
        return 0

    # Validate inputs
    if not args.input:
        parser.error("Input .gcode file path is required.")
    if not args.output:
        parser.error("Output .png file path is required.")

    if not os.path.isfile(args.input):
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        return 1

    # Parse G-code
    print(f"Parsing: {args.input}")
    segments = parse_gcode(args.input)
    print(f"Found {len(segments)} segments "
          f"({sum(1 for s in segments if s['type']=='G1')} G1, "
          f"{sum(1 for s in segments if s['type']=='G0')} G0)")

    # Render
    print(f"Rendering with color_mode={args.color_mode}, dpi={args.dpi}...")
    metadata = render_gcode(
        segments,
        args.output,
        line_width=args.line_width,
        line_color=args.line_color,
        line_alpha=args.line_alpha,
        bg_color=args.bg_color,
        show_g0=str_to_bool(args.show_g0),
        g0_color=args.g0_color,
        g0_alpha=args.g0_alpha,
        g0_width=args.g0_width,
        color_mode=args.color_mode,
        show_start=str_to_bool(args.show_start),
        show_end=str_to_bool(args.show_end),
        nth_segment=args.nth_segment,
        nth_color=args.nth_color,
        dpi=args.dpi,
    )

    # Print metadata as JSON to stdout (GUI reads this)
    print(f"\nSaved: {args.output}")
    print(f"Bounding box: X[{metadata['x_min']} -> {metadata['x_max']}] "
          f"Y[{metadata['y_min']} -> {metadata['y_max']}]")
    print(f"Size: {metadata['width']} x {metadata['height']} units")

    # Print machine-readable JSON on last line for GUI parsing
    print(f"\n__METADATA_JSON__:{json.dumps(metadata)}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

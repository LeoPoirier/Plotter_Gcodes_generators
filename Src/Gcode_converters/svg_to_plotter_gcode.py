#!/usr/bin/env python3
"""
svg_to_plotter_gcode.py - SVG to Plotter G-code converter (svg2gcode backend)

Converts SVG vector drawings to pen-plotter G-code by using svg2gcode to parse
and optimise SVG paths, then post-processing the output to replace laser
commands with Z-axis pen up/down movements suitable for a plotter.

Usage:
    python svg_to_plotter_gcode.py input.svg output.gcode [options]
    python svg_to_plotter_gcode.py --get-params
"""

import argparse
import json
import sys
import os
import subprocess
import tempfile
import re
from pathlib import Path
import math

# ===================================================================
# CONVERTER METADATA
# ===================================================================
CONVERTER_INFO = {
    "name": "SVG to Plotter (svg2gcode)",
    "description": (
        "Converts SVG vector drawings to pen-plotter G-code using svg2gcode "
        "as the backend. SVG paths are parsed, optimised, and output as "
        "plotter moves with Z-axis pen up/down commands. Supports scaling, "
        "origin offset, and color-coded path selection. Automatically "
        "converts polylines, polygons, circles, and other basic shapes to "
        "paths before processing."
    ),
    "author": "Plotter",
    "version": "1.2",
    "input_types": [["SVG files", "*.svg"], ["All files", "*.*"]],
    "parameters": [
        {
            "name": "draw_speed",
            "flag": "--draw-speed",
            "type": "int",
            "default": 3000,
            "min": 100,
            "max": 10000,
            "step": 100,
            "unit": "mm/min",
            "label": "Drawing Speed",
            "description": "Feed rate when pen is down and drawing"
        },
        {
            "name": "travel_speed",
            "flag": "--travel-speed",
            "type": "int",
            "default": 5000,
            "min": 100,
            "max": 15000,
            "step": 100,
            "unit": "mm/min",
            "label": "Travel Speed",
            "description": "Feed rate when pen is up and moving between shapes"
        },
        {
            "name": "z_speed",
            "flag": "--z-speed",
            "type": "int",
            "default": 3000,
            "min": 100,
            "max": 10000,
            "step": 100,
            "unit": "mm/min",
            "label": "Z Speed",
            "description": "Feed rate for Z-axis pen up/down movements"
        },
        {
            "name": "pen_down_z",
            "flag": "--pen-down-z",
            "type": "float",
            "default": -1.0,
            "min": -50.0,
            "max": 50.0,
            "step": 0.5,
            "unit": "mm",
            "label": "Pen Down Z",
            "description": "Z position when pen is down (drawing)"
        },
        {
            "name": "pen_up_z",
            "flag": "--pen-up-z",
            "type": "float",
            "default": 3.0,
            "min": -50.0,
            "max": 50.0,
            "step": 0.5,
            "unit": "mm",
            "label": "Pen Up Z",
            "description": "Z position when pen is up (travelling)"
        },
        {
            "name": "width_mm",
            "flag": "--width-mm",
            "type": "float",
            "default": 100.0,
            "min": 10.0,
            "max": 1000.0,
            "step": 1.0,
            "unit": "mm",
            "label": "Output Width",
            "description": "Output drawing width in mm. Height is computed from aspect ratio."
        },
        {
            "name": "height_scale",
            "flag": "--height-scale",
            "type": "float",
            "default": 1.0,
            "min": 0.1,
            "max": 5.0,
            "step": 0.1,
            "unit": "ratio",
            "label": "Height Scale",
            "description": "Aspect ratio multiplier compared to original image. 1.0 keeps the original aspect ratio."
        },
        {
            "name": "origin_x",
            "flag": "--origin-x",
            "type": "float",
            "default": 0.0,
            "min": -500.0,
            "max": 500.0,
            "step": 1.0,
            "unit": "mm",
            "label": "Origin X Offset",
            "description": "Translate the drawing origin horizontally (mm)"
        },
        {
            "name": "origin_y",
            "flag": "--origin-y",
            "type": "float",
            "default": 0.0,
            "min": -500.0,
            "max": 500.0,
            "step": 1.0,
            "unit": "mm",
            "label": "Origin Y Offset",
            "description": "Translate the drawing origin vertically (mm)"
        },
        {
            "name": "rapidmove",
            "flag": "--rapidmove",
            "type": "int",
            "default": 10,
            "min": 0,
            "max": 100,
            "step": 1,
            "unit": "mm",
            "label": "Rapid Move Threshold",
            "description": "Use G0 rapid moves when travelling more than this distance (0 = disabled)"
        }
    ]
}


# ===================================================================
# SVG SIZE DETECTION: Determine physical size in mm (as svg2gcode sees it)
# ===================================================================

def get_svg_dimensions(svg_path):
    """
    Determine the SVG's physical dimensions in millimetres - matching what
    svg2gcode uses internally as its coordinate space before applying --scale.

    svg2gcode converts all SVG coordinates to mm using the CSS unit rules:
      - 1px (or unitless) = 1/96 inch = 0.26458 mm
      - 1pt = 1/72 inch = 0.35278 mm
      - 1pc = 1/6 inch = 4.2333 mm
      - 1mm = 1 mm
      - 1cm = 10 mm
      - 1in = 25.4 mm

    The physical size determines the output extent at scale=1.0.
    Our --scale values then multiply this to reach the desired --width-mm.

    Returns (width_mm, height_mm) as svg2gcode would interpret them.
    """
    import xml.etree.ElementTree as ET

    tree = ET.parse(svg_path)
    root = tree.getroot()

    # Unit to mm conversion factors (CSS/SVG standard at 96 dpi)
    UNIT_TO_MM = {
        'mm': 1.0,
        'cm': 10.0,
        'in': 25.4,
        'pt': 25.4 / 72.0,     # 0.35278
        'pc': 25.4 / 6.0,      # 4.2333
        'px': 25.4 / 96.0,     # 0.26458
    }

    def parse_length_to_mm(val):
        """Parse an SVG length value and convert to mm."""
        if val is None:
            return None
        val = val.strip()
        # Handle percentage (unsupported)
        if val.endswith('%'):
            return None
        # Try each unit suffix
        for unit, factor in UNIT_TO_MM.items():
            if val.endswith(unit):
                try:
                    return float(val[:-len(unit)].strip()) * factor
                except ValueError:
                    return None
        # No unit = treated as px (SVG spec: unitless = px)
        try:
            return float(val) * UNIT_TO_MM['px']
        except ValueError:
            return None

    width_mm = None
    height_mm = None

    # Try width/height attributes first
    w_attr = root.get('width')
    h_attr = root.get('height')

    width_mm = parse_length_to_mm(w_attr)
    height_mm = parse_length_to_mm(h_attr)

    # If width/height are valid, use them
    if width_mm and height_mm and width_mm > 0 and height_mm > 0:
        return (width_mm, height_mm)

    # Fall back to viewBox (treat viewBox units as px -> convert to mm)
    viewbox = root.get('viewBox')
    if viewbox:
        parts = re.split(r'[\s,]+', viewbox.strip())
        if len(parts) == 4:
            try:
                vb_w = float(parts[2])
                vb_h = float(parts[3])
                if vb_w > 0 and vb_h > 0:
                    # viewBox without width/height: units are px
                    width_mm = vb_w * UNIT_TO_MM['px']
                    height_mm = vb_h * UNIT_TO_MM['px']
                    return (width_mm, height_mm)
            except ValueError:
                pass

    # Last resort
    print("WARNING: Could not determine SVG dimensions, assuming 100x100 mm")
    return (100.0, 100.0)

# ===================================================================
# SVG PREPROCESSING: Convert basic shapes to <path> elements
# ===================================================================

def preprocess_svg(input_path, tmp_dir):
    """
    Parse the SVG and convert non-path elements (polyline, polygon, line,
    rect, circle, ellipse) into <path> elements that svg2gcode can process.
    Returns path to the preprocessed SVG file.
    """
    import xml.etree.ElementTree as ET

    print("Preprocessing SVG: converting shapes to <path> elements...")

    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    ET.register_namespace('inkscape', 'http://www.inkscape.org/namespaces/inkscape')
    ET.register_namespace('sodipodi', 'http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd')

    tree = ET.parse(input_path)
    root = tree.getroot()

    # Handle SVG namespace
    svg_ns = '{http://www.w3.org/2000/svg}'

    converted_count = 0

    def points_to_path_d(points_str, close=False):
        """Convert a points attribute string to a path d attribute."""
        points_str = points_str.strip()
        if not points_str:
            return ""
        # Points can be separated by spaces, commas, or both
        parts = re.split(r'[\s,]+', points_str)
        if len(parts) < 2:
            return ""
        # Build path: M x1 y1 L x2 y2 L x3 y3 ...
        d = f"M {parts[0]} {parts[1]}"
        for i in range(2, len(parts) - 1, 2):
            d += f" L {parts[i]} {parts[i+1]}"
        if close:
            d += " Z"
        return d

    def rect_to_path_d(elem):
        """Convert rect attributes to path d."""
        x = float(elem.get('x', '0'))
        y = float(elem.get('y', '0'))
        w = float(elem.get('width', '0'))
        h = float(elem.get('height', '0'))
        rx = elem.get('rx')
        ry = elem.get('ry')

        if rx or ry:
            rx = float(rx) if rx else 0
            ry = float(ry) if ry else 0
            if rx and not ry:
                ry = rx
            if ry and not rx:
                rx = ry
            rx = min(rx, w / 2)
            ry = min(ry, h / 2)
            d = (f"M {x + rx} {y} "
                 f"L {x + w - rx} {y} "
                 f"A {rx} {ry} 0 0 1 {x + w} {y + ry} "
                 f"L {x + w} {y + h - ry} "
                 f"A {rx} {ry} 0 0 1 {x + w - rx} {y + h} "
                 f"L {x + rx} {y + h} "
                 f"A {rx} {ry} 0 0 1 {x} {y + h - ry} "
                 f"L {x} {y + ry} "
                 f"A {rx} {ry} 0 0 1 {x + rx} {y} Z")
        else:
            d = f"M {x} {y} L {x + w} {y} L {x + w} {y + h} L {x} {y + h} Z"
        return d

    def circle_to_path_d(elem):
        """Convert circle to path using two arcs."""
        cx = float(elem.get('cx', '0'))
        cy = float(elem.get('cy', '0'))
        r = float(elem.get('r', '0'))
        d = (f"M {cx - r} {cy} "
             f"A {r} {r} 0 1 0 {cx + r} {cy} "
             f"A {r} {r} 0 1 0 {cx - r} {cy} Z")
        return d

    def ellipse_to_path_d(elem):
        """Convert ellipse to path using two arcs."""
        cx = float(elem.get('cx', '0'))
        cy = float(elem.get('cy', '0'))
        rx = float(elem.get('rx', '0'))
        ry = float(elem.get('ry', '0'))
        d = (f"M {cx - rx} {cy} "
             f"A {rx} {ry} 0 1 0 {cx + rx} {cy} "
             f"A {rx} {ry} 0 1 0 {cx - rx} {cy} Z")
        return d

    def line_to_path_d(elem):
        """Convert line to path."""
        x1 = elem.get('x1', '0')
        y1 = elem.get('y1', '0')
        x2 = elem.get('x2', '0')
        y2 = elem.get('y2', '0')
        return f"M {x1} {y1} L {x2} {y2}"

    def convert_element(elem, tag_local):
        """Convert a shape element to a path element in-place."""
        nonlocal converted_count

        d = None
        if tag_local == 'polyline':
            points = elem.get('points', '')
            d = points_to_path_d(points, close=False)
        elif tag_local == 'polygon':
            points = elem.get('points', '')
            d = points_to_path_d(points, close=True)
        elif tag_local == 'line':
            d = line_to_path_d(elem)
        elif tag_local == 'rect':
            d = rect_to_path_d(elem)
        elif tag_local == 'circle':
            d = circle_to_path_d(elem)
        elif tag_local == 'ellipse':
            d = ellipse_to_path_d(elem)

        if d:
            # Change the tag to path
            elem.tag = f"{svg_ns}path" if svg_ns in (elem.tag or '') else 'path'
            # Set the d attribute
            elem.set('d', d)
            # Remove shape-specific attributes that don't apply to path
            for attr in ['points', 'x', 'y', 'width', 'height', 'rx', 'ry',
                         'cx', 'cy', 'r', 'x1', 'y1', 'x2', 'y2']:
                if attr in elem.attrib:
                    del elem.attrib[attr]
            # Ensure it has a stroke if no style is set (so svg2gcode sees it)
            style = elem.get('style', '')
            stroke = elem.get('stroke', '')
            if not style and not stroke:
                elem.set('stroke', '#000000')
                elem.set('fill', 'none')
            converted_count += 1

    # Elements to convert (with and without namespace)
    shape_tags = ['polyline', 'polygon', 'line', 'rect', 'circle', 'ellipse']

    # Walk all elements recursively
    for elem in root.iter():
        tag = elem.tag
        # Strip namespace to get local name
        if '}' in tag:
            tag_local = tag.split('}', 1)[1]
        else:
            tag_local = tag

        if tag_local in shape_tags:
            convert_element(elem, tag_local)

    if converted_count == 0:
        print("  No shapes needed conversion (SVG may already use <path> elements)")
        return input_path  # Return original, no changes needed

    # Write preprocessed SVG to temp file
    output_svg = os.path.join(tmp_dir, "preprocessed.svg")
    tree.write(output_svg, xml_declaration=True, encoding='unicode')

    print(f"  Converted {converted_count} shape(s) to <path> elements")
    return output_svg


# ===================================================================
# HELPER: Check svg2gcode availability
# ===================================================================

def find_svg2gcode():
    """Locate the svg2gcode executable."""
    # Try common names
    for cmd in ["svg2gcode", "runsvg2gcode"]:
        try:
            result = subprocess.run(
                [cmd, "-V"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Try as a Python module
    try:
        result = subprocess.run(
            [sys.executable, "-m", "svg2gcode", "-V"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return [sys.executable, "-m", "svg2gcode"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


# ===================================================================
# TRAVEL OPTIMIZATION: Nearest-neighbor path ordering
# ===================================================================

def optimize_travel_order(paths):
    """
    Reorder a list of parsed path segments using a nearest-neighbor heuristic.
    Each path is a list of G-code lines. The function extracts the start
    coordinate (first move) of each path and reorders them so that after
    finishing one path, the next path chosen is the one whose START is closest
    to the END of the current path.

    Returns the reordered list of paths.
    """
    if len(paths) <= 1:
        return paths

    re_coord_xy = re.compile(
        r'(?:X\s*(-?\d+\.?\d*))\s*(?:Y\s*(-?\d+\.?\d*))', re.IGNORECASE
    )

    def get_first_coord(path_lines):
        """Get the first XY coordinate in a path (start point)."""
        for line in path_lines:
            m = re_coord_xy.search(line)
            if m:
                return (float(m.group(1)), float(m.group(2)))
        return (0.0, 0.0)

    def get_last_coord(path_lines):
        """Get the last XY coordinate in a path (end point)."""
        last = (0.0, 0.0)
        for line in path_lines:
            m = re_coord_xy.search(line)
            if m:
                last = (float(m.group(1)), float(m.group(2)))
        return last

    def distance(p1, p2):
        return math.hypot(p2[0] - p1[0], p2[1] - p1[1])

    # Pre-compute start and end coordinates for each path
    starts = [get_first_coord(p) for p in paths]
    ends = [get_last_coord(p) for p in paths]

    # Nearest-neighbor greedy ordering starting from origin (0, 0)
    n = len(paths)
    visited = [False] * n
    order = []
    current_pos = (0.0, 0.0)

    for _ in range(n):
        best_idx = -1
        best_dist = float('inf')
        for i in range(n):
            if visited[i]:
                continue
            d = distance(current_pos, starts[i])
            if d < best_dist:
                best_dist = d
                best_idx = i
        visited[best_idx] = True
        order.append(best_idx)
        current_pos = ends[best_idx]

    # Calculate improvement
    original_travel = sum(
        distance(ends[i], starts[i + 1]) for i in range(n - 1)
    )
    optimized_travel = sum(
        distance(ends[order[i]], starts[order[i + 1]]) for i in range(n - 1)
    )
    if original_travel > 0:
        saved_pct = (1 - optimized_travel / original_travel) * 100
        print(f"  Travel optimization: {saved_pct:.1f}% reduction in travel distance")
        print(f"    Original travel: {original_travel:.1f} mm | Optimized: {optimized_travel:.1f} mm")
    else:
        print(f"  Travel optimization applied (single path or zero travel)")

    return [paths[i] for i in order]


# ===================================================================
# CORE: Post-process svg2gcode output for plotter
# ===================================================================

def postprocess_gcode(raw_gcode_path, output_path, args, output_width_mm, output_height_mm):
    """
    Read the G-code produced by svg2gcode (laser-oriented) and rewrite it
    as plotter-friendly G-code with Z-axis pen up/down movements.

    Applies nearest-neighbor travel optimization to minimize pen-up travel
    between paths.

    Mapping:
      - M3/M4 (laser on)  -> Pen Down: G1 Z<pen_down_z> F<z_speed>
      - M5 (laser off)     -> Pen Up:   G1 Z<pen_up_z> F<z_speed>
      - G0 (rapid move)    -> Pen is already up, travel at travel_speed
      - G1 with S-value    -> Strip S-value, use draw_speed
      - S### on G1 lines   -> Removed (no laser power for plotter)
    """

    pen_up_cmd = f"G1 Z{args.pen_up_z} F{args.z_speed}"
    pen_down_cmd = f"G1 Z{args.pen_down_z} F{args.z_speed}"

    with open(raw_gcode_path, 'r') as f:
        raw_lines = f.readlines()

    output_lines = []
    pen_is_down = False
    move_count = 0
    path_count = 0

    # -- Write plotter header --
    output_lines.append("; ==========================================")
    output_lines.append("; SVG to Plotter G-code (svg2gcode backend)")
    output_lines.append(f"; Input: {os.path.basename(args.input)}")
    output_lines.append(f"; Size: {output_width_mm:.1f} x {output_height_mm:.1f} mm")
    output_lines.append(f"; Draw speed: {args.draw_speed} mm/min")
    output_lines.append(f"; Travel speed: {args.travel_speed} mm/min")
    output_lines.append(f"; Z speed: {args.z_speed} mm/min")
    output_lines.append(f"; Pen down Z: {args.pen_down_z} mm")
    output_lines.append(f"; Pen up Z: {args.pen_up_z} mm")
    output_lines.append(f"; Width: {args.width_mm} mm | Height scale: {args.height_scale}")
    if args.origin_x != 0.0 or args.origin_y != 0.0:
        output_lines.append(f"; Origin offset: ({args.origin_x}, {args.origin_y}) mm")
    if args.color_coded:
        output_lines.append(f"; Color coded: {args.color_coded}")
    output_lines.append("; ==========================================")
    output_lines.append("")
    output_lines.append("G21          ; Millimetres")
    output_lines.append("G90          ; Absolute positioning")
    output_lines.append("G28          ; Home")
    output_lines.append("")
    output_lines.append(f"{pen_up_cmd}   ; Pen up (start position)")
    output_lines.append("")

    # Regex patterns for parsing
    re_g0 = re.compile(r'^G0\s*(.*)', re.IGNORECASE)
    re_g1 = re.compile(r'^G1\s*(.*)', re.IGNORECASE)
    re_m3 = re.compile(r'^M[34]\b', re.IGNORECASE)
    re_m5 = re.compile(r'^M5\b', re.IGNORECASE)
    re_s_value = re.compile(r'\bS\d+\.?\d*\b', re.IGNORECASE)
    re_f_value = re.compile(r'\bF\d+\.?\d*\b', re.IGNORECASE)
    re_comment = re.compile(r'^\s*;')
    re_setup = re.compile(r'^G0\s+G17\s+G40\s+G54\s+G94', re.IGNORECASE)
    re_g21 = re.compile(r'^G21\b', re.IGNORECASE)
    re_g90 = re.compile(r'^G90\b', re.IGNORECASE)
    re_m2 = re.compile(r'^M2\b', re.IGNORECASE)
    re_coord = re.compile(r'[XY]\s*-?\d+\.?\d*', re.IGNORECASE)

    # -- First pass: parse raw G-code into path segments --
    path_segments = []   # list of lists of G-code move strings
    current_segment = []
    pen_active = False   # tracks laser on/off in raw gcode

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue

        # Skip setup/header lines from svg2gcode
        if re_comment.match(line):
            if "path" in line.lower() and "ignore" not in line.lower():
                path_count += 1
            continue
        if re_setup.match(line) or re_g21.match(line) or re_g90.match(line):
            continue
        if re_m2.match(line):
            continue

        # M3/M4 = laser on = start of drawing
        if re_m3.match(line):
            pen_active = True
            continue

        # M5 = laser off = end of a drawing segment
        if re_m5.match(line):
            pen_active = False
            if current_segment:
                path_segments.append(current_segment)
                current_segment = []
            continue

        # G0 = rapid/travel move (pen is up, travelling to next path start)
        if re_g0.match(line):
            coords = re_coord.findall(line)
            if coords:
                coord_str = " ".join(coords)
                current_segment.append(f"G0 {coord_str} F{args.travel_speed}")
            continue

        # G1 = drawing move
        if re_g1.match(line):
            coords = re_coord.findall(line)
            if coords:
                coord_str = " ".join(coords)
                current_segment.append(f"G1 {coord_str} F{args.draw_speed}")
            continue

        # Bare coordinate lines (continuation G1)
        if re_coord.search(line) and not line.startswith(';'):
            clean = re_s_value.sub('', line)
            clean = re_f_value.sub('', clean).strip()
            if clean:
                coords = re_coord.findall(clean)
                if coords:
                    coord_str = " ".join(coords)
                    current_segment.append(f"G1 {coord_str} F{args.draw_speed}")
            continue

    # Don't forget the last segment if file ended without M5
    if current_segment:
        path_segments.append(current_segment)

    # -- Optimize path order using nearest-neighbor --
    print(f"  Found {len(path_segments)} path segment(s) to optimize...")
    path_segments = optimize_travel_order(path_segments)

    # -- Second pass: emit optimized G-code --
    for segment in path_segments:
        if not segment:
            continue

        for gline in segment:
            if gline.startswith("G0"):
                # Travel move - pen stays up
                output_lines.append(gline)
                move_count += 1
            else:
                # First G1 means we need pen down
                if not pen_is_down:
                    output_lines.append(f"{pen_down_cmd}   ; Pen down")
                    pen_is_down = True
                output_lines.append(gline)
                move_count += 1

        # Lift pen at end of this path segment
        if pen_is_down:
            output_lines.append(f"{pen_up_cmd}   ; Pen up")
            pen_is_down = False

    # -- Write plotter footer --
    output_lines.append("")
    if pen_is_down:
        output_lines.append(f"{pen_up_cmd}   ; Pen up (end)")
    output_lines.append(f"G0 X0 Y0 F{args.travel_speed}   ; Return to origin")
    output_lines.append("M18              ; Disable motors")

    # Write final output
    with open(output_path, 'w') as f:
        f.write("\n".join(output_lines) + "\n")

    return move_count, path_count


# ===================================================================
# MAIN CONVERSION
# ===================================================================

def convert(args):
    """Main conversion: call svg2gcode then post-process for plotter."""

    input_path = args.input
    output_path = args.output

    # Validate input file
    if not os.path.isfile(input_path):
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if not input_path.lower().endswith('.svg'):
        print(f"WARNING: Input file does not have .svg extension: {input_path}")

    # -- Determine SVG dimensions and compute scale factors --
    svg_width, svg_height = get_svg_dimensions(input_path)
    print(f"SVG native size: {svg_width:.2f} x {svg_height:.2f} (user units)")

    # Compute scale factors from width_mm and height_scale
    # scale_x maps the SVG width to the desired output width in mm
    scale_x = args.width_mm / svg_width
    # scale_y preserves aspect ratio (same as scale_x) then applies height_scale
    scale_y = scale_x * args.height_scale

    output_width_mm = args.width_mm
    output_height_mm = svg_height * scale_y

    print(f"Output size: {output_width_mm:.1f} x {output_height_mm:.1f} mm")
    print(f"Scale factors: X={scale_x:.4f}  Y={scale_y:.4f}")

    print(f"\nLoading: {input_path}")
    print(f"Drawing speed: {args.draw_speed} mm/min")
    print(f"Travel speed: {args.travel_speed} mm/min")
    print(f"Z speed: {args.z_speed} mm/min")
    print(f"Pen down Z: {args.pen_down_z} mm | Pen up Z: {args.pen_up_z} mm")

    # Find svg2gcode
    svg2gcode_cmd = find_svg2gcode()
    if svg2gcode_cmd is None:
        print(
            "ERROR: svg2gcode not found. Please ensure svg2gcode is installed and on PATH.",
            file=sys.stderr
        )
        sys.exit(1)

    if isinstance(svg2gcode_cmd, str):
        svg2gcode_cmd = [svg2gcode_cmd]

    print(f"Using svg2gcode backend: {' '.join(svg2gcode_cmd)}")

    # Create temporary directory for intermediate files
    tmp_dir = tempfile.mkdtemp(prefix="svg2plotter_")
    tmp_gcode = os.path.join(tmp_dir, "raw_output.gc")

    # Preprocess SVG: convert polyline/polygon/line/rect/circle/ellipse to <path>
    processed_svg = preprocess_svg(input_path, tmp_dir)

    # Build svg2gcode command (use preprocessed SVG)
    cmd = list(svg2gcode_cmd) + [
        processed_svg,
        tmp_gcode,
        "--pathcut",        # Treat all paths as cuts (ensures they are drawn)
        "--nofill",         # Ignore fills (plotter cannot fill areas)
    ]

    # Add computed scale factors
    cmd += ["--scale", str(scale_x), str(scale_y)]
    print(f"Scale: {scale_x:.4f} x {scale_y:.4f}")

    # Add origin offset if set (only when selfcenter is not active)
    if not args.selfcenter and (args.origin_x != 0.0 or args.origin_y != 0.0):
        cmd += ["--origin", str(args.origin_x), str(args.origin_y)]
        print(f"Origin offset: ({args.origin_x}, {args.origin_y}) mm")

    # Self-center option (conflicts with --origin)
    if args.selfcenter:
        cmd += ["--selfcenter"]
        print("Self-centering enabled")

    # Rapid move threshold
    if args.rapidmove != 10:  # 10 is svg2gcode default
        cmd += ["--rapidmove", str(args.rapidmove)]

    # Color coded actions - translate plotter terms to svg2gcode terms
    if args.color_coded:
        # User specifies "draw" action; translate to "cut" for svg2gcode
        translated = args.color_coded.replace(" = draw", " = cut")
        translated = translated.replace("= draw", "= cut")
        # Remove --pathcut when using color_coded (they conflict in svg2gcode)
        cmd = [c for c in cmd if c != "--pathcut"]
        cmd += ["--color_coded", translated]
        print(f"Color coded: {args.color_coded}")

    # Set cutting speed (svg2gcode uses this for path traversal speed)
    cmd += ["--cuttingspeed", str(args.draw_speed)]

    print(f"\nRunning svg2gcode...")
    print(f"Command: {' '.join(cmd)}")

    # Pre-check: does the preprocessed SVG contain any <path> elements?
    try:
        with open(processed_svg, 'r', encoding='utf-8', errors='replace') as svgf:
            svg_content = svgf.read()
        has_paths = bool(re.search(r'<(svg:)?path\b', svg_content, re.IGNORECASE))
        has_images = bool(re.search(r'<(svg:)?image\b', svg_content, re.IGNORECASE))
        if not has_paths and not has_images:
            print("WARNING: SVG contains no drawable elements!")
            print("  Even after preprocessing, no <path> or <image> elements found.")
            print("  The SVG may be empty or use unsupported element types.")
            print("")
            print("ERROR: No convertible elements found in SVG.", file=sys.stderr)
            sys.exit(1)
        if has_paths:
            path_count_svg = len(re.findall(r'<(svg:)?path\b', svg_content, re.IGNORECASE))
            print(f"Found {path_count_svg} <path> element(s) ready for conversion")
        if has_images:
            print("Found <image> element(s) in SVG (will be skipped for plotter)")
    except Exception as e:
        print(f"WARNING: Could not pre-check SVG contents: {e}")

    # Execute svg2gcode
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
    except subprocess.TimeoutExpired:
        print("ERROR: svg2gcode timed out after 120 seconds", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(
            "ERROR: svg2gcode executable not found. Ensure svg2gcode is installed.",
            file=sys.stderr
        )
        sys.exit(1)

    if result.stdout:
        print(result.stdout)

    # Always print stderr so the user can see svg2gcode warnings
    if result.stderr:
        print(f"[svg2gcode stderr]: {result.stderr}")

    if result.returncode != 0:
        print(f"ERROR: svg2gcode failed (exit code {result.returncode})",
              file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(tmp_gcode):
        print("ERROR: svg2gcode did not produce output file.", file=sys.stderr)
        print("  This usually means the SVG has no elements svg2gcode can process.",
              file=sys.stderr)
        print("  Ensure your SVG contains <path> elements (not just basic shapes).",
              file=sys.stderr)
        print("  In Inkscape: Select All -> Path -> Object to Path", file=sys.stderr)
        sys.exit(1)

    raw_size = os.path.getsize(tmp_gcode)
    print(f"svg2gcode produced {raw_size} bytes of raw G-code")

    # Post-process: convert laser gcode to plotter gcode
    print("Post-processing for plotter (replacing laser commands with Z-axis moves)...")
    move_count, path_count = postprocess_gcode(tmp_gcode, output_path, args,
                                                output_width_mm, output_height_mm)

    # Clean up temp files
    try:
        os.remove(tmp_gcode)
        if processed_svg != input_path and os.path.isfile(processed_svg):
            os.remove(processed_svg)
        os.rmdir(tmp_dir)
    except OSError:
        pass

    final_size = os.path.getsize(output_path)
    print(f"\nConversion complete!")
    print(f"Output size: {output_width_mm:.1f} x {output_height_mm:.1f} mm")
    print(f"Paths processed: {path_count}")
    print(f"Total moves: {move_count}")
    print(f"Output file size: {final_size} bytes")
    print(f"G-code written to: {output_path}")


# ===================================================================
# MAIN - handles --get-params and normal execution
# ===================================================================

def main():
    # Protocol: respond to --get-params
    if "--get-params" in sys.argv:
        print(json.dumps(CONVERTER_INFO, indent=2))
        sys.exit(0)

    # Normal argument parsing
    parser = argparse.ArgumentParser(
        description=CONVERTER_INFO["description"],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s drawing.svg output.gcode
  %(prog)s drawing.svg output.gcode --draw-speed 2000 --pen-down-z -2.0
  %(prog)s drawing.svg output.gcode --width-mm 150 --height-scale 1.0
  %(prog)s drawing.svg output.gcode --color-coded "black = ignore red = draw"
        """
    )
    parser.add_argument("input", help="Input SVG file")
    parser.add_argument("output", help="Output G-code file", nargs="?",
                        default="output.gcode")
    parser.add_argument("--draw-speed", type=int, default=3000,
                        help="Drawing speed in mm/min (default: 3000)")
    parser.add_argument("--travel-speed", type=int, default=5000,
                        help="Travel speed in mm/min (default: 5000)")
    parser.add_argument("--z-speed", type=int, default=3000, dest="z_speed",
                        help="Z-axis speed in mm/min (default: 3000)")
    parser.add_argument("--pen-down-z", type=float, default=-1.0, dest="pen_down_z",
                        help="Z position for pen down in mm (default: -1.0)")
    parser.add_argument("--pen-up-z", type=float, default=3.0, dest="pen_up_z",
                        help="Z position for pen up in mm (default: 3.0)")
    parser.add_argument("--width-mm", type=float, default=100.0, dest="width_mm",
                        help="Output drawing width in mm (default: 100). Height is computed from aspect ratio.")
    parser.add_argument("--height-scale", type=float, default=1.0, dest="height_scale",
                        help="Height aspect ratio multiplier (default: 1.0 = keep original aspect ratio)")
    parser.add_argument("--origin-x", type=float, default=0.0, dest="origin_x",
                        help="X origin offset in mm (default: 0)")
    parser.add_argument("--origin-y", type=float, default=0.0, dest="origin_y",
                        help="Y origin offset in mm (default: 0)")
    parser.add_argument("--color-coded", type=str, default="", dest="color_coded",
                        help='Color actions, e.g. "black = ignore red = draw"')
    parser.add_argument("--selfcenter", action="store_true", default=False,
                        help="Center drawing around origin")
    parser.add_argument("--rapidmove", type=int, default=10,
                        help="Rapid move threshold in mm (default: 10)")

    args = parser.parse_args()

    convert(args)


if __name__ == "__main__":
    main()

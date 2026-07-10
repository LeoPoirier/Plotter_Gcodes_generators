#!/usr/bin/env python3
"""
svg_to_plotter_gcode.py — SVG to Plotter G-code converter (svg2gcode backend)

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

# ═══════════════════════════════════════════════════════════════════
# CONVERTER METADATA
# ═══════════════════════════════════════════════════════════════════
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
    "version": "1.1",
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
            "name": "scale_x",
            "flag": "--scale-x",
            "type": "float",
            "default": 1.0,
            "min": 0.1,
            "max": 10.0,
            "step": 0.1,
            "unit": "\u00d7",
            "label": "Scale X",
            "description": "Horizontal scale factor applied to the SVG"
        },
        {
            "name": "scale_y",
            "flag": "--scale-y",
            "type": "float",
            "default": 1.0,
            "min": 0.1,
            "max": 10.0,
            "step": 0.1,
            "unit": "\u00d7",
            "label": "Scale Y",
            "description": "Vertical scale factor applied to the SVG"
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

# ═══════════════════════════════════════════════════════════════════
# SVG PREPROCESSING: Convert basic shapes to <path> elements
# ═══════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════
# HELPER: Check svg2gcode availability
# ═══════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════
# CORE: Post-process svg2gcode output for plotter
# ═══════════════════════════════════════════════════════════════════

def postprocess_gcode(raw_gcode_path, output_path, args):
    """
    Read the G-code produced by svg2gcode (laser-oriented) and rewrite it
    as plotter-friendly G-code with Z-axis pen up/down movements.

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

    # ── Write plotter header ──
    output_lines.append("; ==========================================")
    output_lines.append("; SVG to Plotter G-code (svg2gcode backend)")
    output_lines.append(f"; Input: {os.path.basename(args.input)}")
    output_lines.append(f"; Draw speed: {args.draw_speed} mm/min")
    output_lines.append(f"; Travel speed: {args.travel_speed} mm/min")
    output_lines.append(f"; Z speed: {args.z_speed} mm/min")
    output_lines.append(f"; Pen down Z: {args.pen_down_z} mm")
    output_lines.append(f"; Pen up Z: {args.pen_up_z} mm")
    if args.scale_x != 1.0 or args.scale_y != 1.0:
        output_lines.append(f"; Scale: {args.scale_x} x {args.scale_y}")
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

    for line in raw_lines:
        line = line.strip()

        # Skip empty lines
        if not line:
            continue

        # Pass through svg2gcode comment lines about paths (useful info)
        if re_comment.match(line):
            if "path" in line.lower() or "color_coded" in line.lower():
                output_lines.append(line)
                if "path" in line.lower() and "ignore" not in line.lower():
                    path_count += 1
            continue

        # Skip svg2gcode setup lines (we wrote our own header)
        if re_setup.match(line) or re_g21.match(line) or re_g90.match(line):
            continue

        # Skip M2 (program end) - we write our own footer
        if re_m2.match(line):
            continue

        # ── M3/M4 = Laser on -> Pen Down (Z-axis) ──
        if re_m3.match(line):
            if not pen_is_down:
                output_lines.append(f"{pen_down_cmd}   ; Pen down")
                pen_is_down = True
            continue

        # ── M5 = Laser off -> Pen Up (Z-axis) ──
        if re_m5.match(line):
            if pen_is_down:
                output_lines.append(f"{pen_up_cmd}   ; Pen up")
                pen_is_down = False
            continue

        # ── G0 = Rapid move (pen should be up) ──
        if re_g0.match(line):
            # Ensure pen is up before travel
            if pen_is_down:
                output_lines.append(f"{pen_up_cmd}   ; Pen up")
                pen_is_down = False

            # Strip S and F values, add our travel speed
            coords = re_coord.findall(line)
            if coords:
                coord_str = " ".join(coords)
                output_lines.append(f"G0 {coord_str} F{args.travel_speed}")
                move_count += 1
            continue

        # ── G1 = Linear move (drawing) ──
        if re_g1.match(line):
            # Strip S-values (laser power not relevant for plotter)
            # Replace F-value with our draw speed
            coords = re_coord.findall(line)
            if coords:
                coord_str = " ".join(coords)
                output_lines.append(f"G1 {coord_str} F{args.draw_speed}")
                move_count += 1
            continue

        # ── Lines that are just coordinates (continuation G1) ──
        # svg2gcode sometimes emits bare "X## Y##" lines after G1
        if re_coord.search(line) and not line.startswith(';'):
            clean = re_s_value.sub('', line)
            clean = re_f_value.sub('', clean).strip()
            if clean:
                coords = re_coord.findall(clean)
                if coords:
                    coord_str = " ".join(coords)
                    output_lines.append(f"G1 {coord_str} F{args.draw_speed}")
                    move_count += 1
            continue

    # ── Write plotter footer ──
    output_lines.append("")
    if pen_is_down:
        output_lines.append(f"{pen_up_cmd}   ; Pen up (end)")
    output_lines.append(f"G0 X0 Y0 F{args.travel_speed}   ; Return to origin")
    output_lines.append("M18              ; Disable motors")

    # Write final output
    with open(output_path, 'w') as f:
        f.write("\n".join(output_lines) + "\n")

    return move_count, path_count


# ═══════════════════════════════════════════════════════════════════
# MAIN CONVERSION
# ═══════════════════════════════════════════════════════════════════

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

    print(f"Loading: {input_path}")
    print(f"Drawing speed: {args.draw_speed} mm/min")
    print(f"Travel speed: {args.travel_speed} mm/min")
    print(f"Z speed: {args.z_speed} mm/min")
    print(f"Pen down Z: {args.pen_down_z} mm | Pen up Z: {args.pen_up_z} mm")

    # Find svg2gcode
    svg2gcode_cmd = find_svg2gcode()
    if svg2gcode_cmd is None:
        print(
            "ERROR: svg2gcode not found. Install with: pip install svg2gcode",
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

    # Add scale if not default
    if args.scale_x != 1.0 or args.scale_y != 1.0:
        cmd += ["--scale", str(args.scale_x), str(args.scale_y)]
        print(f"Scale: {args.scale_x} x {args.scale_y}")

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
        # (svg2gcode's "cut" = follow the path = what a plotter draws)
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
            "ERROR: svg2gcode executable not found. "
            "Install with: pip install svg2gcode",
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
    move_count, path_count = postprocess_gcode(tmp_gcode, output_path, args)

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
    print(f"Paths processed: {path_count}")
    print(f"Total moves: {move_count}")
    print(f"Output size: {final_size} bytes")
    print(f"G-code written to: {output_path}")


# ═══════════════════════════════════════════════════════════════════
# MAIN — handles --get-params and normal execution
# ═══════════════════════════════════════════════════════════════════

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
  %(prog)s drawing.svg output.gcode --scale-x 2.0 --scale-y 2.0
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
    parser.add_argument("--scale-x", type=float, default=1.0, dest="scale_x",
                        help="X scale factor (default: 1.0)")
    parser.add_argument("--scale-y", type=float, default=1.0, dest="scale_y",
                        help="Y scale factor (default: 1.0)")
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

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
        },
        {
            "name": "hatch_fill",
            "flag": "--hatch-fill",
            "type": "bool",
            "default": False,
            "label": "Hatch Fill",
            "description": "Fill closed paths with parallel hatch lines"
        },
        {
            "name": "hatch_spacing",
            "flag": "--hatch-spacing",
            "type": "float",
            "default": 2.0,
            "min": 0.3,
            "max": 20.0,
            "step": 0.1,
            "unit": "mm",
            "label": "Hatch Spacing",
            "description": "Distance between hatch lines (in output mm, before scaling)"
        },
        {
            "name": "hatch_angle",
            "flag": "--hatch-angle",
            "type": "float",
            "default": 45.0,
            "min": 0.0,
            "max": 180.0,
            "step": 1.0,
            "unit": "\u00b0",
            "label": "Hatch Angle",
            "description": "Angle of hatch lines in degrees (0 = horizontal, 90 = vertical)"
        },
        {
            "name": "hatch_crosshatch",
            "flag": "--hatch-crosshatch",
            "type": "bool",
            "default": False,
            "label": "Crosshatch",
            "description": "Add a second set of hatch lines perpendicular to the first"
        }
    ]
}


# ===================================================================
# SVG SIZE DETECTION: Read viewBox/width/height from SVG
# ===================================================================

def get_svg_dimensions(svg_path):
    """
    Determine the SVG's physical dimensions in millimetres - matching what
    svg2gcode uses internally as its coordinate space before applying --scale.

    Also returns the viewBox dimensions (the coordinate space that SVG paths use).

    svg2gcode converts all SVG coordinates to mm using the CSS unit rules:
      - 1px (or unitless) = 1/96 inch = 0.26458 mm
      - 1pt = 1/72 inch = 0.35278 mm
      - 1pc = 1/6 inch = 4.2333 mm
      - 1mm = 1 mm
      - 1cm = 10 mm
      - 1in = 25.4 mm

    Returns (width_mm, height_mm, viewbox_width, viewbox_height).
    viewbox_width/height are the coordinate-space dimensions that path 'd' values use.
    If no viewBox is present, viewBox dims equal the unitless width/height numbers.
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
        if val.endswith('%'):
            return None
        for unit, factor in UNIT_TO_MM.items():
            if val.endswith(unit):
                try:
                    return float(val[:-len(unit)].strip()) * factor
                except ValueError:
                    return None
        try:
            return float(val) * UNIT_TO_MM['px']
        except ValueError:
            return None

    def parse_length_raw(val):
        """Strip units and return just the number (for viewBox fallback)."""
        if val is None:
            return None
        val = val.strip()
        for unit in ['px', 'pt', 'pc', 'mm', 'cm', 'in', 'em', 'ex']:
            if val.endswith(unit):
                val = val[:-len(unit)].strip()
                break
        if val.endswith('%'):
            return None
        try:
            return float(val)
        except ValueError:
            return None

    width_mm = None
    height_mm = None
    viewbox_w = None
    viewbox_h = None

    # Parse viewBox
    viewbox = root.get('viewBox')
    if viewbox:
        parts = re.split(r'[\s,]+', viewbox.strip())
        if len(parts) == 4:
            try:
                viewbox_w = float(parts[2])
                viewbox_h = float(parts[3])
            except ValueError:
                pass

    # Parse width/height attributes
    w_attr = root.get('width')
    h_attr = root.get('height')

    width_mm = parse_length_to_mm(w_attr)
    height_mm = parse_length_to_mm(h_attr)

    # If width/height are valid mm values
    if width_mm and height_mm and width_mm > 0 and height_mm > 0:
        # If no viewBox, the coordinate space is the raw number from width/height
        if viewbox_w is None or viewbox_h is None:
            raw_w = parse_length_raw(w_attr)
            raw_h = parse_length_raw(h_attr)
            viewbox_w = raw_w if raw_w else width_mm
            viewbox_h = raw_h if raw_h else height_mm
        return (width_mm, height_mm, viewbox_w, viewbox_h)

    # Fall back to viewBox (treat as px -> convert to mm)
    if viewbox_w and viewbox_h and viewbox_w > 0 and viewbox_h > 0:
        width_mm = viewbox_w * UNIT_TO_MM['px']
        height_mm = viewbox_h * UNIT_TO_MM['px']
        return (width_mm, height_mm, viewbox_w, viewbox_h)

    # Last resort
    print("WARNING: Could not determine SVG dimensions, assuming 100x100 mm")
    return (100.0, 100.0, 100.0, 100.0)


# ===================================================================
# HATCH FILL: Generate parallel fill lines inside closed paths
# ===================================================================

def _flatten_cubic_bezier(p0, p1, p2, p3, tolerance=0.5):
    """Flatten a cubic bezier curve into line segments.
    Points are (x, y) tuples. Returns list of (x, y) points."""
    # Use de Casteljau subdivision until flat enough
    def _subdivide(a, b, c, d, tol, pts):
        # Check flatness: distance of control points from line a-d
        ax, ay = a
        dx, dy = d
        ux = 3.0*b[0] - 2.0*a[0] - d[0]; uy = 3.0*b[1] - 2.0*a[1] - d[1]
        vx = 3.0*c[0] - 2.0*d[0] - a[0]; vy = 3.0*c[1] - 2.0*d[1] - a[1]
        u2 = ux*ux + uy*uy
        v2 = vx*vx + vy*vy
        if max(u2, v2) <= 16.0 * tol * tol:
            pts.append(d)
            return
        # Subdivide at t=0.5
        ab = ((a[0]+b[0])/2, (a[1]+b[1])/2)
        bc = ((b[0]+c[0])/2, (b[1]+c[1])/2)
        cd = ((c[0]+d[0])/2, (c[1]+d[1])/2)
        abc = ((ab[0]+bc[0])/2, (ab[1]+bc[1])/2)
        bcd = ((bc[0]+cd[0])/2, (bc[1]+cd[1])/2)
        abcd = ((abc[0]+bcd[0])/2, (abc[1]+bcd[1])/2)
        _subdivide(a, ab, abc, abcd, tol, pts)
        _subdivide(abcd, bcd, cd, d, tol, pts)

    pts = [p0]
    _subdivide(p0, p1, p2, p3, tolerance, pts)
    return pts


def _flatten_quadratic_bezier(p0, p1, p2, tolerance=0.5):
    """Flatten a quadratic bezier by converting to cubic."""
    # Q->C: CP1 = P0 + 2/3*(P1-P0), CP2 = P2 + 2/3*(P1-P2)
    cp1 = (p0[0] + 2.0/3.0*(p1[0]-p0[0]), p0[1] + 2.0/3.0*(p1[1]-p0[1]))
    cp2 = (p2[0] + 2.0/3.0*(p1[0]-p2[0]), p2[1] + 2.0/3.0*(p1[1]-p2[1]))
    return _flatten_cubic_bezier(p0, cp1, cp2, p2, tolerance)


def _flatten_arc(p0, rx, ry, x_rot, large_arc, sweep, p1, tolerance=0.5):
    """Flatten an SVG arc into line segments.
    Implements the SVG arc endpoint-to-center parameterisation."""
    import math

    x1, y1 = p0
    x2, y2 = p1

    # Handle degenerate cases
    if (x1 == x2 and y1 == y2) or rx == 0 or ry == 0:
        return [p1]

    rx = abs(rx)
    ry = abs(ry)
    phi = math.radians(x_rot)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    # Step 1: compute (x1', y1')
    dx2 = (x1 - x2) / 2.0
    dy2 = (y1 - y2) / 2.0
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2

    # Step 2: compute (cx', cy')
    x1p2 = x1p * x1p
    y1p2 = y1p * y1p
    rx2 = rx * rx
    ry2 = ry * ry

    # Ensure radii are large enough
    lam = x1p2 / rx2 + y1p2 / ry2
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
        rx2 = rx * rx
        ry2 = ry * ry

    num = max(0, rx2*ry2 - rx2*y1p2 - ry2*x1p2)
    den = rx2*y1p2 + ry2*x1p2
    if den == 0:
        return [p1]
    sq = math.sqrt(num / den)
    if large_arc == sweep:
        sq = -sq
    cxp = sq * rx * y1p / ry
    cyp = -sq * ry * x1p / rx

    # Step 3: compute (cx, cy) from (cx', cy')
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    # Step 4: compute theta1 and dtheta
    def angle_vec(ux, uy, vx, vy):
        n = math.sqrt(ux*ux + uy*uy) * math.sqrt(vx*vx + vy*vy)
        if n == 0:
            return 0
        c = (ux*vx + uy*vy) / n
        c = max(-1.0, min(1.0, c))
        a = math.acos(c)
        if ux*vy - uy*vx < 0:
            a = -a
        return a

    theta1 = angle_vec(1, 0, (x1p - cxp)/rx, (y1p - cyp)/ry)
    dtheta = angle_vec(
        (x1p - cxp)/rx, (y1p - cyp)/ry,
        (-x1p - cxp)/rx, (-y1p - cyp)/ry
    )

    if not sweep and dtheta > 0:
        dtheta -= 2*math.pi
    elif sweep and dtheta < 0:
        dtheta += 2*math.pi

    # Step 5: emit points along the arc
    n_steps = max(4, int(abs(dtheta) / (2*math.acos(1.0 - tolerance / max(rx, ry))) + 1))
    pts = []
    for i in range(1, n_steps + 1):
        t = theta1 + dtheta * i / n_steps
        cos_t = math.cos(t)
        sin_t = math.sin(t)
        x = cos_phi * rx * cos_t - sin_phi * ry * sin_t + cx
        y = sin_phi * rx * cos_t + cos_phi * ry * sin_t + cy
        pts.append((x, y))

    return pts


def _parse_transform(transform_str):
    """Parse an SVG transform attribute into a 3x3 transformation matrix.
    Supports: translate, scale, rotate, matrix, skewX, skewY.
    Returns (a, b, c, d, e, f) representing the matrix:
        [a c e]
        [b d f]
        [0 0 1]
    """
    import math as _math

    def multiply(m1, m2):
        """Multiply two 2D affine matrices (each as (a,b,c,d,e,f))."""
        a1, b1, c1, d1, e1, f1 = m1
        a2, b2, c2, d2, e2, f2 = m2
        return (
            a1*a2 + c1*b2,
            b1*a2 + d1*b2,
            a1*c2 + c1*d2,
            b1*c2 + d1*d2,
            a1*e2 + c1*f2 + e1,
            b1*e2 + d1*f2 + f1
        )

    if not transform_str:
        return (1, 0, 0, 1, 0, 0)  # Identity

    result = (1, 0, 0, 1, 0, 0)  # Identity

    # Parse individual transforms
    import re as _re
    transforms = _re.findall(r'(\w+)\s*\(([^)]*)\)', transform_str)

    for func, args_str in transforms:
        args = [float(x) for x in _re.split(r'[\s,]+', args_str.strip()) if x]

        if func == 'translate':
            tx = args[0] if len(args) >= 1 else 0
            ty = args[1] if len(args) >= 2 else 0
            m = (1, 0, 0, 1, tx, ty)
        elif func == 'scale':
            sx = args[0] if len(args) >= 1 else 1
            sy = args[1] if len(args) >= 2 else sx
            m = (sx, 0, 0, sy, 0, 0)
        elif func == 'rotate':
            angle = _math.radians(args[0]) if len(args) >= 1 else 0
            cos_a = _math.cos(angle)
            sin_a = _math.sin(angle)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                # rotate around (cx, cy)
                m = multiply((1, 0, 0, 1, cx, cy),
                    multiply((cos_a, sin_a, -sin_a, cos_a, 0, 0),
                             (1, 0, 0, 1, -cx, -cy)))
            else:
                m = (cos_a, sin_a, -sin_a, cos_a, 0, 0)
        elif func == 'matrix':
            if len(args) >= 6:
                m = (args[0], args[1], args[2], args[3], args[4], args[5])
            else:
                m = (1, 0, 0, 1, 0, 0)
        elif func == 'skewX':
            angle = _math.radians(args[0]) if len(args) >= 1 else 0
            m = (1, 0, _math.tan(angle), 1, 0, 0)
        elif func == 'skewY':
            angle = _math.radians(args[0]) if len(args) >= 1 else 0
            m = (1, _math.tan(angle), 0, 1, 0, 0)
        else:
            m = (1, 0, 0, 1, 0, 0)

        result = multiply(result, m)

    return result


def _get_element_transform(elem, tree_root):
    """Get the cumulative transform for an element by walking up the tree.
    Returns (a, b, c, d, e, f) affine matrix."""

    def multiply(m1, m2):
        a1, b1, c1, d1, e1, f1 = m1
        a2, b2, c2, d2, e2, f2 = m2
        return (
            a1*a2 + c1*b2,
            b1*a2 + d1*b2,
            a1*c2 + c1*d2,
            b1*c2 + d1*d2,
            a1*e2 + c1*f2 + e1,
            b1*e2 + d1*f2 + f1
        )

    # Build parent map
    parent_map = {child: parent for parent in tree_root.iter() for child in parent}

    # Collect transforms from element up to root
    transforms = []
    current = elem
    while current is not None:
        t = current.get('transform', '')
        if t:
            transforms.append(_parse_transform(t))
        current = parent_map.get(current)

    # Multiply in reverse order (root transform first, then children)
    result = (1, 0, 0, 1, 0, 0)
    for t in reversed(transforms):
        result = multiply(result, t)

    return result


def _apply_transform_to_polygon(polygon, matrix):
    """Apply an affine transform matrix to all points in a polygon.
    Matrix is (a, b, c, d, e, f)."""
    a, b, c, d, e, f = matrix
    transformed = []
    for (x, y) in polygon:
        nx = a * x + c * y + e
        ny = b * x + d * y + f
        transformed.append((nx, ny))
    return transformed


def _parse_svg_path_to_polygons(d_attr, tolerance=0.5):
    """Parse an SVG path d attribute into a list of closed polygons.
    Each polygon is a list of (x, y) tuples.
    Only closed subpaths (ending with Z/z) are returned."""
    import re as _re

    # Tokenise the d attribute
    # Matches: command letter OR number (with optional sign, decimal, exponent)
    tokens = _re.findall(r'[MmZzLlHhVvCcSsQqTtAa]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d_attr)

    polygons = []
    current_poly = []
    pos = (0.0, 0.0)       # current point
    start = (0.0, 0.0)     # subpath start (for Z)
    last_ctrl = None        # last control point for S/T
    last_cmd = ''

    i = 0
    def next_num():
        nonlocal i
        if i < len(tokens):
            val = float(tokens[i])
            i += 1
            return val
        return 0.0

    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
        else:
            # Implicit repeat of previous command
            cmd = last_cmd
            if cmd == 'M':
                cmd = 'L'
            elif cmd == 'm':
                cmd = 'l'

        if cmd in ('M', 'm'):
            # If we have an open polygon from previous subpath, discard it
            # (we only want closed paths for hatching)
            x = next_num()
            y = next_num()
            if cmd == 'm':
                pos = (pos[0] + x, pos[1] + y)
            else:
                pos = (x, y)
            start = pos
            current_poly = [pos]
            last_ctrl = None

        elif cmd in ('Z', 'z'):
            pos = start
            if len(current_poly) >= 3:
                polygons.append(current_poly[:])
            current_poly = []
            last_ctrl = None

        elif cmd in ('L', 'l'):
            x = next_num()
            y = next_num()
            if cmd == 'l':
                pos = (pos[0] + x, pos[1] + y)
            else:
                pos = (x, y)
            current_poly.append(pos)
            last_ctrl = None

        elif cmd in ('H', 'h'):
            x = next_num()
            if cmd == 'h':
                pos = (pos[0] + x, pos[1])
            else:
                pos = (x, pos[1])
            current_poly.append(pos)
            last_ctrl = None

        elif cmd in ('V', 'v'):
            y = next_num()
            if cmd == 'v':
                pos = (pos[0], pos[1] + y)
            else:
                pos = (pos[0], y)
            current_poly.append(pos)
            last_ctrl = None

        elif cmd in ('C', 'c'):
            x1 = next_num(); y1 = next_num()
            x2 = next_num(); y2 = next_num()
            x = next_num(); y = next_num()
            if cmd == 'c':
                p1 = (pos[0]+x1, pos[1]+y1)
                p2 = (pos[0]+x2, pos[1]+y2)
                end = (pos[0]+x, pos[1]+y)
            else:
                p1 = (x1, y1)
                p2 = (x2, y2)
                end = (x, y)
            pts = _flatten_cubic_bezier(pos, p1, p2, end, tolerance)
            current_poly.extend(pts)
            pos = end
            last_ctrl = p2

        elif cmd in ('S', 's'):
            x2 = next_num(); y2 = next_num()
            x = next_num(); y = next_num()
            # Reflect previous control point
            if last_ctrl and last_cmd in ('C', 'c', 'S', 's'):
                p1 = (2*pos[0] - last_ctrl[0], 2*pos[1] - last_ctrl[1])
            else:
                p1 = pos
            if cmd == 's':
                p2 = (pos[0]+x2, pos[1]+y2)
                end = (pos[0]+x, pos[1]+y)
            else:
                p2 = (x2, y2)
                end = (x, y)
            pts = _flatten_cubic_bezier(pos, p1, p2, end, tolerance)
            current_poly.extend(pts)
            pos = end
            last_ctrl = p2

        elif cmd in ('Q', 'q'):
            x1 = next_num(); y1 = next_num()
            x = next_num(); y = next_num()
            if cmd == 'q':
                p1 = (pos[0]+x1, pos[1]+y1)
                end = (pos[0]+x, pos[1]+y)
            else:
                p1 = (x1, y1)
                end = (x, y)
            pts = _flatten_quadratic_bezier(pos, p1, end, tolerance)
            current_poly.extend(pts)
            pos = end
            last_ctrl = p1

        elif cmd in ('T', 't'):
            x = next_num(); y = next_num()
            if last_ctrl and last_cmd in ('Q', 'q', 'T', 't'):
                p1 = (2*pos[0] - last_ctrl[0], 2*pos[1] - last_ctrl[1])
            else:
                p1 = pos
            if cmd == 't':
                end = (pos[0]+x, pos[1]+y)
            else:
                end = (x, y)
            pts = _flatten_quadratic_bezier(pos, p1, end, tolerance)
            current_poly.extend(pts)
            pos = end
            last_ctrl = p1

        elif cmd in ('A', 'a'):
            rx_a = next_num(); ry_a = next_num()
            x_rot = next_num()
            large_arc = int(next_num())
            sweep_flag = int(next_num())
            x = next_num(); y = next_num()
            if cmd == 'a':
                end = (pos[0]+x, pos[1]+y)
            else:
                end = (x, y)
            pts = _flatten_arc(pos, rx_a, ry_a, x_rot, large_arc, sweep_flag, end, tolerance)
            current_poly.extend(pts)
            pos = end
            last_ctrl = None

        last_cmd = cmd

    return polygons


def _line_polygon_intersections(line_y, polygon):
    """Find all x-coordinates where a horizontal line at y=line_y
    intersects the polygon edges. Returns sorted list of x values."""
    intersections = []
    n = len(polygon)
    for i in range(n):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % n]
        y1, y2 = p1[1], p2[1]
        # Skip horizontal edges
        if y1 == y2:
            continue
        # Check if line_y is within this edge's y range
        if (y1 <= line_y < y2) or (y2 <= line_y < y1):
            # Linear interpolation for x at line_y
            t = (line_y - y1) / (y2 - y1)
            x = p1[0] + t * (p2[0] - p1[0])
            intersections.append(x)
    intersections.sort()
    return intersections


def generate_hatch_lines(polygons, spacing, angle_deg, bounds=None):
    """Generate hatch fill lines for a list of polygons.

    Args:
        polygons: list of polygons, each polygon is list of (x,y) tuples
        spacing: distance between hatch lines in SVG user units
        angle_deg: angle of hatch lines in degrees

    Returns:
        list of line segments [(x1,y1,x2,y2), ...]
    """
    import math

    if not polygons:
        return []

    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    # Rotate all polygons so hatch lines become horizontal
    def rotate(x, y):
        return (x*cos_a + y*sin_a, -x*sin_a + y*cos_a)

    def unrotate(x, y):
        return (x*cos_a - y*sin_a, x*sin_a + y*cos_a)

    rotated_polygons = []
    all_y = []
    for poly in polygons:
        rp = [rotate(p[0], p[1]) for p in poly]
        rotated_polygons.append(rp)
        for p in rp:
            all_y.append(p[1])

    if not all_y:
        return []

    y_min = min(all_y)
    y_max = max(all_y)

    # Generate horizontal scan lines in rotated space
    hatch_segments = []
    y = y_min + spacing / 2.0
    line_idx = 0
    while y < y_max:
        # Collect intersections from all polygons
        all_intersections = []
        for rpoly in rotated_polygons:
            xs = _line_polygon_intersections(y, rpoly)
            all_intersections.extend(xs)
        all_intersections.sort()

        # Pair up intersections (enter/exit)
        for j in range(0, len(all_intersections) - 1, 2):
            x1 = all_intersections[j]
            x2 = all_intersections[j + 1]
            if abs(x2 - x1) > 0.01:  # Skip degenerate segments
                # Alternate direction for zigzag (reduces travel)
                if line_idx % 2 == 0:
                    p1 = unrotate(x1, y)
                    p2 = unrotate(x2, y)
                else:
                    p1 = unrotate(x2, y)
                    p2 = unrotate(x1, y)
                hatch_segments.append((p1[0], p1[1], p2[0], p2[1]))

        y += spacing
        line_idx += 1

    return hatch_segments


def _segments_to_svg_path(segments):
    """Convert a list of (x1,y1,x2,y2) segments into an SVG path d attribute.
    Groups consecutive segments that share endpoints to minimise pen lifts."""
    if not segments:
        return ""

    # Build path string - each segment is M x1 y1 L x2 y2
    # But try to chain segments that connect end-to-start
    parts = []
    prev_end = None
    for (x1, y1, x2, y2) in segments:
        if prev_end and abs(x1 - prev_end[0]) < 0.01 and abs(y1 - prev_end[1]) < 0.01:
            # Continue from previous endpoint
            parts.append(f"L {x2:.4f} {y2:.4f}")
        else:
            parts.append(f"M {x1:.4f} {y1:.4f} L {x2:.4f} {y2:.4f}")
        prev_end = (x2, y2)

    return " ".join(parts)


def apply_hatch_fill(svg_path, tmp_dir, spacing, angle_deg, crosshatch=False, tolerance=0.5):
    """Apply hatch fill to all closed paths in an SVG file.

    Parses the SVG, finds closed paths, generates hatch lines, and adds them
    as new <path> elements to the SVG.

    Args:
        svg_path: path to the SVG file
        tmp_dir: temporary directory for output
        spacing: hatch line spacing in SVG user units
        angle_deg: hatch angle in degrees
        crosshatch: if True, add a second pass at angle+90
        tolerance: bezier flattening tolerance

    Returns:
        path to the modified SVG file (or original if no hatching needed)
    """
    import xml.etree.ElementTree as ET

    print("Applying hatch fill to closed paths...")

    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    ET.register_namespace('inkscape', 'http://www.inkscape.org/namespaces/inkscape')
    ET.register_namespace('sodipodi', 'http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd')

    tree = ET.parse(svg_path)
    root = tree.getroot()

    svg_ns = '{http://www.w3.org/2000/svg}'

    # Find all path elements with 'd' attributes
    all_polygons = []
    path_elements = list(root.iter(f'{svg_ns}path')) + list(root.iter('path'))

    for elem in path_elements:
        d = elem.get('d', '')
        if not d:
            continue
        # Parse the path into polygons
        polys = _parse_svg_path_to_polygons(d, tolerance)
        if not polys:
            continue
        # Get cumulative transform for this element (handles nested groups)
        matrix = _get_element_transform(elem, root)
        # Apply transform to convert to absolute SVG coordinates
        is_identity = (abs(matrix[0]-1) < 1e-9 and abs(matrix[1]) < 1e-9 and
                       abs(matrix[2]) < 1e-9 and abs(matrix[3]-1) < 1e-9 and
                       abs(matrix[4]) < 1e-9 and abs(matrix[5]) < 1e-9)
        if not is_identity:
            polys = [_apply_transform_to_polygon(p, matrix) for p in polys]
        all_polygons.extend(polys)

    if not all_polygons:
        print("  No closed paths found for hatch fill")
        return svg_path

    print(f"  Found {len(all_polygons)} closed subpath(s) to hatch")

    # Generate hatch lines
    segments = generate_hatch_lines(all_polygons, spacing, angle_deg)

    if crosshatch:
        segments2 = generate_hatch_lines(all_polygons, spacing, angle_deg + 90.0)
        segments.extend(segments2)
        print(f"  Generated {len(segments)} hatch segments (crosshatch)")
    else:
        print(f"  Generated {len(segments)} hatch segments")

    if not segments:
        print("  No hatch segments generated (shapes may be too small)")
        return svg_path

    # Create SVG path element for the hatch lines
    hatch_d = _segments_to_svg_path(segments)
    hatch_elem = ET.SubElement(root, f'{svg_ns}path')
    hatch_elem.set('d', hatch_d)
    hatch_elem.set('stroke', '#000000')
    hatch_elem.set('fill', 'none')
    hatch_elem.set('stroke-width', '0.5')
    hatch_elem.set('id', 'hatch-fill')

    # Write modified SVG
    output_svg = os.path.join(tmp_dir, "hatched.svg")
    tree.write(output_svg, xml_declaration=True, encoding='unicode')

    print(f"  Hatch fill SVG written")
    return output_svg


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

def postprocess_gcode(raw_gcode_path, output_path, args):
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
    # Size will be filled in after we measure actual extent
    size_line_idx = len(output_lines)
    output_lines.append("; Size: (computing...)")
    output_lines.append(f"; Draw speed: {args.draw_speed} mm/min")
    output_lines.append(f"; Travel speed: {args.travel_speed} mm/min")
    output_lines.append(f"; Z speed: {args.z_speed} mm/min")
    output_lines.append(f"; Pen down Z: {args.pen_down_z} mm")
    output_lines.append(f"; Pen up Z: {args.pen_up_z} mm")
    output_lines.append(f"; Target width: {args.width_mm} mm | Height scale: {args.height_scale}")
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

    # -- Second pass: measure coordinate extent from all segments --
    re_x = re.compile(r'X\s*(-?\d+\.?\d*)', re.IGNORECASE)
    re_y = re.compile(r'Y\s*(-?\d+\.?\d*)', re.IGNORECASE)

    x_min = float('inf')
    x_max = float('-inf')
    y_min = float('inf')
    y_max = float('-inf')

    for segment in path_segments:
        for gline in segment:
            mx = re_x.search(gline)
            my = re_y.search(gline)
            if mx:
                xv = float(mx.group(1))
                x_min = min(x_min, xv)
                x_max = max(x_max, xv)
            if my:
                yv = float(my.group(1))
                y_min = min(y_min, yv)
                y_max = max(y_max, yv)

    if x_max <= x_min or y_max <= y_min:
        print("  WARNING: Could not determine coordinate extent!")
        rescale_x = 1.0
        rescale_y = 1.0
        output_width_mm = 0.0
        output_height_mm = 0.0
    else:
        raw_width = x_max - x_min
        raw_height = y_max - y_min
        print(f"  Raw G-code extent: {raw_width:.2f} x {raw_height:.2f} (svg2gcode units)")

        # Compute scale to fit desired width, preserving aspect ratio
        rescale_x = args.width_mm / raw_width
        rescale_y = rescale_x * args.height_scale

        output_width_mm = args.width_mm
        output_height_mm = raw_height * rescale_y

        print(f"  Rescale factors: X={rescale_x:.4f}  Y={rescale_y:.4f}")
        print(f"  Output size: {output_width_mm:.1f} x {output_height_mm:.1f} mm")

    # Update the size line in the header
    output_lines[size_line_idx] = f"; Size: {output_width_mm:.1f} x {output_height_mm:.1f} mm"

    # -- Third pass: emit scaled G-code --
    # We shift coordinates so that minimum becomes (0 + origin_offset)
    x_offset = -x_min if x_min != float('inf') else 0
    y_offset = -y_min if y_min != float('inf') else 0

    for segment in path_segments:
        if not segment:
            continue

        for gline in segment:
            # Scale and offset the coordinates
            new_parts = []
            mx = re_x.search(gline)
            my = re_y.search(gline)
            xv = (float(mx.group(1)) + x_offset) * rescale_x if mx else None
            yv = (float(my.group(1)) + y_offset) * rescale_y if my else None

            # Rebuild the G-code line with scaled coordinates
            if gline.startswith("G0"):
                parts = ["G0"]
                if xv is not None:
                    parts.append(f"X{xv:.3f}")
                if yv is not None:
                    parts.append(f"Y{yv:.3f}")
                parts.append(f"F{args.travel_speed}")
                scaled_line = " ".join(parts)
            else:
                parts = ["G1"]
                if xv is not None:
                    parts.append(f"X{xv:.3f}")
                if yv is not None:
                    parts.append(f"Y{yv:.3f}")
                parts.append(f"F{args.draw_speed}")
                scaled_line = " ".join(parts)

            if gline.startswith("G0"):
                output_lines.append(scaled_line)
                move_count += 1
            else:
                if not pen_is_down:
                    output_lines.append(f"{pen_down_cmd}   ; Pen down")
                    pen_is_down = True
                output_lines.append(scaled_line)
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

    return move_count, path_count, output_width_mm, output_height_mm


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

    # -- Read SVG dimensions (used for hatch spacing calculation) --
    svg_width_mm, svg_height_mm, svg_vb_w, svg_vb_h = get_svg_dimensions(input_path)
    print(f"SVG physical size: {svg_width_mm:.2f} x {svg_height_mm:.2f} mm")
    print(f"SVG viewBox size: {svg_vb_w:.2f} x {svg_vb_h:.2f} (coordinate units)")
    print(f"Target output width: {args.width_mm} mm (height scale: {args.height_scale})")

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

    # Apply hatch fill if enabled
    if args.hatch_fill:
        # Convert hatch spacing from desired output mm to SVG viewBox units.
        # Since post-processing scales everything by (width_mm / raw_extent),
        # and raw_extent comes from svg2gcode processing viewBox coords,
        # the hatch spacing in viewBox units that produces the desired mm spacing is:
        #   hatch_svg = desired_mm * (viewBox_w / width_mm) * (raw_extent / viewBox_as_gcode)
        # Since we can't know raw_extent yet, and hatch goes through the SAME
        # svg2gcode + post-processing pipeline as outlines, we just need spacing
        # relative to the drawing. Use viewBox width as reference:
        #   spacing_fraction = desired_mm / width_mm (fraction of output width)
        #   hatch_svg = spacing_fraction * viewBox_w
        hatch_spacing_svg = args.hatch_spacing * svg_vb_w / args.width_mm
        print(f"Hatch spacing: {args.hatch_spacing} mm -> {hatch_spacing_svg:.2f} SVG units")
        processed_svg = apply_hatch_fill(
            processed_svg, tmp_dir,
            spacing=hatch_spacing_svg,
            angle_deg=args.hatch_angle,
            crosshatch=args.hatch_crosshatch,
            tolerance=0.5
        )

    # Build svg2gcode command (use preprocessed SVG)
    cmd = list(svg2gcode_cmd) + [
        processed_svg,
        tmp_gcode,
        "--pathcut",        # Treat all paths as cuts (ensures they are drawn)
        "--nofill",         # Ignore fills (plotter cannot fill areas)
    ]

    # NOTE: We do NOT pass --scale to svg2gcode. Instead we let it output at
    # native scale and apply our own scaling in post-processing. This avoids
    # any unit-conversion mismatches between our SVG parsing and svg2gcode's.

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
    move_count, path_count, output_width_mm, output_height_mm = postprocess_gcode(
        tmp_gcode, output_path, args)

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
    print(f"Output dimensions: {output_width_mm:.1f} x {output_height_mm:.1f} mm")
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
    parser.add_argument("--hatch-fill", type=str, default="false", dest="hatch_fill",
                        help="Enable hatch fill for closed paths (default: false)")
    parser.add_argument("--hatch-spacing", type=float, default=2.0, dest="hatch_spacing",
                        help="Hatch line spacing in mm (default: 2.0)")
    parser.add_argument("--hatch-angle", type=float, default=45.0, dest="hatch_angle",
                        help="Hatch angle in degrees (default: 45)")
    parser.add_argument("--hatch-crosshatch", type=str, default="false", dest="hatch_crosshatch",
                        help="Enable crosshatch (default: false)")

    args = parser.parse_args()

    # Parse bool flags (GUI passes them as strings "true"/"false")
    if isinstance(args.hatch_fill, str):
        args.hatch_fill = args.hatch_fill.lower() in ("true", "1", "yes")
    if isinstance(args.hatch_crosshatch, str):
        args.hatch_crosshatch = args.hatch_crosshatch.lower() in ("true", "1", "yes")

    convert(args)


if __name__ == "__main__":
    main()

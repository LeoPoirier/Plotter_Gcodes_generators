#!/usr/bin/env python3
"""
png_to_plotter_gcode.py — PNG/JPG to Plotter G-code converter (edge + hatch)

Converts a raster image (PNG or JPG) into pen-plotter G-code using two
complementary drawing layers:
  1. Edge layer — Canny edge detection traces outlines and structural lines
  2. Hatch layer — Parallel line hatching fills tonal regions with density
                   proportional to darkness

Usage:
    python3 png_to_plotter_gcode.py input.png output.gcode [options]
    python3 png_to_plotter_gcode.py --get-params
"""

import argparse
import json
import sys
import os
import math
import numpy as np
import cv2
from PIL import Image

# ═══════════════════════════════════════════════════════════════════
# CONVERTER METADATA
# ═══════════════════════════════════════════════════════════════════
CONVERTER_INFO = {
    "name": "Image to Plotter (edge + hatch)",
    "description": (
        "Converts PNG/JPG raster images to pen-plotter G-code using Canny "
        "edge detection for outlines and parallel-line hatching for tonal "
        "fill. Supports adjustable density, crosshatching, contrast/brightness "
        "preprocessing, and nearest-neighbor travel optimization."
    ),
    "author": "Plotter",
    "version": "1.0",
    "input_types": [["Image files", "*.png *.jpg *.jpeg"], ["All files", "*.*"]],
    "parameters": [
        {
            "name": "width_mm",
            "flag": "--width-mm",
            "type": "float",
            "default": 100,
            "min": 1.0,
            "max": 1000.0,
            "step": 1.0,
            "unit": "mm",
            "label": "Output Width",
            "description": "Output drawing width in mm. Height is computed from aspect ratio and height_scale."
        },
        {
            "name": "height_scale",
            "flag": "--height-scale",
            "type": "float",
            "default": 1.0,
            "min": 0.1,
            "max": 10.0,
            "step": 0.1,
            "unit": "\u00d7",
            "label": "Height Scale",
            "description": "Scale factor for height relative to original aspect ratio. 1.0 keeps original proportions."
        },
        {
            "name": "blur_radius",
            "flag": "--blur-radius",
            "type": "int",
            "default": 2,
            "min": 0,
            "max": 20,
            "step": 1,
            "unit": "px",
            "label": "Blur Radius",
            "description": "Gaussian blur radius before edge detection. 0 = disabled."
        },
        {
            "name": "contrast",
            "flag": "--contrast",
            "type": "float",
            "default": 1.0,
            "min": 0.1,
            "max": 5.0,
            "step": 0.1,
            "unit": "\u00d7",
            "label": "Contrast",
            "description": "Contrast multiplier applied to the grayscale image before processing."
        },
        {
            "name": "brightness",
            "flag": "--brightness",
            "type": "float",
            "default": 0.0,
            "min": -128.0,
            "max": 128.0,
            "step": 1.0,
            "unit": "levels",
            "label": "Brightness",
            "description": "Brightness offset (-128 to +128) applied before processing."
        },
        {
            "name": "invert",
            "flag": "--invert",
            "type": "bool",
            "default": False,
            "label": "Invert Image",
            "description": "Invert the image before processing (useful for white-on-black drawings)."
        },
        {
            "name": "edges",
            "flag": "--edges",
            "type": "bool",
            "default": True,
            "label": "Edge Layer",
            "description": "Enable/disable the edge detection layer."
        },
        {
            "name": "canny_low",
            "flag": "--canny-low",
            "type": "int",
            "default": 50,
            "min": 0,
            "max": 255,
            "step": 1,
            "unit": "",
            "label": "Canny Low Threshold",
            "description": "Canny lower hysteresis threshold. Controls which weak edges are discarded."
        },
        {
            "name": "canny_high",
            "flag": "--canny-high",
            "type": "int",
            "default": 150,
            "min": 0,
            "max": 255,
            "step": 1,
            "unit": "",
            "label": "Canny High Threshold",
            "description": "Canny upper hysteresis threshold. Rule of thumb: high ~ 3x low."
        },
        {
            "name": "simplify_tolerance",
            "flag": "--simplify-tolerance",
            "type": "float",
            "default": 0.3,
            "min": 0.0,
            "max": 5.0,
            "step": 0.1,
            "unit": "mm",
            "label": "Simplify Tolerance",
            "description": "Ramer-Douglas-Peucker simplification tolerance. Higher = fewer points."
        },
        {
            "name": "min_contour_length",
            "flag": "--min-contour-length",
            "type": "float",
            "default": 1.0,
            "min": 0.0,
            "max": 50.0,
            "step": 0.5,
            "unit": "mm",
            "label": "Min Contour Length",
            "description": "Discard contours shorter than this. Removes noise specks."
        },
        {
            "name": "hatch",
            "flag": "--hatch",
            "type": "bool",
            "default": True,
            "label": "Hatch Layer",
            "description": "Enable/disable the hatch infill layer."
        },
        {
            "name": "hatch_angle",
            "flag": "--hatch-angle",
            "type": "float",
            "default": 45.0,
            "min": 0.0,
            "max": 179.0,
            "step": 1.0,
            "unit": "degrees",
            "label": "Hatch Angle",
            "description": "Angle of hatch lines. 0=horizontal, 45=diagonal, 90=vertical."
        },
        {
            "name": "hatch_spacing_min",
            "flag": "--hatch-spacing-min",
            "type": "float",
            "default": 0.4,
            "min": 0.1,
            "max": 10.0,
            "step": 0.1,
            "unit": "mm",
            "label": "Hatch Spacing Min",
            "description": "Spacing in the darkest areas (maximum density). Should be >= pen tip diameter."
        },
        {
            "name": "hatch_spacing_max",
            "flag": "--hatch-spacing-max",
            "type": "float",
            "default": 4.0,
            "min": 0.1,
            "max": 20.0,
            "step": 0.1,
            "unit": "mm",
            "label": "Hatch Spacing Max",
            "description": "Spacing in near-white areas (minimum density)."
        },
        {
            "name": "hatch_threshold",
            "flag": "--hatch-threshold",
            "type": "int",
            "default": 240,
            "min": 0,
            "max": 255,
            "step": 1,
            "unit": "",
            "label": "Hatch Threshold",
            "description": "Brightness above which no hatch is drawn (pure white cutoff)."
        },
        {
            "name": "crosshatch",
            "flag": "--crosshatch",
            "type": "bool",
            "default": False,
            "label": "Crosshatch",
            "description": "Add a second hatch pass at hatch_angle + 90 in the darkest zone."
        },
        {
            "name": "crosshatch_threshold",
            "flag": "--crosshatch-threshold",
            "type": "int",
            "default": 80,
            "min": 0,
            "max": 255,
            "step": 1,
            "unit": "",
            "label": "Crosshatch Threshold",
            "description": "Brightness below which cross-hatching is applied."
        },
        {
            "name": "draw_speed",
            "flag": "--draw-speed",
            "type": "int",
            "default": 2000,
            "min": 100,
            "max": 10000,
            "step": 100,
            "unit": "mm/min",
            "label": "Drawing Speed",
            "description": "Feed rate when pen is down and drawing."
        },
        {
            "name": "travel_speed",
            "flag": "--travel-speed",
            "type": "int",
            "default": 3000,
            "min": 100,
            "max": 15000,
            "step": 100,
            "unit": "mm/min",
            "label": "Travel Speed",
            "description": "Feed rate when pen is up and travelling."
        },
        {
            "name": "z_speed",
            "flag": "--z-speed",
            "type": "int",
            "default": 200,
            "min": 100,
            "max": 10000,
            "step": 100,
            "unit": "mm/min",
            "label": "Z Speed",
            "description": "Feed rate for Z-axis pen up/down movements."
        },
        {
            "name": "pen_down_z",
            "flag": "--pen-down-z",
            "type": "float",
            "default": 0.0,
            "min": -50.0,
            "max": 50.0,
            "step": 0.5,
            "unit": "mm",
            "label": "Pen Down Z",
            "description": "Z position when pen is down (drawing)."
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
            "description": "Z position when pen is up (travelling)."
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
            "description": "Translate the drawing origin horizontally."
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
            "description": "Translate the drawing origin vertically."
        }
    ]
}


# ═══════════════════════════════════════════════════════════════════
# IMAGE PREPROCESSING
# ═══════════════════════════════════════════════════════════════════

def load_and_preprocess(input_path, args):
    """
    Load image, composite RGBA on white, convert to grayscale,
    apply contrast/brightness/invert, resize to target mm dimensions.
    Returns: grayscale numpy array, width_mm, height_mm, scale (px per mm)
    """
    print(f"Loading image: {input_path}")
    img = Image.open(input_path)

    # Handle RGBA: composite on white background
    if img.mode == 'RGBA':
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
        print("  Composited RGBA on white background")

    # Convert to grayscale
    img = img.convert('L')
    orig_w, orig_h = img.size
    print(f"  Image size: {orig_w} x {orig_h} px")

    # Compute physical dimensions
    width_mm = args.width_mm
    aspect_ratio = orig_h / orig_w
    height_mm = width_mm * aspect_ratio * args.height_scale
    print(f"  Output size: {width_mm:.1f} x {height_mm:.1f} mm")

    # Convert to numpy array
    gray = np.array(img, dtype=np.float32)

    # Apply contrast and brightness
    if args.contrast != 1.0 or args.brightness != 0.0:
        gray = gray * args.contrast + args.brightness
        gray = np.clip(gray, 0, 255)
        print(f"  Applied contrast={args.contrast}, brightness={args.brightness}")

    # Invert if requested
    if args.invert:
        gray = 255.0 - gray
        print("  Image inverted")

    gray = gray.astype(np.uint8)

    # Compute scale factor (pixels per mm)
    px_per_mm = orig_w / width_mm

    return gray, width_mm, height_mm, px_per_mm


# ═══════════════════════════════════════════════════════════════════
# EDGE LAYER
# ═══════════════════════════════════════════════════════════════════

def generate_edge_paths(gray, args, width_mm, height_mm, px_per_mm):
    """
    Run Canny edge detection, extract and simplify contours,
    convert to mm coordinates, filter short contours.
    Returns list of path segments: each segment is [(x_mm, y_mm), ...]
    """
    print("Generating edge layer...")

    # Apply Gaussian blur
    if args.blur_radius > 0:
        # OpenCV GaussianBlur requires odd kernel size
        ksize = args.blur_radius * 2 + 1
        blurred = cv2.GaussianBlur(gray, (ksize, ksize), 0)
    else:
        blurred = gray

    # Canny edge detection
    edges = cv2.Canny(blurred, args.canny_low, args.canny_high)

    # Find contours
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    print(f"  Found {len(contours)} raw contours")

    # Simplify tolerance in pixels
    simplify_tol_px = args.simplify_tolerance * px_per_mm

    # Process contours
    paths = []
    discarded_short = 0
    discarded_points = 0

    for contour in contours:
        # Simplify with Ramer-Douglas-Peucker
        simplified = cv2.approxPolyDP(contour, simplify_tol_px, closed=False)

        if len(simplified) < 2:
            discarded_points += 1
            continue

        # Convert to mm coordinates with Y-flip
        points_mm = []
        for pt in simplified:
            x_px, y_px = pt[0]
            x_mm = x_px / px_per_mm
            y_mm = height_mm - (y_px / px_per_mm)  # Flip Y
            points_mm.append((x_mm, y_mm))

        # Compute contour length in mm
        length = 0.0
        for i in range(1, len(points_mm)):
            dx = points_mm[i][0] - points_mm[i-1][0]
            dy = points_mm[i][1] - points_mm[i-1][1]
            length += math.hypot(dx, dy)

        if length < args.min_contour_length:
            discarded_short += 1
            continue

        paths.append(points_mm)

    print(f"  Kept {len(paths)} contours (discarded {discarded_short} short, {discarded_points} degenerate)")
    return paths


# ═══════════════════════════════════════════════════════════════════
# HATCH LAYER
# ═══════════════════════════════════════════════════════════════════

def generate_hatch_paths(gray, args, width_mm, height_mm, px_per_mm):
    """
    Generate parallel hatch lines with density proportional to image darkness.
    Cast scan lines at hatch_angle, skip lines in bright areas.
    Returns list of path segments: each segment is [(x_mm, y_mm), ...]
    """
    print("Generating hatch layer...")

    img_h, img_w = gray.shape
    paths = []

    def _cast_hatch_at_angle(angle_deg, brightness_mask=None, threshold=None):
        """
        Cast parallel scan lines at the given angle.
        brightness_mask: if provided, only hatch where mask pixel < threshold.
        If None, use the full grayscale image with spacing logic.
        """
        nonlocal paths

        angle_rad = math.radians(angle_deg)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)

        # Determine the bounding box diagonal to ensure full coverage
        diag = math.hypot(width_mm, height_mm)

        # Scan lines are cast perpendicular to the hatch angle
        # We sweep from -diag/2 to +diag/2 in the perpendicular direction
        # centered on the image center
        cx_mm = width_mm / 2.0
        cy_mm = height_mm / 2.0

        # Step size for scan lines (in the perpendicular direction)
        step = args.hatch_spacing_min
        num_lines = int(diag / step) + 1

        for i in range(num_lines):
            # Offset from center in the perpendicular direction
            offset = -diag / 2.0 + i * step

            # Scan line start and end points (long enough to cross the image)
            # The scan line direction is along the angle
            # The perpendicular offset is at angle + 90
            perp_x = -sin_a * offset
            perp_y = cos_a * offset

            # Line endpoints (extend well beyond image)
            x0_mm = cx_mm + perp_x - cos_a * diag
            y0_mm = cy_mm + perp_y - sin_a * diag
            x1_mm = cx_mm + perp_x + cos_a * diag
            y1_mm = cy_mm + perp_y + sin_a * diag

            # Sample points along this line within the image bounds
            line_length_mm = 2.0 * diag
            # Sample at approximately 1 pixel resolution
            num_samples = max(int(line_length_mm * px_per_mm), 2)
            sample_step = line_length_mm / num_samples

            # Collect dark segments along this scan line
            in_segment = False
            segment_start = None
            segments = []

            for s in range(num_samples + 1):
                t = s / num_samples
                x_mm = x0_mm + (x1_mm - x0_mm) * t
                y_mm = y0_mm + (y1_mm - y0_mm) * t

                # Check if point is within image bounds
                if x_mm < 0 or x_mm >= width_mm or y_mm < 0 or y_mm >= height_mm:
                    if in_segment:
                        segments.append((segment_start, (prev_x, prev_y)))
                        in_segment = False
                    continue

                # Convert mm to pixel (with Y-flip: y_px = (height_mm - y_mm) * px_per_mm)
                x_px = int(x_mm * px_per_mm)
                y_px = int((height_mm - y_mm) * px_per_mm)

                # Clamp to image bounds
                x_px = min(max(x_px, 0), img_w - 1)
                y_px = min(max(y_px, 0), img_h - 1)

                brightness = gray[y_px, x_px]

                # Determine if we should draw here
                if brightness_mask is not None:
                    # Crosshatch mode: draw only where brightness < threshold
                    should_draw = brightness < threshold
                else:
                    # Normal hatch mode: draw where brightness < hatch_threshold
                    # and use spacing logic to skip lines in lighter areas
                    should_draw = brightness < args.hatch_threshold

                    # Spacing skip logic:
                    # For this scan line at offset, compute the effective spacing
                    # needed for this brightness level
                    if should_draw:
                        needed_spacing = (args.hatch_spacing_min +
                                          (args.hatch_spacing_max - args.hatch_spacing_min) *
                                          (brightness / args.hatch_threshold))
                        # How many base steps does this spacing represent?
                        line_index_in_period = i % max(1, int(round(needed_spacing / step)))
                        if line_index_in_period != 0:
                            should_draw = False

                if should_draw:
                    if not in_segment:
                        segment_start = (x_mm, y_mm)
                        in_segment = True
                    prev_x, prev_y = x_mm, y_mm
                else:
                    if in_segment:
                        segments.append((segment_start, (prev_x, prev_y)))
                        in_segment = False

            if in_segment:
                segments.append((segment_start, (prev_x, prev_y)))

            # Add valid segments as paths (minimum length check)
            for seg_start, seg_end in segments:
                seg_len = math.hypot(seg_end[0] - seg_start[0],
                                     seg_end[1] - seg_start[1])
                if seg_len > 0.2:  # minimum segment length in mm
                    paths.append([seg_start, seg_end])

    # Primary hatch pass
    _cast_hatch_at_angle(args.hatch_angle)
    primary_count = len(paths)
    print(f"  Primary hatch: {primary_count} segments at {args.hatch_angle} deg")

    # Crosshatch pass (perpendicular, only in darkest zones)
    if args.crosshatch:
        cross_angle = (args.hatch_angle + 90.0) % 180.0
        _cast_hatch_at_angle(cross_angle, brightness_mask=gray,
                             threshold=args.crosshatch_threshold)
        cross_count = len(paths) - primary_count
        print(f"  Crosshatch: {cross_count} segments at {cross_angle} deg")

    print(f"  Total hatch segments: {len(paths)}")
    return paths


# ═══════════════════════════════════════════════════════════════════
# TRAVEL OPTIMIZATION: Nearest-neighbor path ordering (greedy)
# ═══════════════════════════════════════════════════════════════════

def optimize_travel_order(paths):
    """
    Reorder path segments using greedy nearest-neighbor heuristic.
    Each path is a list of (x, y) tuples.
    Picks the closest unvisited segment start to the current position.
    No segment reversal.
    Returns reordered list of paths.
    """
    if len(paths) <= 1:
        return paths

    n = len(paths)
    starts = [p[0] for p in paths]
    ends = [p[-1] for p in paths]

    visited = [False] * n
    order = []
    current_pos = (0.0, 0.0)

    for _ in range(n):
        best_idx = -1
        best_dist = float('inf')
        for i in range(n):
            if visited[i]:
                continue
            d = math.hypot(current_pos[0] - starts[i][0],
                           current_pos[1] - starts[i][1])
            if d < best_dist:
                best_dist = d
                best_idx = i
        visited[best_idx] = True
        order.append(best_idx)
        current_pos = ends[best_idx]

    # Calculate improvement
    original_travel = sum(
        math.hypot(ends[i][0] - starts[i+1][0], ends[i][1] - starts[i+1][1])
        for i in range(n - 1)
    )
    optimized_travel = sum(
        math.hypot(ends[order[i]][0] - starts[order[i+1]][0],
                   ends[order[i]][1] - starts[order[i+1]][1])
        for i in range(n - 1)
    )

    if original_travel > 0:
        saved_pct = (1 - optimized_travel / original_travel) * 100
        print(f"  Travel optimization: {saved_pct:.1f}% reduction")
        print(f"    Original: {original_travel:.1f} mm | Optimized: {optimized_travel:.1f} mm")
    else:
        print("  Travel optimization applied (minimal travel)")

    return [paths[i] for i in order]


# ═══════════════════════════════════════════════════════════════════
# G-CODE GENERATION
# ═══════════════════════════════════════════════════════════════════

def generate_gcode(paths, args, input_path, width_mm, height_mm, img_size):
    """
    Convert optimized path segments to G-code string.
    Each path segment is a list of (x_mm, y_mm) points.
    """
    pen_up_cmd = f"G1 Z{args.pen_up_z} F{args.z_speed}"
    pen_down_cmd = f"G1 Z{args.pen_down_z} F{args.z_speed}"

    lines = []

    # Header
    lines.append("; ==========================================")
    lines.append("; Image to Plotter G-code")
    lines.append(f"; Input: {os.path.basename(input_path)}  ({img_size[0]}x{img_size[1]} px -> {width_mm:.1f}x{height_mm:.1f} mm)")
    lines.append(f"; Draw speed: {args.draw_speed} mm/min | Travel speed: {args.travel_speed} mm/min")
    lines.append(f"; Edges: {'ON' if args.edges else 'OFF'} | Hatch: {'ON' if args.hatch else 'OFF'}")
    if args.hatch:
        lines.append(f"; Hatch angle: {args.hatch_angle} deg | Spacing: {args.hatch_spacing_min}-{args.hatch_spacing_max} mm")
    if args.crosshatch:
        lines.append(f"; Crosshatch: ON (threshold < {args.crosshatch_threshold})")
    lines.append("; ==========================================")
    lines.append("")
    lines.append("G21          ; Millimetres")
    lines.append("G90          ; Absolute positioning")
    lines.append("G28          ; Home")
    lines.append("")
    lines.append(f"{pen_up_cmd}   ; Pen up (start)")
    lines.append("")

    move_count = 0
    path_count = 0

    for segment in paths:
        if len(segment) < 2:
            continue

        path_count += 1

        # Travel to start of segment (pen up)
        sx = segment[0][0] + args.origin_x
        sy = segment[0][1] + args.origin_y
        lines.append(f"G0 X{sx:.3f} Y{sy:.3f} F{args.travel_speed}")
        move_count += 1

        # Pen down
        lines.append(f"{pen_down_cmd}   ; Pen down")

        # Draw segment
        for point in segment[1:]:
            px = point[0] + args.origin_x
            py = point[1] + args.origin_y
            lines.append(f"G1 X{px:.3f} Y{py:.3f} F{args.draw_speed}")
            move_count += 1

        # Pen up
        lines.append(f"{pen_up_cmd}   ; Pen up")

    # Footer
    lines.append("")
    lines.append(f"G0 X0 Y0 F{args.travel_speed}   ; Return to origin")
    lines.append("M18              ; Disable motors")

    return "\n".join(lines) + "\n", move_count, path_count


# ═══════════════════════════════════════════════════════════════════
# MAIN CONVERSION
# ═══════════════════════════════════════════════════════════════════

def convert(args):
    """Main conversion pipeline."""

    input_path = args.input
    output_path = args.output

    # Validate input
    if not os.path.isfile(input_path):
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Load and preprocess image
    gray, width_mm, height_mm, px_per_mm = load_and_preprocess(input_path, args)
    img_size = (gray.shape[1], gray.shape[0])  # (width, height) in pixels

    all_paths = []

    # Edge layer
    if args.edges:
        edge_paths = generate_edge_paths(gray, args, width_mm, height_mm, px_per_mm)
        all_paths.extend(edge_paths)
        print(f"  Edge layer contributed {len(edge_paths)} path segments")
    else:
        print("Edge layer: DISABLED")

    # Hatch layer
    if args.hatch:
        hatch_paths = generate_hatch_paths(gray, args, width_mm, height_mm, px_per_mm)
        all_paths.extend(hatch_paths)
        print(f"  Hatch layer contributed {len(hatch_paths)} path segments")
    else:
        print("Hatch layer: DISABLED")

    if not all_paths:
        print("WARNING: No paths generated! Output will be empty.", file=sys.stderr)

    # Travel optimization
    print(f"\nOptimizing travel order for {len(all_paths)} segments...")
    all_paths = optimize_travel_order(all_paths)

    # Generate G-code
    print("\nGenerating G-code...")
    gcode, move_count, path_count = generate_gcode(
        all_paths, args, input_path, width_mm, height_mm, img_size
    )

    # Write output
    with open(output_path, 'w') as f:
        f.write(gcode)

    final_size = os.path.getsize(output_path)
    print(f"\nConversion complete!")
    print(f"  Paths: {path_count}")
    print(f"  Total moves: {move_count}")
    print(f"  Output size: {final_size} bytes")
    print(f"  G-code written to: {output_path}")


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
  %(prog)s photo.jpg output.gcode --width-mm 150
  %(prog)s logo.png output.gcode --width-mm 100 --hatch False
  %(prog)s portrait.jpg output.gcode --width-mm 200 --edges False --hatch-angle 45
  %(prog)s photo.jpg output.gcode --width-mm 150 --crosshatch
        """
    )
    parser.add_argument("input", help="Input image file (PNG/JPG)")
    parser.add_argument("output", help="Output G-code file", nargs="?",
                        default="output.gcode")

    # Physical size
    parser.add_argument("--width-mm", type=float, default=100.0, dest="width_mm",
                        help="Output width in mm (default: 100)")
    parser.add_argument("--height-scale", type=float, default=1.0, dest="height_scale",
                        help="Height scale factor relative to aspect ratio (default: 1.0)")

    # Preprocessing
    parser.add_argument("--blur-radius", type=int, default=2, dest="blur_radius",
                        help="Gaussian blur radius in px (default: 2, 0=disabled)")
    parser.add_argument("--contrast", type=float, default=1.0,
                        help="Contrast multiplier (default: 1.0)")
    parser.add_argument("--brightness", type=float, default=0.0,
                        help="Brightness offset -128 to +128 (default: 0)")
    parser.add_argument("--invert", type=bool, default=False,
                        help="Invert image (default: False)")

    # Edge layer
    parser.add_argument("--edges", type=lambda x: x.lower() in ('true', '1', 'yes'),
                        default=True,
                        help="Enable edge layer (default: True)")
    parser.add_argument("--canny-low", type=int, default=50, dest="canny_low",
                        help="Canny low threshold (default: 50)")
    parser.add_argument("--canny-high", type=int, default=150, dest="canny_high",
                        help="Canny high threshold (default: 150)")
    parser.add_argument("--simplify-tolerance", type=float, default=0.3,
                        dest="simplify_tolerance",
                        help="Contour simplification tolerance in mm (default: 0.3)")
    parser.add_argument("--min-contour-length", type=float, default=1.0,
                        dest="min_contour_length",
                        help="Minimum contour length in mm (default: 1.0)")

    # Hatch layer
    parser.add_argument("--hatch", type=lambda x: x.lower() in ('true', '1', 'yes'),
                        default=True,
                        help="Enable hatch layer (default: True)")
    parser.add_argument("--hatch-angle", type=float, default=45.0, dest="hatch_angle",
                        help="Hatch angle in degrees (default: 45)")
    parser.add_argument("--hatch-spacing-min", type=float, default=0.4,
                        dest="hatch_spacing_min",
                        help="Min hatch spacing in mm (darkest areas, default: 0.4)")
    parser.add_argument("--hatch-spacing-max", type=float, default=4.0,
                        dest="hatch_spacing_max",
                        help="Max hatch spacing in mm (lightest areas, default: 4.0)")
    parser.add_argument("--hatch-threshold", type=int, default=240, dest="hatch_threshold",
                        help="Brightness cutoff for hatching (default: 240)")
    parser.add_argument("--crosshatch", type=lambda x: x.lower() in ('true', '1', 'yes'),
                        default=False,
                        help="Enable crosshatch in dark zones (default: False)")
    parser.add_argument("--crosshatch-threshold", type=int, default=80,
                        dest="crosshatch_threshold",
                        help="Brightness threshold for crosshatch (default: 80)")

    # Pen / motion
    parser.add_argument("--draw-speed", type=int, default=2000, dest="draw_speed",
                        help="Drawing speed in mm/min (default: 2000)")
    parser.add_argument("--travel-speed", type=int, default=3000, dest="travel_speed",
                        help="Travel speed in mm/min (default: 3000)")
    parser.add_argument("--z-speed", type=int, default=200, dest="z_speed",
                        help="Z-axis speed in mm/min (default: 200)")
    parser.add_argument("--pen-down-z", type=float, default=0.0, dest="pen_down_z",
                        help="Z position pen down in mm (default: 0)")
    parser.add_argument("--pen-up-z", type=float, default=3.0, dest="pen_up_z",
                        help="Z position pen up in mm (default: 3.0)")
    parser.add_argument("--origin-x", type=float, default=0.0, dest="origin_x",
                        help="X origin offset in mm (default: 0)")
    parser.add_argument("--origin-y", type=float, default=0.0, dest="origin_y",
                        help="Y origin offset in mm (default: 0)")

    args = parser.parse_args()
    convert(args)


if __name__ == "__main__":
    main()

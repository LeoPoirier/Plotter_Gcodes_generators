#!/usr/bin/env python3
"""
image_to_gcode_continous_s.py — Convert an image to a single continuous
oscillating line G-code for a CoreXY pen plotter (no pen lift needed).

Supports --get-params flag to report available parameters to the GUI.

Usage:
    python image_to_gcode_continous_s.py input.png output.gcode [options]
    python image_to_gcode_continous_s.py --get-params
"""

import argparse
import json
import sys
import numpy as np
from PIL import Image, ImageFilter


# ══════════════════════════════════════════════════════════════════════════════
# CONVERTER METADATA — The GUI reads this via --get-params
# ══════════════════════════════════════════════════════════════════════════════

CONVERTER_INFO = {
    "name": "Continuous S-Pattern (Oscillation)",
    "description": (
        "Moves the pen in horizontal S-pattern lines across the image. "
        "The line oscillates vertically — amplitude is proportional to "
        "image darkness. Dark = large waves, Bright = flat line. "
        "Single continuous stroke, no pen lift needed."
    ),
    "author": "Plotter project",
    "version": "1.0",
    "illustration": "image_to_gcode_continous_s.png",
    "parameters": [
        {
            "name": "width",
            "flag": "--width",
            "type": "float",
            "default": 100.0,
            "min": 10.0,
            "max": 1000.0,
            "step": 1.0,
            "unit": "mm",
            "label": "Plot width",
            "description": "Total drawing width in millimetres"
        },
        {
            "name": "height",
            "flag": "--height",
            "type": "float",
            "default": 100.0,
            "min": 10.0,
            "max": 1000.0,
            "step": 1.0,
            "unit": "mm",
            "label": "Plot height",
            "description": "Total drawing height in millimetres"
        },
        {
            "name": "lines",
            "flag": "--lines",
            "type": "int",
            "default": 60,
            "min": 5,
            "max": 500,
            "step": 1,
            "unit": "",
            "label": "Number of lines",
            "description": "Horizontal passes across the image. More = finer detail but slower"
        },
        {
            "name": "frequency",
            "flag": "--frequency",
            "type": "float",
            "default": 1.0,
            "min": 0.1,
            "max": 10.0,
            "step": 0.1,
            "unit": "waves/mm",
            "label": "Oscillation frequency",
            "description": "Number of wave cycles per mm of travel"
        },
        {
            "name": "max_amp",
            "flag": "--max-amp",
            "type": "float",
            "default": 0.8,
            "min": 0.05,
            "max": 5.0,
            "step": 0.05,
            "unit": "mm",
            "label": "Max amplitude",
            "description": "Maximum wave amplitude (keep below line_spacing/2 to avoid overlap)"
        },
        {
            "name": "feedrate",
            "flag": "--feedrate",
            "type": "int",
            "default": 3000,
            "min": 100,
            "max": 30000,
            "step": 100,
            "unit": "mm/min",
            "label": "Feedrate",
            "description": "Drawing speed in mm/min"
        },
        {
            "name": "resolution",
            "flag": "--resolution",
            "type": "float",
            "default": 2.0,
            "min": 0.5,
            "max": 10.0,
            "step": 0.1,
            "unit": "pts/mm",
            "label": "Resolution",
            "description": "Sample points per mm of travel. Higher = smoother but larger file"
        }
    ]
}


# ══════════════════════════════════════════════════════════════════════════════
# Image processing & G-code generation
# ══════════════════════════════════════════════════════════════════════════════

def load_and_prepare_image(image_path, num_lines, points_per_line):
    img = Image.open(image_path).convert('L')
    img = img.filter(ImageFilter.GaussianBlur(radius=1))
    img_resized = img.resize((points_per_line, num_lines), Image.LANCZOS)
    pixels = np.array(img_resized, dtype=np.float64) / 255.0
    darkness = 1.0 - pixels
    return darkness


def generate_gcode(darkness, width, height, frequency, max_amplitude,
                   feedrate, resolution):
    num_lines = darkness.shape[0]
    line_spacing = height / num_lines
    points_per_line = darkness.shape[1]

    gcode_lines = []
    gcode_lines.append("; ==========================================")
    gcode_lines.append("; Single-line oscillation plot")
    gcode_lines.append(f"; Size: {width} x {height} mm")
    gcode_lines.append(f"; Lines: {num_lines}")
    gcode_lines.append(f"; Frequency: {frequency} waves/mm")
    gcode_lines.append(f"; Max amplitude: {max_amplitude} mm")
    gcode_lines.append("; NO PEN LIFT - single continuous stroke")
    gcode_lines.append("; ==========================================")
    gcode_lines.append("")
    gcode_lines.append("G21          ; Millimetres")
    gcode_lines.append("G90          ; Absolute positioning")
    gcode_lines.append("G28          ; Home")
    gcode_lines.append("")

    start_y = height - (line_spacing / 2)
    gcode_lines.append(f"G1 X0.000 Y{start_y:.3f} F{feedrate}")
    gcode_lines.append("")
    gcode_lines.append("; --- Begin drawing ---")

    total_points = 0

    for line_idx in range(num_lines):
        y_center = height - (line_idx + 0.5) * line_spacing
        going_right = (line_idx % 2 == 0)
        num_points = int(width * resolution)

        for pt in range(num_points):
            t = pt / (num_points - 1)

            if going_right:
                x = t * width
                pixel_idx = int(t * (points_per_line - 1))
            else:
                x = (1.0 - t) * width
                pixel_idx = int((1.0 - t) * (points_per_line - 1))

            pixel_idx = max(0, min(pixel_idx, points_per_line - 1))
            dark = darkness[line_idx, pixel_idx]

            distance_along = t * width
            phase = 2.0 * np.pi * frequency * distance_along
            amplitude = dark * max_amplitude
            y = y_center + amplitude * np.sin(phase)

            gcode_lines.append(f"G1 X{x:.3f} Y{y:.3f} F{feedrate}")
            total_points += 1

        if line_idx < num_lines - 1:
            next_y_center = height - (line_idx + 1.5) * line_spacing
            gcode_lines.append(f"G1 X{x:.3f} Y{next_y_center:.3f} F{feedrate}")
            total_points += 1

    gcode_lines.append("")
    gcode_lines.append("; --- End of drawing ---")
    gcode_lines.append(f"G1 X0 Y0 F{feedrate + 2000}   ; Return home")
    gcode_lines.append("M18                             ; Disable motors")
    gcode_lines.append(f"; Total points: {total_points}")

    return "\n".join(gcode_lines)


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # Handle --get-params before argparse takes over
    if "--get-params" in sys.argv:
        print(json.dumps(CONVERTER_INFO, indent=2))
        sys.exit(0)

    parser = argparse.ArgumentParser(
        description="Convert image to single-line oscillation G-code for pen plotter"
    )
    parser.add_argument("input", help="Input image file (PNG, JPEG, etc.)")
    parser.add_argument("output", help="Output G-code file", nargs="?",
                        default="output.gcode")
    parser.add_argument("--width", type=float, default=100.0)
    parser.add_argument("--height", type=float, default=100.0)
    parser.add_argument("--lines", type=int, default=60)
    parser.add_argument("--frequency", type=float, default=1.0)
    parser.add_argument("--max-amp", type=float, default=0.8)
    parser.add_argument("--feedrate", type=int, default=3000)
    parser.add_argument("--resolution", type=float, default=2.0)
    parser.add_argument("--get-params", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    print(f"Loading image: {args.input}")
    points_per_line = int(args.width * args.resolution)
    darkness = load_and_prepare_image(args.input, args.lines, points_per_line)

    print(f"Image processed: {darkness.shape[1]}x{darkness.shape[0]} samples")
    print(f"Plot size: {args.width} x {args.height} mm")
    print(f"Lines: {args.lines} | Spacing: {args.height / args.lines:.2f} mm")
    print(f"Frequency: {args.frequency} waves/mm | Max amplitude: {args.max_amp} mm")
    print(f"Resolution: {args.resolution} pts/mm ({points_per_line} points/line)")

    gcode = generate_gcode(
        darkness,
        width=args.width,
        height=args.height,
        frequency=args.frequency,
        max_amplitude=args.max_amp,
        feedrate=args.feedrate,
        resolution=args.resolution
    )

    with open(args.output, 'w') as f:
        f.write(gcode)

    print(f"\nG-code written to: {args.output}")
    print(f"File size: {len(gcode) / 1024:.1f} KB")
    print(f"Estimated line count: {gcode.count(chr(10))}")
    print("\nReady to plot! No pen lift required.")


if __name__ == "__main__":
    main()

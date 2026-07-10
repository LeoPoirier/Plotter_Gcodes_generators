# Plotter G-code Converter — Developer Guide

## Overview

This document explains how to create new G-code converter scripts that plug into the Plotter GUI. The system is designed so that adding a new converter requires **zero changes to the GUI** — just drop a Python file into the `Gcode_converters/` folder and it appears automatically.

---

## Folder Structure

```
Plotter/
├── plotter_gui.py                  ← Main GUI application
├── run_plotter.bat                 ← Windows double-click launcher
└── Gcode_converters/               ← All converters live here
    ├── image_to_gcode_continous_s.py
    ├── your_new_converter.py
    └── another_converter.py
```

---

## How Discovery Works

When the GUI starts, it:

1. Scans `Gcode_converters/` for all `.py` files
2. Calls each one with: `python your_converter.py --get-params`
3. Expects a JSON response on stdout describing the converter
4. Builds the UI dynamically from that JSON

If a script doesn't respond to `--get-params` (times out or returns non-zero), it is silently skipped.

---

## The `--get-params` Protocol

Your converter must handle a special flag: `--get-params`

When called with this flag, the script must:
- Print a JSON object to stdout
- Exit with code 0
- Perform no other action (no file I/O, no processing)

### JSON Schema

```json
{
  "name": "Human-Readable Converter Name",
  "description": "A brief explanation of what this converter does and how the output looks.",
  "author": "Your Name (optional)",
  "version": "1.0 (optional)",
  "input_types": [["Image files", "*.png *.jpg *.jpeg *.bmp *.tiff"], ["All files", "*.*"]],
  "parameters": [
    {
      "name": "internal_name",
      "flag": "--cli-flag",
      "type": "float | int",
      "default": 100.0,
      "min": 10.0,
      "max": 1000.0,
      "step": 1.0,
      "unit": "mm",
      "label": "Display Label",
      "description": "Tooltip or help text for this parameter"
    }
  ]
}
```

### Field Reference

#### Top-Level Fields

| Field           | Required | Description                                              |
|-----------------|----------|----------------------------------------------------------|
| `name`          | Yes      | Displayed in the converter dropdown                      |
| `description`   | Yes      | Shown below the dropdown when selected                   |
| `author`        | No       | Credit (not displayed, for documentation)                |
| `version`       | No       | Version string (not displayed, for documentation)        |
| `illustration`  | No       | Filename of an illustration image in the same folder     |
| `input_types`   | No       | File filter list for the Browse dialog (see below)       |
| `parameters`    | Yes      | Array of parameter objects (can be empty `[]`)           |

#### Illustration Image

Each converter can have an illustration that shows the user what kind of output it produces. This is displayed in the GUI when the converter is selected.

**Two ways to provide an illustration:**

1. **By convention (auto-detected):** Place an image with the **same base name** as your script:
   ```
   Gcode_converters/
     my_converter.py
     my_converter.png   ← auto-detected
   ```

2. **Explicitly in JSON:** Add an `"illustration"` field pointing to the filename:
   ```json
   "illustration": "my_custom_preview.png"
   ```

**Image guidelines:**
- Recommended size: around 280 x 120 pixels (will be scaled to fit)
- Format: PNG or JPEG
- Content: A visual example of what the converter's output looks like
- Think of it as halfway between an icon and a full image — illustrative, not decorative

#### Input File Types

By default, the GUI's Browse dialog shows image files (`*.png *.jpg *.jpeg *.bmp *.tiff`). If your converter accepts a different file type (e.g. SVG, CSV, DXF), declare `"input_types"` in your `--get-params` JSON:

```json
"input_types": [
  ["SVG files", "*.svg"],
  ["All files", "*.*"]
]
```

Each entry is a `[label, pattern]` pair, exactly as tkinter's `filedialog` expects. The GUI will use this list for the Browse dialog when your converter is active, and will also skip the image preview for non-image file types, showing a neutral placeholder instead.

**If `input_types` is omitted**, the GUI falls back to the standard image filter — so existing converters require no changes.

#### Parameter Fields

| Field         | Required | Type   | Description                                          |
|---------------|----------|--------|------------------------------------------------------|
| `name`        | Yes      | string | Internal identifier (not displayed)                  |
| `flag`        | Yes      | string | CLI flag passed to your script (e.g. `--width`)      |
| `type`        | Yes      | string | Either `"float"` or `"int"`                          |
| `default`     | Yes      | number | Starting value shown in the GUI                      |
| `min`         | Yes      | number | Minimum value for the slider range                   |
| `max`         | Yes      | number | Maximum value for the slider range                   |
| `step`        | No       | number | Suggested increment (affects decimal display)        |
| `unit`        | No       | string | Unit label shown next to the value (e.g. `"mm"`)     |
| `label`       | Yes      | string | Human-readable name shown in the GUI                 |
| `description` | No       | string | Help text (for future tooltip support)               |

> **Note on slider limits:** The slider respects `min`/`max`, but the user can type any value in the text field. Your script should handle values outside the suggested range gracefully.

---

## How the GUI Calls Your Converter

When the user clicks "Generate G-code", the GUI constructs a command:

```
python your_converter.py <input_file> <output_gcode> --flag1 value1 --flag2 value2 ...
```

### Arguments

| Position | Content                              |
|----------|--------------------------------------|
| 1st      | Input file path                      |
| 2nd      | Output G-code file path              |
| Rest     | `--flag value` pairs from parameters |

### Expected Behaviour
- **stdout:** Print progress/status messages — these appear in the GUI log panel
- **stderr:** Print errors — shown in the log if exit code is non-zero
- **Exit code 0:** Success — GUI shows "Done!" popup
- **Exit code non-zero:** Failure — GUI shows error popup

---

## Minimal Converter Template

Copy this to create a new converter:

```python
#!/usr/bin/env python3
"""
my_converter.py — Brief description

Usage:
    python my_converter.py input.png output.gcode [options]
    python my_converter.py --get-params
"""

import argparse
import json
import sys

# ═══════════════════════════════════════════════════════════════════
# CONVERTER METADATA
# ═══════════════════════════════════════════════════════════════════

CONVERTER_INFO = {
    "name": "My New Converter",
    "description": "Describe what your converter does in one or two sentences.",
    "author": "Your Name",
    "version": "1.0",
    # Optional: declare accepted input file types for the Browse dialog.
    # Omit this field to fall back to the default image filter.
    # "input_types": [["SVG files", "*.svg"], ["All files", "*.*"]],
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
            "name": "feedrate",
            "flag": "--feedrate",
            "type": "int",
            "default": 3000,
            "min": 100,
            "max": 30000,
            "step": 100,
            "unit": "mm/min",
            "label": "Feedrate",
            "description": "Drawing speed"
        }
        # Add more parameters as needed...
    ]
}

# ═══════════════════════════════════════════════════════════════════
# YOUR CONVERSION LOGIC
# ═══════════════════════════════════════════════════════════════════

def convert(input_path, output_path, width, height, feedrate):
    """Your main conversion function."""

    # 1. Load and process the input file
    # from PIL import Image
    # img = Image.open(input_path).convert('L')
    # ...

    # 2. Generate G-code
    gcode_lines = []
    gcode_lines.append("G21          ; Millimetres")
    gcode_lines.append("G90          ; Absolute positioning")
    gcode_lines.append("G28          ; Home")
    gcode_lines.append("")

    # ... your algorithm here ...

    gcode_lines.append("")
    gcode_lines.append("G1 X0 Y0 F5000   ; Return home")
    gcode_lines.append("M18              ; Disable motors")

    # 3. Write output
    with open(output_path, 'w') as f:
        f.write("\n".join(gcode_lines))

    print(f"G-code written to: {output_path}")
    print(f"Total lines: {len(gcode_lines)}")


# ═══════════════════════════════════════════════════════════════════
# MAIN — handles --get-params and normal execution
# ═══════════════════════════════════════════════════════════════════

def main():
    # Protocol: respond to --get-params
    if "--get-params" in sys.argv:
        print(json.dumps(CONVERTER_INFO, indent=2))
        sys.exit(0)

    # Normal argument parsing
    parser = argparse.ArgumentParser(description=CONVERTER_INFO["description"])
    parser.add_argument("input", help="Input file")
    parser.add_argument("output", help="Output G-code file", nargs="?",
                        default="output.gcode")
    parser.add_argument("--width", type=float, default=100.0)
    parser.add_argument("--height", type=float, default=100.0)
    parser.add_argument("--feedrate", type=int, default=3000)

    args = parser.parse_args()

    print(f"Loading: {args.input}")
    print(f"Size: {args.width} x {args.height} mm")

    convert(
        input_path=args.input,
        output_path=args.output,
        width=args.width,
        height=args.height,
        feedrate=args.feedrate
    )


if __name__ == "__main__":
    main()
```

---

## Checklist for a New Converter

- [ ] File is placed in `Gcode_converters/` folder
- [ ] File ends in `.py`
- [ ] Responds to `--get-params` with valid JSON on stdout
- [ ] Accepts positional args: `input_path output_path`
- [ ] Accepts `--flag value` pairs matching the declared parameters
- [ ] Declares `"input_types"` if the input is not a standard image file
- [ ] Prints progress to stdout (shown in GUI log)
- [ ] Exits with code 0 on success
- [ ] Exits with non-zero code on failure (with error on stderr)

---

## G-code Output Conventions

For consistency across converters, follow these conventions in your generated G-code:

### Header

```gcode
; ==========================================
; Converter name / description
; Size: 100 x 100 mm
; Key parameters listed here
; ==========================================

G21          ; Millimetres
G90          ; Absolute positioning
G28          ; Home (sets current position as origin)
```

### Drawing

```gcode
G1 X... Y... F...   ; All drawing moves
```

### Footer

```gcode
G1 X0 Y0 F5000      ; Return to origin
M18                  ; Disable motors
```

### Key Rules

| Rule | Reason |
|------|--------|
| Always use `G21` (mm mode) | Consistency across converters |
| Always start with `G28` | Ensures homing macro runs |
| Use `G90` (absolute) | Easier to debug and visualise |
| Single continuous path | No pen lift mechanism yet |
| Return to `X0 Y0` at the end | So the head is out of the way |
| End with `M18` | Lets the user move the head by hand |

---

## Tips for Good Converters

1. **Print progress** — The GUI shows stdout in real-time. Print what you're doing:
   ```python
   print(f"Loading: {input_path}")
   print(f"Processing... {num_lines} lines")
   print(f"G-code written: {output_path}")
   ```

2. **Validate inputs** — Check the file exists, check parameters make sense:
   ```python
   if args.max_amp > (args.height / args.lines) / 2:
       print("WARNING: amplitude may cause line overlap", file=sys.stderr)
   ```

3. **Keep it single-file** — Each converter should be one `.py` file with all dependencies imported from standard libraries or common packages (numpy, Pillow).

4. **Handle edge cases** — What if the file is malformed? What if parameters are out of a sensible range?

5. **Test with `--get-params` first:**
   ```
   python your_converter.py --get-params
   ```
   Should print clean JSON and nothing else.

---

## Common Dependencies

The GUI assumes these are installed (document them for users):

```
pip install pillow numpy
```

If your converter needs additional packages, print a helpful error:

```python
try:
    import scipy
except ImportError:
    print("ERROR: This converter requires scipy. Install with: pip install scipy",
          file=sys.stderr)
    sys.exit(1)
```

---

## Future Extensions

The protocol is designed to be extended. Possible future fields in `--get-params` JSON:

| Field | Purpose |
|-------|---------|
| `"requires_pen_lift"` | Boolean — flags converters that need Z axis |
| `"estimated_time"` | Function to estimate plot duration |
| `"preview"` | Generate a preview image without full G-code |
| `"categories"` | Tags like `"photo"`, `"geometric"`, `"text"` |

These are not implemented yet but the JSON structure allows adding them without breaking existing converters.

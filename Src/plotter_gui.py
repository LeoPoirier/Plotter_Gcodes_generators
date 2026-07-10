#!/usr/bin/env python3
"""
plotter_gui.py - Modular GUI for G-code converters

Features:
  - Auto-discovers converters in the Gcode_converters/ subfolder
  - Queries each converter via --get-params for its parameters
  - Dynamically builds sliders + editable value fields per converter
  - Dropdown to switch between converters
  - Converter illustration image support
  - Editable fields allow values beyond slider range (slider greys out)
  - Saves last-used parameter values to Gcode_converters/default.conf
  - Reset button restores original parameter defaults from --get-params
  - Double-click run_plotter.bat to launch (no terminal needed)

Folder structure:
    your_folder/
      plotter_gui.py
      run_plotter.bat
      Gcode_converters/
        default.conf                     <- auto-generated defaults
        image_to_gcode_continous_s.py
        image_to_gcode_continous_s.png   <- optional illustration
        some_other_converter.py
        some_other_converter.png         <- optional illustration
        ...

Requirements: pillow, numpy
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import json
import sys
import os
from PIL import Image, ImageTk


# ==============================================================================
# DEFAULT.CONF MANAGEMENT
# ==============================================================================

def get_converters_dir():
    """Return path to Gcode_converters/ next to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, "Gcode_converters")


def get_defaults_path():
    """Return path to the default.conf file."""
    return os.path.join(get_converters_dir(), "default.conf")


def load_defaults():
    """
    Load saved defaults from default.conf.
    Returns a dict: { converter_filename: { param_name: value, ... }, ... }
    """
    path = get_defaults_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_defaults(defaults_dict):
    """
    Save defaults dict to default.conf.
    Format: { converter_filename: { param_name: value_str, ... }, ... }
    """
    path = get_defaults_path()
    conv_dir = get_converters_dir()
    if not os.path.isdir(conv_dir):
        os.makedirs(conv_dir, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(defaults_dict, f, indent=2)
    except IOError as e:
        print(f"Warning: could not save defaults: {e}")


# ==============================================================================
# CONVERTER DISCOVERY
# ==============================================================================

def discover_converters():
    """
    Scan Gcode_converters/ folder for .py files, call each with --get-params,
    and return a dict of { display_name: { path, info } }.
    """
    converters = {}
    conv_dir = get_converters_dir()

    if not os.path.isdir(conv_dir):
        return converters

    for filename in sorted(os.listdir(conv_dir)):
        if not filename.endswith(".py"):
            continue

        filepath = os.path.join(conv_dir, filename)

        try:
            result = subprocess.run(
                [sys.executable, filepath, "--get-params"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                info = json.loads(result.stdout)
                display_name = info.get("name", filename)

                # Look for illustration image (same name, .png or .jpg)
                base = os.path.splitext(filepath)[0]
                illustration = None
                for ext in (".png", ".jpg", ".jpeg", ".gif"):
                    candidate = base + ext
                    if os.path.isfile(candidate):
                        illustration = candidate
                        break
                # Also check if "illustration" field is in the JSON
                if not illustration and "illustration" in info:
                    ill_path = os.path.join(conv_dir, info["illustration"])
                    if os.path.isfile(ill_path):
                        illustration = ill_path

                converters[display_name] = {
                    "path": filepath,
                    "filename": filename,
                    "info": info,
                    "illustration": illustration
                }
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
            print(f"Skipping {filename}: {e}")

    return converters


# ==============================================================================
# GUI APPLICATION
# ==============================================================================

PREVIEW_SIZE = 280  # pixels for image preview area


class PlotterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Plotter  -  Image to G-code")
        self.root.resizable(True, True)
        self.root.minsize(760, 520)

        # Theme colors
        self.DARK   = "#1e1e2e"
        self.PANEL  = "#2a2a3d"
        self.ACCENT = "#7c6af7"
        self.FG     = "#cdd6f4"
        self.FG2    = "#a6adc8"
        self.ENTRY  = "#313244"
        self.GREY   = "#44446a"

        self.root.configure(bg=self.DARK)
        self._setup_styles()

        # State
        self.converters = {}
        self.current_converter = None
        self.param_widgets = []  # list of (param_info, entry_var, slider, var) tuples
        self._suppress_slider_trace = False
        self.saved_defaults = load_defaults()  # Load persistent defaults

        # Build UI
        self._build_layout()
        self._discover_and_populate()

    # --------------------------------------------------------------------------
    # Styles
    # --------------------------------------------------------------------------

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame",            background=self.DARK)
        style.configure("TLabel",            background=self.DARK, foreground=self.FG,
                        font=("Segoe UI", 10))
        style.configure("Dim.TLabel",        background=self.DARK, foreground=self.FG2,
                        font=("Segoe UI", 9))
        style.configure("Desc.TLabel",       background=self.DARK, foreground=self.FG2,
                        font=("Segoe UI", 9, "italic"))
        style.configure("TLabelframe",       background=self.PANEL, foreground=self.FG)
        style.configure("TLabelframe.Label", background=self.PANEL, foreground=self.ACCENT,
                        font=("Segoe UI", 10, "bold"))
        style.configure("TEntry",            fieldbackground=self.ENTRY, foreground=self.FG,
                        insertcolor=self.FG)
        style.configure("TScale",            background=self.DARK, troughcolor=self.ENTRY)
        style.configure("Disabled.TScale",   background=self.GREY, troughcolor=self.GREY)
        style.configure("TScrollbar",        background=self.PANEL, troughcolor=self.DARK,
                        arrowcolor=self.FG2)
        style.configure("TButton",           background=self.ACCENT, foreground="white",
                        font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("Reset.TButton",     background="#555577", foreground="white",
                        font=("Segoe UI", 9), padding=4)
        style.configure("TCombobox",         fieldbackground=self.ENTRY, foreground=self.FG,
                        selectbackground=self.ACCENT)
        style.map("TButton",
                  background=[("active", "#6a5ae0"), ("disabled", "#44446a")])
        style.map("Reset.TButton",
                  background=[("active", "#6a6a8a")])
        style.configure("TCheckbutton",      background=self.DARK, foreground=self.FG,
                        font=("Segoe UI", 10))
        style.map("TCheckbutton",
                  background=[("active", self.DARK)])
        style.map("TCombobox",
                  fieldbackground=[("readonly", self.ENTRY)])

    # --------------------------------------------------------------------------
    # Layout
    # --------------------------------------------------------------------------

    def _build_layout(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.grid(row=0, column=0, sticky="nsew")

        # Allow root and outer frame to expand with the window
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)  # right panel expands horizontally

        self.left  = ttk.Frame(outer)
        self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        self.right = ttk.Frame(outer)
        self.right.grid(row=0, column=1, sticky="nsew")
        self.right.rowconfigure(2, weight=1)   # log section grows vertically
        self.right.columnconfigure(0, weight=1)

        # -- Converter selector ------------------------------------------------
        f_conv = ttk.LabelFrame(self.left, text=" Converter ", padding=10)
        f_conv.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(f_conv, text="Algorithm:").grid(row=0, column=0, sticky="w")
        self.converter_var = tk.StringVar()
        self.converter_combo = ttk.Combobox(
            f_conv, textvariable=self.converter_var,
            state="readonly", width=36
        )
        self.converter_combo.grid(row=0, column=1, padx=6, columnspan=2)
        self.converter_combo.bind("<<ComboboxSelected>>", self._on_converter_changed)

        self.desc_label = ttk.Label(f_conv, text="", style="Desc.TLabel", wraplength=420)
        self.desc_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # -- File selection ----------------------------------------------------
        f_files = ttk.LabelFrame(self.left, text=" Files ", padding=10)
        f_files.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(f_files, text="Input file:").grid(row=0, column=0, sticky="w")
        self.input_var = tk.StringVar()
        ttk.Entry(f_files, textvariable=self.input_var, width=36).grid(row=0, column=1, padx=6)
        ttk.Button(f_files, text="Browse", command=self._browse_input).grid(row=0, column=2)

        ttk.Label(f_files, text="Output .gcode:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.output_var = tk.StringVar()
        ttk.Entry(f_files, textvariable=self.output_var, width=36).grid(
            row=1, column=1, padx=6, pady=(6, 0))
        ttk.Button(f_files, text="Browse", command=self._browse_output).grid(
            row=1, column=2, pady=(6, 0))

        # -- Parameters (dynamic) ----------------------------------------------
        self.f_params = ttk.LabelFrame(self.left, text=" Parameters ", padding=10)
        self.f_params.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        self.params_inner = ttk.Frame(self.f_params)
        self.params_inner.grid(row=0, column=0, sticky="ew")

        # -- Reset button (restores --get-params defaults) ---------------------
        self.reset_btn = ttk.Button(
            self.left, text="Reset to Original Defaults",
            command=self._reset_to_original, style="Reset.TButton"
        )
        self.reset_btn.grid(row=3, column=0, sticky="ew", ipady=2, pady=(0, 4))

        # -- Run button --------------------------------------------------------
        self.run_btn = ttk.Button(self.left, text="Generate G-code", command=self._run)
        self.run_btn.grid(row=4, column=0, sticky="ew", ipady=4, pady=(0, 4))

        # -- Preview button ----------------------------------------------------
        self.preview_btn = ttk.Button(
            self.left, text="Preview G-code", command=self._preview_gcode
        )
        self.preview_btn.grid(row=5, column=0, sticky="ew", ipady=4, pady=(0, 4))

        # -- Right: converter illustration -------------------------------------
        f_illust = ttk.LabelFrame(self.right, text=" Converter Preview ", padding=8)
        f_illust.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.illust_label = tk.Label(
            f_illust, text="No illustration available",
            bg=self.PANEL, fg=self.FG2, font=("Segoe UI", 9)
        )
        self.illust_label.grid(sticky="nsew")

        # -- Right: image preview ----------------------------------------------
        f_prev = ttk.LabelFrame(self.right, text=" Image Preview ", padding=8)
        f_prev.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        # Use a Canvas for fixed-size preview area
        self.preview_canvas = tk.Canvas(
            f_prev, width=PREVIEW_SIZE, height=PREVIEW_SIZE,
            bg=self.PANEL, highlightthickness=0
        )
        self.preview_canvas.grid(sticky="nsew")
        self.preview_canvas.create_text(
            PREVIEW_SIZE // 2, PREVIEW_SIZE // 2,
            text="No image selected", fill=self.FG2, font=("Segoe UI", 10)
        )

        # -- Right: log --------------------------------------------------------
        f_log = ttk.LabelFrame(self.right, text=" Log ", padding=8)
        f_log.grid(row=2, column=0, sticky="nsew")
        f_log.rowconfigure(0, weight=1)
        f_log.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            f_log, width=38, height=8,
            bg=self.PANEL, fg=self.FG2,
            font=("Consolas", 9), relief="flat"
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(f_log, command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=sb.set)

    # --------------------------------------------------------------------------
    # Converter discovery
    # --------------------------------------------------------------------------

    def _discover_and_populate(self):
        self.converters = discover_converters()

        if not self.converters:
            self.converter_combo["values"] = ["(no converters found)"]
            self.converter_var.set("(no converters found)")
            self.log_text.insert("1.0",
                "No converters found!\n\n"
                "Make sure Gcode_converters/ folder exists next to this script\n"
                "and contains .py files that support --get-params.\n"
            )
            return

        names = list(self.converters.keys())
        self.converter_combo["values"] = names
        self.converter_var.set(names[0])
        self._on_converter_changed()

    # --------------------------------------------------------------------------
    # Dynamic parameter UI
    # --------------------------------------------------------------------------

    def _on_converter_changed(self, event=None):
        name = self.converter_var.get()
        if name not in self.converters:
            return

        self.current_converter = self.converters[name]
        info = self.current_converter["info"]

        # Update description
        self.desc_label.config(text=info.get("description", ""))

        # Update illustration
        self._load_illustration(self.current_converter.get("illustration"))

        # Rebuild parameter widgets (uses saved defaults if available)
        self._build_param_widgets(info.get("parameters", []))

    def _load_illustration(self, path):
        """Load converter illustration image."""
        if path and os.path.isfile(path):
            try:
                img = Image.open(path)
                img.thumbnail((PREVIEW_SIZE, 120))
                photo = ImageTk.PhotoImage(img)
                self.illust_label.config(image=photo, text="")
                self.illust_label.image = photo
                return
            except Exception:
                pass
        self.illust_label.config(image="", text="No illustration available")
        self.illust_label.image = None

    def _get_effective_default(self, param):
        """
        Return the effective default value for a parameter.
        Checks saved defaults (from default.conf) first, then falls back
        to the original default from --get-params.
        """
        if self.current_converter:
            filename = self.current_converter["filename"]
            saved = self.saved_defaults.get(filename, {})
            param_name = param["name"]
            if param_name in saved:
                # Boolean parameters: return as bool
                if param.get("type") == "bool":
                    return saved[param_name].lower() in ("true", "1", "yes")
                try:
                    return float(saved[param_name])
                except (ValueError, TypeError):
                    pass
        return param["default"]

    def _build_param_widgets(self, params):
        # Destroy old widgets
        for child in self.params_inner.winfo_children():
            child.destroy()
        self.param_widgets = []

        if not params:
            ttk.Label(self.params_inner, text="No parameters",
                      style="Dim.TLabel").grid(row=0, column=0)
            return

        # Header row
        ttk.Label(self.params_inner, text="Parameter", style="Dim.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(self.params_inner, text="Slider", style="Dim.TLabel").grid(
            row=0, column=1, sticky="w", pady=(0, 4))
        ttk.Label(self.params_inner, text="Value", style="Dim.TLabel").grid(
            row=0, column=2, sticky="w", pady=(0, 4))
        ttk.Label(self.params_inner, text="Unit", style="Dim.TLabel").grid(
            row=0, column=3, sticky="w", pady=(0, 4))

        for i, p in enumerate(params):
            row = i + 1
            effective_default = self._get_effective_default(p)

            # ==============================================================
            # Boolean parameter -> render as a tickbox (checkbox)
            # ==============================================================
            if p.get("type") == "bool":
                bool_var = tk.BooleanVar(value=bool(effective_default))

                # Checkbox spanning the slider+entry columns
                label_text = p.get("label", p["name"])
                cb = ttk.Checkbutton(
                    self.params_inner, text=label_text,
                    variable=bool_var
                )
                cb.grid(row=row, column=0, columnspan=2, sticky="w", pady=3, padx=(0, 8))

                # Optional description as dim label in the unit column
                desc = p.get("description", "")
                if desc:
                    ttk.Label(self.params_inner, text=desc,
                              style="Dim.TLabel").grid(
                        row=row, column=2, columnspan=2, sticky="w", padx=(2, 0))

                # Store: (param_info, entry_var=None, slider=None, var=BooleanVar)
                self.param_widgets.append((p, None, None, bool_var))
                continue

            # ==============================================================
            # Numeric parameter (int / float) -> slider + entry (original)
            # ==============================================================
            var = tk.DoubleVar(value=effective_default)

            # Label
            label_text = p.get("label", p["name"])
            ttk.Label(self.params_inner, text=label_text).grid(
                row=row, column=0, sticky="w", pady=3, padx=(0, 8))

            # Slider
            p_min = p.get("min", 0)
            p_max = p.get("max", 100)
            slider = ttk.Scale(
                self.params_inner, variable=var,
                from_=p_min, to=p_max,
                orient="horizontal", length=180
            )
            slider.grid(row=row, column=1, padx=4, pady=3)

            # Editable entry field
            entry_var = tk.StringVar(value=self._format_value(p, effective_default))
            entry = ttk.Entry(self.params_inner, textvariable=entry_var, width=8)
            entry.grid(row=row, column=2, padx=4, pady=3)

            # If effective_default is outside slider range, grey out slider
            if effective_default < p_min or effective_default > p_max:
                slider.state(["disabled"])

            # Unit label
            unit = p.get("unit", "")
            ttk.Label(self.params_inner, text=unit, style="Dim.TLabel").grid(
                row=row, column=3, sticky="w", padx=(2, 0))

            # -- Slider -> Entry binding --
            def _slider_to_entry(*args, v=var, ev=entry_var, param=p, sl=slider):
                if self._suppress_slider_trace:
                    return
                ev.set(self._format_value(param, v.get()))
                # Re-enable slider style (value is in range since slider moved it)
                sl.state(["!disabled"])
            var.trace_add("write", _slider_to_entry)

            # -- Entry -> Slider binding (on Enter or FocusOut) --
            def _entry_committed(event, v=var, ev=entry_var, param=p, sl=slider):
                try:
                    val = float(ev.get())
                    p_min_local = param.get("min", 0)
                    p_max_local = param.get("max", 100)

                    if val < p_min_local or val > p_max_local:
                        # Value is out of slider range - grey out slider
                        sl.state(["disabled"])
                    else:
                        # Value is in range - update slider and re-enable
                        sl.state(["!disabled"])
                        self._suppress_slider_trace = True
                        v.set(val)
                        self._suppress_slider_trace = False
                except ValueError:
                    pass

            entry.bind("<Return>", _entry_committed)
            entry.bind("<FocusOut>", _entry_committed)

            # -- Re-enable slider on click --
            def _slider_clicked(event, sl=slider, v=var, ev=entry_var, param=p):
                sl.state(["!disabled"])
                # Sync entry back to slider's current value
                ev.set(self._format_value(param, v.get()))
            slider.bind("<Button-1>", _slider_clicked)

            # Store reference: (param_info, entry_var, slider, var)
            self.param_widgets.append((p, entry_var, slider, var))

    def _format_value(self, param, value):
        ptype = param.get("type", "float")
        if ptype == "int":
            return str(int(round(value)))
        else:
            step = param.get("step", 0.1)
            if step >= 1:
                return f"{value:.0f}"
            elif step >= 0.1:
                return f"{value:.1f}"
            else:
                return f"{value:.2f}"

    # --------------------------------------------------------------------------
    # Defaults: save and reset
    # --------------------------------------------------------------------------

    def _save_current_as_defaults(self):
        """Save the current parameter values to default.conf."""
        if not self.current_converter:
            return

        filename = self.current_converter["filename"]
        param_values = {}
        for param_info, entry_var, slider, var in self.param_widgets:
            if param_info.get("type") == "bool":
                # Boolean: store "true" / "false"
                param_values[param_info["name"]] = "true" if var.get() else "false"
            else:
                param_values[param_info["name"]] = entry_var.get().strip()

        self.saved_defaults[filename] = param_values
        save_defaults(self.saved_defaults)

    def _reset_to_original(self):
        """
        Reset all parameter values to the original defaults as reported
        by the converter's --get-params query (ignoring default.conf).
        """
        if not self.current_converter:
            return

        info = self.current_converter["info"]
        params = info.get("parameters", [])

        for (param_info, entry_var, slider, var), p in zip(self.param_widgets, params):
            original_default = p["default"]

            # Boolean: just reset the BooleanVar
            if param_info.get("type") == "bool":
                var.set(bool(original_default))
                continue

            # Numeric (int/float): reset slider + entry
            p_min = p.get("min", 0)
            p_max = p.get("max", 100)

            # Update entry field
            entry_var.set(self._format_value(p, original_default))

            # Update slider
            if original_default < p_min or original_default > p_max:
                slider.state(["disabled"])
            else:
                slider.state(["!disabled"])
                self._suppress_slider_trace = True
                var.set(original_default)
                self._suppress_slider_trace = False

        # Also remove from saved defaults so next launch uses originals
        filename = self.current_converter["filename"]
        if filename in self.saved_defaults:
            del self.saved_defaults[filename]
            save_defaults(self.saved_defaults)

        self.log_text.insert(tk.END, "Parameters reset to original defaults.\n")

    # --------------------------------------------------------------------------
    # File browsing
    # --------------------------------------------------------------------------

    def _browse_input(self):
        # Dynamically use filetypes declared by the active converter
        default_types = [("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff"), ("All files", "*.*")]

        if self.current_converter:
            raw = self.current_converter["info"].get("input_types", default_types)
            # Normalize: convert list-of-lists to list-of-tuples for tkinter
            filetypes = [tuple(ft) for ft in raw]
        else:
            filetypes = default_types

        path = filedialog.askopenfilename(
            title="Select Input File",
            filetypes=filetypes
        )
        if path:
            self.input_var.set(path)
            base = os.path.splitext(path)[0]
            self.output_var.set(base + ".gcode")
            self._load_preview(path)

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Save G-code As",
            defaultextension=".gcode",
            filetypes=[("G-code files", "*.gcode"), ("All files", "*.*")]
        )
        if path:
            self.output_var.set(path)

    def _load_preview(self, path):
        """Load image preview into the canvas, properly scaled."""
        # Only attempt image preview for known image formats
        IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif"}
        ext = os.path.splitext(path)[1].lower()

        if ext not in IMAGE_EXTS:
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(
                PREVIEW_SIZE // 2, PREVIEW_SIZE // 2,
                text=f"No preview for\n{ext} files",
                fill=self.FG2, font=("Segoe UI", 10)
            )
            return

        try:
            img = Image.open(path)
            # Scale to fit the preview area while maintaining aspect ratio
            img.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)

            # Clear canvas and draw centered image
            self.preview_canvas.delete("all")
            self.preview_canvas.create_image(
                PREVIEW_SIZE // 2, PREVIEW_SIZE // 2,
                anchor="center", image=photo
            )
            # Keep a reference to prevent garbage collection
            self.preview_canvas._photo = photo
        except Exception as e:
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(
                PREVIEW_SIZE // 2, PREVIEW_SIZE // 2,
                text=f"Preview error:\n{e}",
                fill=self.FG2, font=("Segoe UI", 9)
            )

    # --------------------------------------------------------------------------
    # Run converter
    # --------------------------------------------------------------------------

    def _run(self):
        if not self.current_converter:
            messagebox.showerror("Error", "No converter selected.")
            return

        input_path  = self.input_var.get().strip()
        output_path = self.output_var.get().strip()

        if not input_path:
            messagebox.showerror("Error", "Please select an input file.")
            return
        if not os.path.isfile(input_path):
            messagebox.showerror("Error", f"Input file not found:\n{input_path}")
            return
        if not output_path:
            messagebox.showerror("Error", "Please set an output G-code path.")
            return

        backend = self.current_converter["path"]

        # Build command with parameters from entry fields (allows beyond-slider values)
        cmd = [sys.executable, backend, input_path, output_path]

        for param_info, entry_var, slider, var in self.param_widgets:
            flag = param_info["flag"]
            if param_info.get("type") == "bool":
                # Boolean: pass "true" or "false"
                value = "true" if var.get() else "false"
            else:
                value = entry_var.get().strip()
            if value:
                cmd.extend([flag, value])

        self.run_btn.config(state="disabled", text="Generating...")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, f"Running: {self.current_converter['filename']}\n")
        self.log_text.insert(tk.END, f"Command: {' '.join(cmd[1:])}\n\n")
        self.root.update()

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            self.log_text.delete("1.0", tk.END)
            self.log_text.insert(tk.END, result.stdout)
            if result.returncode != 0:
                self.log_text.insert(tk.END, "\n--- ERRORS ---\n" + result.stderr)
                messagebox.showerror("Failed",
                    "Converter returned an error.\nCheck the log below.")
            else:
                # -- Save current values as new defaults --
                self._save_current_as_defaults()
                self.log_text.insert(tk.END, "\n[Defaults saved for next session]\n")
                messagebox.showinfo("Done!", f"G-code saved to:\n{output_path}")
        except subprocess.TimeoutExpired:
            messagebox.showerror("Timeout", "Converter took too long (>120s). Aborted.")
        except Exception as e:
            self.log_text.insert(tk.END, f"Failed to launch:\n{e}")
            messagebox.showerror("Error", str(e))
        finally:
            self.run_btn.config(state="normal", text="Generate G-code")

    def _preview_gcode(self):
        """Launch the GCode viewer GUI with the current output file as argument."""
        output_path = self.output_var.get().strip()
        if not output_path:
            messagebox.showerror("Error", "No output G-code path set.\nGenerate G-code first.")
            return
        if not os.path.isfile(output_path):
            messagebox.showerror(
                "File Not Found",
                f"G-code file not found:\n{output_path}\n\nGenerate the G-code first."
            )
            return

        script_dir = os.path.dirname(os.path.abspath(__file__))
        viewer_path = os.path.join(script_dir, "Gcode_viewer_GUI.py")

        if not os.path.isfile(viewer_path):
            messagebox.showerror(
                "Viewer Not Found",
                f"Could not find Gcode_viewer_GUI.py next to this script:\n{viewer_path}"
            )
            return

        try:
            subprocess.Popen([sys.executable, viewer_path, output_path])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch viewer:\n{e}")


# ==============================================================================
# Entry point
# ==============================================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = PlotterGUI(root)
    root.mainloop()

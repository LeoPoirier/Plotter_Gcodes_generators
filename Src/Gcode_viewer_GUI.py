#!/usr/bin/env python3
"""
Gcode_viewer_GUI.py — GUI for G-code preview visualization

Displays a parameter panel to configure rendering options, calls
Gcode_viewer_generator.py to produce a PNG, and displays the result.

Usage:
    python Gcode_viewer_GUI.py [path/to/file.gcode]

Can be launched standalone or from plotter_gui.py via the "Preview G-code" button.

Requirements: pip install pillow
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
import subprocess
import json
import sys
import os
from PIL import Image, ImageTk


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATOR_PATH = os.path.join(SCRIPT_DIR, "Gcode_viewer_generator.py")
TEMP_DIR = os.path.join(SCRIPT_DIR, "Temp")
PREVIEW_PATH = os.path.join(TEMP_DIR, "preview.png")


# ══════════════════════════════════════════════════════════════════════════════
# GUI APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

class GcodeViewerGUI:
    def __init__(self, root, gcode_path=None):
        self.root = root
        self.root.title("G-code Viewer")
        self.root.resizable(True, True)
        self.root.minsize(900, 600)

        # Theme colors (same as plotter_gui)
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
        self.gcode_path = gcode_path or ""
        self.preview_photo = None  # keep reference to prevent GC

        # Ensure Temp directory exists
        os.makedirs(TEMP_DIR, exist_ok=True)

        # Build UI
        self._build_layout()

        # If a gcode path was provided, auto-render
        if self.gcode_path and os.path.isfile(self.gcode_path):
            self.gcode_var.set(self.gcode_path)
            self.root.after(200, self._render)

    # ──────────────────────────────────────────────────────────────────────
    # Styles
    # ──────────────────────────────────────────────────────────────────────

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame",            background=self.DARK)
        style.configure("TLabel",            background=self.DARK, foreground=self.FG,
                        font=("Segoe UI", 10))
        style.configure("Dim.TLabel",        background=self.DARK, foreground=self.FG2,
                        font=("Segoe UI", 9))
        style.configure("Info.TLabel",       background=self.PANEL, foreground=self.FG,
                        font=("Consolas", 9))
        style.configure("TLabelframe",       background=self.PANEL, foreground=self.FG)
        style.configure("TLabelframe.Label", background=self.PANEL, foreground=self.ACCENT,
                        font=("Segoe UI", 10, "bold"))
        style.configure("TEntry",            fieldbackground=self.ENTRY, foreground=self.FG,
                        insertcolor=self.FG)
        style.configure("TScale",            background=self.DARK, troughcolor=self.ENTRY)
        style.configure("TScrollbar",        background=self.PANEL, troughcolor=self.DARK,
                        arrowcolor=self.FG2)
        style.configure("TButton",           background=self.ACCENT, foreground="white",
                        font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("Small.TButton",     background=self.ACCENT, foreground="white",
                        font=("Segoe UI", 9), padding=4)
        style.configure("TCombobox",         fieldbackground=self.ENTRY, foreground=self.FG,
                        selectbackground=self.ACCENT)
        style.configure("TCheckbutton",      background=self.DARK, foreground=self.FG,
                        font=("Segoe UI", 10))
        style.map("TButton",
                  background=[("active", "#6a5ae0"), ("disabled", "#44446a")])
        style.map("TCombobox",
                  fieldbackground=[("readonly", self.ENTRY)])
        style.map("TCheckbutton",
                  background=[("active", self.DARK)])

    # ──────────────────────────────────────────────────────────────────────
    # Layout
    # ──────────────────────────────────────────────────────────────────────

    def _build_layout(self):
        # Main outer frame
        outer = ttk.Frame(self.root, padding=10)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)  # preview area expands

        # ══ LEFT PANEL (parameters) ═══════════════════════════════════════
        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        # ── File selection ────────────────────────────────────────────────
        f_file = ttk.LabelFrame(left, text=" G-code File ", padding=8)
        f_file.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.gcode_var = tk.StringVar(value=self.gcode_path)
        ttk.Entry(f_file, textvariable=self.gcode_var, width=34).grid(
            row=0, column=0, padx=(0, 4))
        ttk.Button(f_file, text="Browse", style="Small.TButton",
                   command=self._browse_gcode).grid(row=0, column=1)

        # ── G1 Line Settings ─────────────────────────────────────────────
        f_g1 = ttk.LabelFrame(left, text=" G1 (Draw) Lines ", padding=8)
        f_g1.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        # Line Width
        ttk.Label(f_g1, text="Width:").grid(row=0, column=0, sticky="w")
        self.line_width_var = tk.DoubleVar(value=0.5)
        self.line_width_slider = ttk.Scale(
            f_g1, variable=self.line_width_var, from_=0.1, to=5.0,
            orient="horizontal", length=120)
        self.line_width_slider.grid(row=0, column=1, padx=4)
        self.line_width_entry = ttk.Entry(f_g1, width=6)
        self.line_width_entry.insert(0, "0.5")
        self.line_width_entry.grid(row=0, column=2, padx=2)
        ttk.Label(f_g1, text="mm", style="Dim.TLabel").grid(row=0, column=3)
        self._bind_slider_entry(self.line_width_var, self.line_width_entry, self.line_width_slider)

        # Line Color
        ttk.Label(f_g1, text="Color:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.line_color_var = tk.StringVar(value="#000000")
        self.line_color_btn = tk.Button(
            f_g1, width=4, bg="#000000", relief="raised",
            command=lambda: self._pick_color(self.line_color_var, self.line_color_btn))
        self.line_color_btn.grid(row=1, column=1, sticky="w", padx=4, pady=(4, 0))
        ttk.Label(f_g1, textvariable=self.line_color_var,
                  style="Dim.TLabel").grid(row=1, column=2, columnspan=2, sticky="w", pady=(4, 0))

        # Line Alpha
        ttk.Label(f_g1, text="Opacity:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.line_alpha_var = tk.DoubleVar(value=1.0)
        self.line_alpha_slider = ttk.Scale(
            f_g1, variable=self.line_alpha_var, from_=0.0, to=1.0,
            orient="horizontal", length=120)
        self.line_alpha_slider.grid(row=2, column=1, padx=4, pady=(4, 0))
        self.line_alpha_entry = ttk.Entry(f_g1, width=6)
        self.line_alpha_entry.insert(0, "1.0")
        self.line_alpha_entry.grid(row=2, column=2, padx=2, pady=(4, 0))
        self._bind_slider_entry(self.line_alpha_var, self.line_alpha_entry, self.line_alpha_slider)

        # ── G0 (Travel) Settings ─────────────────────────────────────────
        f_g0 = ttk.LabelFrame(left, text=" G0 (Travel) Lines ", padding=8)
        f_g0.grid(row=2, column=0, sticky="ew", pady=(0, 6))

        # Show G0 checkbox
        self.show_g0_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_g0, text="Show travel moves",
                        variable=self.show_g0_var).grid(row=0, column=0, columnspan=4, sticky="w")

        # G0 Width
        ttk.Label(f_g0, text="Width:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.g0_width_var = tk.DoubleVar(value=0.3)
        self.g0_width_slider = ttk.Scale(
            f_g0, variable=self.g0_width_var, from_=0.1, to=5.0,
            orient="horizontal", length=120)
        self.g0_width_slider.grid(row=1, column=1, padx=4, pady=(4, 0))
        self.g0_width_entry = ttk.Entry(f_g0, width=6)
        self.g0_width_entry.insert(0, "0.3")
        self.g0_width_entry.grid(row=1, column=2, padx=2, pady=(4, 0))
        ttk.Label(f_g0, text="mm", style="Dim.TLabel").grid(row=1, column=3, pady=(4, 0))
        self._bind_slider_entry(self.g0_width_var, self.g0_width_entry, self.g0_width_slider)

        # G0 Color
        ttk.Label(f_g0, text="Color:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.g0_color_var = tk.StringVar(value="#FF0000")
        self.g0_color_btn = tk.Button(
            f_g0, width=4, bg="#FF0000", relief="raised",
            command=lambda: self._pick_color(self.g0_color_var, self.g0_color_btn))
        self.g0_color_btn.grid(row=2, column=1, sticky="w", padx=4, pady=(4, 0))
        ttk.Label(f_g0, textvariable=self.g0_color_var,
                  style="Dim.TLabel").grid(row=2, column=2, columnspan=2, sticky="w", pady=(4, 0))

        # G0 Alpha
        ttk.Label(f_g0, text="Opacity:").grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.g0_alpha_var = tk.DoubleVar(value=0.3)
        self.g0_alpha_slider = ttk.Scale(
            f_g0, variable=self.g0_alpha_var, from_=0.0, to=1.0,
            orient="horizontal", length=120)
        self.g0_alpha_slider.grid(row=3, column=1, padx=4, pady=(4, 0))
        self.g0_alpha_entry = ttk.Entry(f_g0, width=6)
        self.g0_alpha_entry.insert(0, "0.3")
        self.g0_alpha_entry.grid(row=3, column=2, padx=2, pady=(4, 0))
        self._bind_slider_entry(self.g0_alpha_var, self.g0_alpha_entry, self.g0_alpha_slider)

        # ── Color Mode & Markers ─────────────────────────────────────────
        f_mode = ttk.LabelFrame(left, text=" Color Mode & Markers ", padding=8)
        f_mode.grid(row=3, column=0, sticky="ew", pady=(0, 6))

        # Color mode dropdown
        ttk.Label(f_mode, text="Mode:").grid(row=0, column=0, sticky="w")
        self.color_mode_var = tk.StringVar(value="solid")
        mode_combo = ttk.Combobox(
            f_mode, textvariable=self.color_mode_var,
            values=["solid", "gradient", "feedrate"],
            state="readonly", width=12)
        mode_combo.grid(row=0, column=1, columnspan=2, sticky="w", padx=4)

        # Show Start / End checkboxes
        self.show_start_var = tk.BooleanVar(value=False)
        self.show_end_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_mode, text="Show start point",
                        variable=self.show_start_var).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Checkbutton(f_mode, text="Show end point",
                        variable=self.show_end_var).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))

        # Nth segment
        ttk.Label(f_mode, text="Highlight Nth:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.nth_var = tk.IntVar(value=0)
        self.nth_slider = ttk.Scale(
            f_mode, variable=self.nth_var, from_=0, to=500,
            orient="horizontal", length=100)
        self.nth_slider.grid(row=3, column=1, padx=4, pady=(6, 0))
        self.nth_entry = ttk.Entry(f_mode, width=6)
        self.nth_entry.insert(0, "0")
        self.nth_entry.grid(row=3, column=2, padx=2, pady=(6, 0))
        self._bind_slider_entry(self.nth_var, self.nth_entry, self.nth_slider, is_int=True)

        # Nth color
        ttk.Label(f_mode, text="Nth Color:").grid(row=4, column=0, sticky="w", pady=(4, 0))
        self.nth_color_var = tk.StringVar(value="#00FF00")
        self.nth_color_btn = tk.Button(
            f_mode, width=4, bg="#00FF00", relief="raised",
            command=lambda: self._pick_color(self.nth_color_var, self.nth_color_btn))
        self.nth_color_btn.grid(row=4, column=1, sticky="w", padx=4, pady=(4, 0))
        ttk.Label(f_mode, textvariable=self.nth_color_var,
                  style="Dim.TLabel").grid(row=4, column=2, sticky="w", pady=(4, 0))

        # ── Background & DPI ─────────────────────────────────────────────
        f_misc = ttk.LabelFrame(left, text=" Background & Output ", padding=8)
        f_misc.grid(row=4, column=0, sticky="ew", pady=(0, 6))

        # Background Color
        ttk.Label(f_misc, text="BG Color:").grid(row=0, column=0, sticky="w")
        self.bg_color_var = tk.StringVar(value="#FFFFFF")
        self.bg_color_btn = tk.Button(
            f_misc, width=4, bg="#FFFFFF", relief="raised",
            command=lambda: self._pick_color(self.bg_color_var, self.bg_color_btn))
        self.bg_color_btn.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(f_misc, textvariable=self.bg_color_var,
                  style="Dim.TLabel").grid(row=0, column=2, columnspan=2, sticky="w")

        # DPI
        ttk.Label(f_misc, text="DPI:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.dpi_var = tk.IntVar(value=150)
        self.dpi_slider = ttk.Scale(
            f_misc, variable=self.dpi_var, from_=72, to=600,
            orient="horizontal", length=120)
        self.dpi_slider.grid(row=1, column=1, padx=4, pady=(4, 0))
        self.dpi_entry = ttk.Entry(f_misc, width=6)
        self.dpi_entry.insert(0, "150")
        self.dpi_entry.grid(row=1, column=2, padx=2, pady=(4, 0))
        ttk.Label(f_misc, text="dpi", style="Dim.TLabel").grid(row=1, column=3, pady=(4, 0))
        self._bind_slider_entry(self.dpi_var, self.dpi_entry, self.dpi_slider, is_int=True)

        # ── Render Button ─────────────────────────────────────────────────
        self.render_btn = ttk.Button(left, text="Render Preview", command=self._render)
        self.render_btn.grid(row=5, column=0, sticky="ew", ipady=4, pady=(4, 4))

        # ── Save Button ───────────────────────────────────────────────────
        self.save_btn = ttk.Button(left, text="Save Image As...",
                                   style="Small.TButton", command=self._save_image)
        self.save_btn.grid(row=6, column=0, sticky="ew", pady=(0, 4))

        # ══ RIGHT PANEL (preview + info) ══════════════════════════════════
        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        # ── Preview area ──────────────────────────────────────────────────
        f_preview = ttk.LabelFrame(right, text=" Preview (scroll=zoom, drag=pan, dbl-click=fit) ", padding=4)
        f_preview.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        f_preview.rowconfigure(0, weight=1)
        f_preview.columnconfigure(0, weight=1)

        # Canvas for zoom+pan image display
        self.canvas = tk.Canvas(
            f_preview, bg=self.PANEL, highlightthickness=0, cursor="fleur")
        self.canvas.grid(row=0, column=0, sticky="nsew")

        # Placeholder text
        self.canvas.create_text(
            200, 150, text="Click 'Render Preview' to generate",
            fill=self.FG2, font=("Segoe UI", 11), tags="placeholder")

        # Zoom & pan state
        self._zoom = 1.0       # current zoom factor (1.0 = fit-to-canvas)
        self._pan_x = 0.0      # pan offset in image pixels
        self._pan_y = 0.0
        self._drag_start = None

        # Bind events
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)          # Windows/macOS
        self.canvas.bind("<Button-4>", self._on_mousewheel_linux_up)   # Linux scroll up
        self.canvas.bind("<Button-5>", self._on_mousewheel_linux_down) # Linux scroll down
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_pan_end)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)        # Middle-click pan
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_end)
        self.canvas.bind("<Double-Button-1>", self._on_fit_to_canvas)

        # ── Info bar ──────────────────────────────────────────────────────
        f_info = ttk.LabelFrame(right, text=" Info ", padding=6)
        f_info.grid(row=1, column=0, sticky="ew")
        f_info.columnconfigure(0, weight=1)

        self.info_var = tk.StringVar(value="No preview generated yet.")
        ttk.Label(f_info, textvariable=self.info_var, style="Info.TLabel",
                  wraplength=500).grid(row=0, column=0, sticky="w")

        # ── Log ───────────────────────────────────────────────────────────
        f_log = ttk.LabelFrame(right, text=" Log ", padding=4)
        f_log.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        f_log.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            f_log, width=50, height=4,
            bg=self.PANEL, fg=self.FG2,
            font=("Consolas", 9), relief="flat")
        self.log_text.grid(row=0, column=0, sticky="ew")
        log_sb = ttk.Scrollbar(f_log, command=self.log_text.yview)
        log_sb.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=log_sb.set)

    # ──────────────────────────────────────────────────────────────────────
    # Slider ↔ Entry binding helper
    # ──────────────────────────────────────────────────────────────────────

    def _bind_slider_entry(self, var, entry, slider, is_int=False):
        """Two-way binding between a Scale variable and an Entry widget."""
        self._suppress = False

        def _slider_changed(*args):
            if self._suppress:
                return
            val = var.get()
            entry.delete(0, tk.END)
            entry.insert(0, str(int(round(val))) if is_int else f"{val:.2f}")

        def _entry_committed(event):
            try:
                val = int(entry.get()) if is_int else float(entry.get())
                s_min = float(slider.cget("from"))
                s_max = float(slider.cget("to"))
                if s_min <= val <= s_max:
                    self._suppress = True
                    var.set(val)
                    self._suppress = False
            except ValueError:
                pass

        var.trace_add("write", _slider_changed)
        entry.bind("<Return>", _entry_committed)
        entry.bind("<FocusOut>", _entry_committed)

    # ──────────────────────────────────────────────────────────────────────
    # Color picker
    # ──────────────────────────────────────────────────────────────────────

    def _pick_color(self, var, button):
        """Open color chooser dialog and update variable + button."""
        current = var.get()
        result = colorchooser.askcolor(color=current, title="Choose Color")
        if result and result[1]:
            hex_color = result[1].upper()
            var.set(hex_color)
            button.configure(bg=hex_color)

    # ──────────────────────────────────────────────────────────────────────
    # File browsing
    # ──────────────────────────────────────────────────────────────────────

    def _browse_gcode(self):
        path = filedialog.askopenfilename(
            title="Select G-code File",
            filetypes=[("G-code files", "*.gcode *.nc *.ngc *.tap"),
                       ("All files", "*.*")])
        if path:
            self.gcode_var.set(path)

    # ──────────────────────────────────────────────────────────────────────
    # Render
    # ──────────────────────────────────────────────────────────────────────

    def _render(self):
        """Call Gcode_viewer_generator.py with current parameters."""
        gcode = self.gcode_var.get().strip()
        if not gcode:
            messagebox.showerror("Error", "Please select a G-code file.")
            return
        if not os.path.isfile(gcode):
            messagebox.showerror("Error", f"File not found:\n{gcode}")
            return
        if not os.path.isfile(GENERATOR_PATH):
            messagebox.showerror("Error",
                f"Generator not found:\n{GENERATOR_PATH}\n\n"
                "Make sure Gcode_viewer_generator.py is next to this script.")
            return

        # Build command
        cmd = [
            sys.executable, GENERATOR_PATH,
            gcode, PREVIEW_PATH,
            "--line-width",  self._get_entry_or_var(self.line_width_entry, self.line_width_var),
            "--line-color",  self.line_color_var.get(),
            "--line-alpha",  self._get_entry_or_var(self.line_alpha_entry, self.line_alpha_var),
            "--bg-color",    self.bg_color_var.get(),
            "--show-g0",     "true" if self.show_g0_var.get() else "false",
            "--g0-color",    self.g0_color_var.get(),
            "--g0-alpha",    self._get_entry_or_var(self.g0_alpha_entry, self.g0_alpha_var),
            "--g0-width",    self._get_entry_or_var(self.g0_width_entry, self.g0_width_var),
            "--color-mode",  self.color_mode_var.get(),
            "--show-start",  "true" if self.show_start_var.get() else "false",
            "--show-end",    "true" if self.show_end_var.get() else "false",
            "--nth-segment", self._get_entry_or_var(self.nth_entry, self.nth_var),
            "--nth-color",   self.nth_color_var.get(),
            "--dpi",         self._get_entry_or_var(self.dpi_entry, self.dpi_var),
        ]

        # Update UI
        self.render_btn.config(state="disabled", text="Rendering...")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, f"Command:\n{' '.join(cmd[1:])}\n\n")
        self.root.update()

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            self.log_text.insert(tk.END, result.stdout)
            if result.returncode != 0:
                self.log_text.insert(tk.END, "\n--- ERRORS ---\n" + result.stderr)
                messagebox.showerror("Render Failed",
                    "Generator returned an error.\nCheck the log below.")
            else:
                # Parse metadata from output
                self._parse_and_display_metadata(result.stdout)
                # Load and display the preview image
                self._display_preview()
        except subprocess.TimeoutExpired:
            messagebox.showerror("Timeout", "Rendering took too long (>120s). Aborted.")
        except Exception as e:
            self.log_text.insert(tk.END, f"Failed to launch:\n{e}")
            messagebox.showerror("Error", str(e))
        finally:
            self.render_btn.config(state="normal", text="Render Preview")

    def _get_entry_or_var(self, entry, var):
        """Get value from entry widget, fallback to variable."""
        val = entry.get().strip()
        if val:
            return val
        return str(var.get())

    def _parse_and_display_metadata(self, stdout):
        """Extract __METADATA_JSON__ from generator stdout and update info bar."""
        for line in stdout.splitlines():
            if line.startswith("__METADATA_JSON__:"):
                try:
                    meta = json.loads(line.split(":", 1)[1])
                    info = (
                        f"Bounding Box:  X[{meta['x_min']} .. {meta['x_max']}]  "
                        f"Y[{meta['y_min']} .. {meta['y_max']}]   |   "
                        f"Size: {meta['width']} x {meta['height']} mm   |   "
                        f"Segments: {meta['total_segments']} "
                        f"(G1: {meta['g1_count']}, G0: {meta['g0_count']})"
                    )
                    self.info_var.set(info)
                except (json.JSONDecodeError, KeyError):
                    pass
                break

    # ──────────────────────────────────────────────────────────────────────
    # Display preview image — zoom & pan
    # ──────────────────────────────────────────────────────────────────────

    def _display_preview(self):
        """Load the rendered PNG and display it on the canvas."""
        if not os.path.isfile(PREVIEW_PATH):
            return

        try:
            img = Image.open(PREVIEW_PATH)
            self._full_image = img  # keep original for zoom
            # Reset zoom/pan on new render
            self._zoom = 1.0
            self._pan_x = 0.0
            self._pan_y = 0.0
            self._redraw_image()
        except Exception as e:
            self.canvas.delete("all")
            self.canvas.create_text(
                200, 150, text=f"Error loading preview:\n{e}",
                fill=self.FG2, font=("Segoe UI", 10))

    def _redraw_image(self):
        """Redraw the image at the current zoom level and pan offset."""
        if not hasattr(self, "_full_image"):
            return

        img = self._full_image
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            cw, ch = 600, 400

        img_w, img_h = img.size

        # Base scale: fit image to canvas (this is zoom=1.0)
        base_scale = min(cw / img_w, ch / img_h, 1.0)
        # Effective scale with user zoom
        eff_scale = base_scale * self._zoom

        new_w = max(1, int(img_w * eff_scale))
        new_h = max(1, int(img_h * eff_scale))

        resized = img.resize((new_w, new_h), Image.LANCZOS)
        photo = ImageTk.PhotoImage(resized)

        # Position: center + pan offset
        cx = cw / 2 + self._pan_x
        cy = ch / 2 + self._pan_y

        self.canvas.delete("all")
        self.canvas.create_image(int(cx), int(cy), anchor="center", image=photo)
        self.preview_photo = photo  # prevent GC

    def _on_canvas_resize(self, event):
        """Re-draw image when canvas is resized."""
        if hasattr(self, "_full_image"):
            self._redraw_image()

    # ── Zoom ──────────────────────────────────────────────────────────────

    def _on_mousewheel(self, event):
        """Handle mouse wheel zoom (Windows/macOS)."""
        if not hasattr(self, "_full_image"):
            return
        # event.delta is typically 120 or -120 on Windows
        if event.delta > 0:
            factor = 1.15
        else:
            factor = 1 / 1.15
        self._apply_zoom(factor, event.x, event.y)

    def _on_mousewheel_linux_up(self, event):
        """Linux scroll up = zoom in."""
        if not hasattr(self, "_full_image"):
            return
        self._apply_zoom(1.15, event.x, event.y)

    def _on_mousewheel_linux_down(self, event):
        """Linux scroll down = zoom out."""
        if not hasattr(self, "_full_image"):
            return
        self._apply_zoom(1 / 1.15, event.x, event.y)

    def _apply_zoom(self, factor, mouse_x, mouse_y):
        """Apply zoom factor centered on mouse position."""
        old_zoom = self._zoom
        self._zoom *= factor
        # Clamp zoom between 0.1x and 50x
        self._zoom = max(0.1, min(50.0, self._zoom))
        actual_factor = self._zoom / old_zoom

        # Adjust pan so the point under the mouse stays in place
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        # Vector from canvas center to mouse
        dx = mouse_x - cw / 2
        dy = mouse_y - ch / 2
        # Adjust pan: the point under mouse should stay fixed
        self._pan_x = actual_factor * (self._pan_x - dx) + dx
        self._pan_y = actual_factor * (self._pan_y - dy) + dy

        self._redraw_image()

    # ── Pan ───────────────────────────────────────────────────────────────

    def _on_pan_start(self, event):
        """Start panning (left or middle mouse button)."""
        self._drag_start = (event.x, event.y)

    def _on_pan_move(self, event):
        """Pan while dragging."""
        if self._drag_start is None:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        self._drag_start = (event.x, event.y)
        self._pan_x += dx
        self._pan_y += dy
        self._redraw_image()

    def _on_pan_end(self, event):
        """End panning."""
        self._drag_start = None

    # ── Fit to canvas ─────────────────────────────────────────────────────

    def _on_fit_to_canvas(self, event):
        """Double-click to reset zoom/pan (fit to canvas)."""
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._redraw_image()

    # ──────────────────────────────────────────────────────────────────────
    # Save image
    # ──────────────────────────────────────────────────────────────────────

    def _save_image(self):
        """Save the preview PNG to a user-chosen location."""
        if not os.path.isfile(PREVIEW_PATH):
            messagebox.showinfo("No Image", "Render a preview first.")
            return

        path = filedialog.asksaveasfilename(
            title="Save Preview Image",
            defaultextension=".png",
            filetypes=[("PNG files", "*.png"), ("All files", "*.*")])
        if path:
            try:
                import shutil
                shutil.copy2(PREVIEW_PATH, path)
                messagebox.showinfo("Saved", f"Image saved to:\n{path}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save:\n{e}")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    gcode_file = None
    if len(sys.argv) > 1:
        gcode_file = sys.argv[1]

    root = tk.Tk()
    app = GcodeViewerGUI(root, gcode_path=gcode_file)
    root.mainloop()

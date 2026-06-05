"""Standalone Tkinter control-panel GUI.

Launched as a subprocess by ``inputs/control_panel.py`` so the Tk
mainloop can own the macOS main thread (Cocoa requires Tk there) while
the robot-arm asyncio app keeps running in its own process.

Protocol (JSON lines over stdio):
  parent -> child on stdin:    {"type": "status", "lines": ["Serial: ...", "AI: ..."]}
                               {"type": "state",  "base": 90, "lift": 105, ...}
                               {"type": "quit"}
  child  -> parent on stdout:  {"type": "action", "intent": "home", "payload": {}}
                               {"type": "shutdown"}

This script only depends on the Python standard library so it can be
spawned from any venv that has Tkinter (ships with macOS' system
python-tk).
"""

from __future__ import annotations

import json
import math
import queue
import os
import sys
import threading
import time
from dataclasses import dataclass

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


COLORS = {
    "bg":            "#030508",
    "panel":         "#0c0e12",
    "card":          "#0f1117",
    "card_alt":      "#141820",
    "border":        "#2a2f3a",
    "border_soft":   "#1a1e26",
    "text":          "#f4f4f5",
    "muted":         "#8b919e",
    "dim":           "#6b7280",
    "primary":       "#6b9ec8",
    "primary_hover": "#8bb8dc",
    "success":       "#5a9a7a",
    "success_hover": "#6db89a",
    "info":          "#8b9cb8",
    "info_hover":    "#a3b4cc",
    "warning":       "#c9a05c",
    "warning_hover": "#d4b06e",
    "danger":        "#c45c6a",
    "danger_hover":  "#d06878",
    "accent":        "#6b9ec8",
    "track":         "#1a1e26",
    "track_fill":    "#6b9ec8",
    # Per-source tints for the unified activity feed.
    "source_voice":   "#22d3ee",
    "source_gesture": "#a78bfa",
    "source_vision":  "#22c58d",
    "source_panel":   "#60a5fa",
    "source_arm":     "#fbbf24",
    "source_system":  "#8691a8",
    "source_typed":   "#22c58d",
}


def _source_color(source: str) -> str:
    return COLORS.get(f"source_{source}", COLORS["muted"])


# Servo limits used for live progress bars. Kept here so the GUI stays a
# pure-stdlib subprocess (no import from the orchestrator side).
SERVO_LIMITS = {
    "base":   (10, 250),
    "lift":   (15, 225),
    "rotate": (10, 170),
    "claw":   (15, 165),
}


@dataclass(frozen=True)
class PanelButton:
    label: str
    intent: str
    payload: dict
    accent: str = "primary"
    hint: str = ""


BUTTON_GRID = (
    (
        PanelButton("Open Claw", "open_claw", {}, hint="O / voice: open"),
        PanelButton("Close Claw", "close_claw", {}, hint="C / voice: close"),
        PanelButton("Lift ↑", "lift_up", {}, hint="↑ / voice: lift / up"),
        PanelButton("Lift ↓", "lift_down", {}, hint="↓ / voice: lower / down"),
    ),
    (
        PanelButton("Base ←", "base_left", {}, hint="← / voice: left"),
        PanelButton("Base →", "base_right", {}, hint="→ / voice: right"),
        PanelButton("Rotate ↺", "rotate_left", {}, accent="info", hint="voice: rotate left"),
        PanelButton("Rotate ↻", "rotate_right", {}, accent="info", hint="voice: rotate / right"),
    ),
    (
        PanelButton("Home", "home", {}, accent="info", hint="H / voice: home"),
        PanelButton("Inspect", "preset_pose", {"name": "inspect"}, accent="info", hint="voice: inspect"),
        PanelButton("Pick", "pick_object", {"label": "object"}, accent="success", hint="voice: pick up <thing>"),
        PanelButton("Place", "place_object", {"label": "object"}, accent="success", hint="voice: drop / place"),
    ),
)
ACCESSIBILITY_MODE = os.environ.get("ROBOT_ARM_ACCESSIBILITY", "1").strip().lower() not in {
    "0", "false", "no", "off",
}

ADL_BUTTONS = (
    PanelButton("Medication", "pick_object", {"label": "bottle", "adl_id": "medication"}, accent="success", hint="Voice: get my pills"),
    PanelButton("TV Remote", "pick_object", {"label": "remote", "adl_id": "remote"}, accent="info", hint="Voice: get the remote"),
    PanelButton("Water", "pick_object", {"label": "bottle", "adl_id": "water"}, accent="primary", hint="Voice: get my water"),
    PanelButton("Phone", "pick_object", {"label": "cell phone", "adl_id": "phone"}, accent="info", hint="Voice: get my phone"),
    PanelButton("Home", "home", {}, accent="warning", hint="Safe rest position"),
    PanelButton("Help / Stop", "emergency_stop", {}, accent="danger", hint="Voice: stop"),
)
EMERGENCY_BUTTON = PanelButton(
    "EMERGENCY  STOP", "emergency_stop", {}, accent="danger",
    hint="Space / voice: stop / halt",
)
MOTION_INTENTS = {
    "open_claw", "close_claw",
    "lift_up", "lift_down",
    "base_left", "base_right",
    "rotate_left", "rotate_right",
}
SPEED_INTENTS = MOTION_INTENTS | {"preset_pose", "pick_object", "place_object"}


def _emit(message: dict) -> None:
    try:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _stdin_reader(q: "queue.Queue[dict]") -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            q.put(json.loads(line))
        except json.JSONDecodeError:
            continue
    q.put({"type": "quit"})


class ServoBar:
    """A tiny canvas-based horizontal bar with a title, value, and range."""

    def __init__(self, parent: tk.Widget, title: str, *, min_deg: int, max_deg: int) -> None:
        self.min_deg = min_deg
        self.max_deg = max_deg
        self.frame = tk.Frame(parent, bg=COLORS["card"])
        top = tk.Frame(self.frame, bg=COLORS["card"])
        top.pack(fill="x")
        self.title_label = tk.Label(
            top,
            text=title,
            fg=COLORS["text"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 11, "bold"),
            anchor="w",
        )
        self.title_label.pack(side="left")
        self.value_label = tk.Label(
            top,
            text="—",
            fg=COLORS["accent"],
            bg=COLORS["card"],
            font=("Menlo", 11, "bold"),
        )
        self.value_label.pack(side="right")

        self.canvas = tk.Canvas(
            self.frame,
            height=8,
            bg=COLORS["track"],
            bd=0,
            highlightthickness=0,
        )
        self.canvas.pack(fill="x", pady=(6, 2))
        self._fill = self.canvas.create_rectangle(
            0, 0, 0, 0, fill=COLORS["track_fill"], width=0,
        )

        self.range_label = tk.Label(
            self.frame,
            text=f"{min_deg}° — {max_deg}°",
            fg=COLORS["dim"],
            bg=COLORS["card"],
            font=("Menlo", 9),
            anchor="w",
        )
        self.range_label.pack(fill="x")

        self.canvas.bind("<Configure>", self._redraw)
        self._last_value: int | None = None

    def set_value(self, deg: int) -> None:
        self._last_value = int(deg)
        self.value_label.configure(text=f"{self._last_value:>3}°")
        self._redraw()

    def _redraw(self, _event=None) -> None:
        if self._last_value is None:
            return
        w = max(1, int(self.canvas.winfo_width()))
        h = max(1, int(self.canvas.winfo_height()))
        clamped = max(self.min_deg, min(self.max_deg, self._last_value))
        span = max(1, self.max_deg - self.min_deg)
        frac = (clamped - self.min_deg) / span
        self.canvas.coords(self._fill, 0, 0, int(w * frac), h)


class Panel:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Assistive Arm — Control Center")
        self.root.configure(bg=COLORS["bg"])
        self.root.geometry("980x1020")
        self.root.minsize(900, 880)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            tkfont.nametofont("TkDefaultFont").configure(family="Helvetica Neue", size=11)
        except Exception:
            pass

        self.buttons: dict[str, tk.Button] = {}
        self._stdin_q: "queue.Queue[dict]" = queue.Queue()
        threading.Thread(target=_stdin_reader, args=(self._stdin_q,), daemon=True).start()

        self.step_deg_var = tk.IntVar(value=12)
        self.speed_pct_var = tk.IntVar(value=35)
        self.trial_mode_var = tk.StringVar(value="dual_perception")
        self.trial_target_var = tk.StringVar(value="water bottle")
        self.trial_motor_var = tk.StringVar(value="moderate")
        self.trial_tremor_var = tk.BooleanVar(value=False)
        self.trial_distance_var = tk.StringVar(value="")
        self.trial_alignment_var = tk.StringVar(value="")
        self.trial_note_var = tk.StringVar(value="")

        self.servo_bars: dict[str, ServoBar] = {}
        self.serial_badge: tk.Label | None = None
        self.ai_badge: tk.Label | None = None
        self.estop_badge: tk.Label | None = None

        # Voice card state
        self.voice_heard_label: tk.Label | None = None
        self.voice_partial_label: tk.Label | None = None
        self.voice_intent_label: tk.Label | None = None
        self.voice_entry: tk.Entry | None = None
        self.voice_history: tk.Text | None = None

        # Unified activity + telemetry state.
        self.health_chips: dict[str, tk.Label] = {}
        self.activity_text: tk.Text | None = None
        self.pending_frame: tk.Frame | None = None
        self.pending_label: tk.Label | None = None
        self.pending_countdown_label: tk.Label | None = None
        self._last_activity_ids: set[int] = set()
        self._last_serial_ids: set[int] = set()
        self._pending_active: bool = False
        self.range_label: tk.Label | None = None
        self.feedback_label: tk.Label | None = None
        self.lidar_canvas: tk.Canvas | None = None
        self.lidar_summary_label: tk.Label | None = None
        self.lidar_detail_label: tk.Label | None = None
        self.serial_monitor_text: tk.Text | None = None
        self.lab_active_label: tk.Label | None = None
        self.lab_recent_text: tk.Text | None = None
        self.lab_log_path_label: tk.Label | None = None
        self._lidar_samples: dict[int, dict] = {}

        self._build_ui()
        self.root.after(80, self._pump_stdin)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self._build_header()
        self._configure_ttk_styles()
        body = tk.Frame(self.root, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=(4, 10))

        self._build_pending_banner(body)
        nav = ttk.Notebook(body, style="Panel.TNotebook")
        nav.pack(fill="both", expand=True, pady=(8, 0))

        control_tab = tk.Frame(nav, bg=COLORS["bg"])
        experiment_tab = tk.Frame(nav, bg=COLORS["bg"])
        diagnostics_tab = tk.Frame(nav, bg=COLORS["bg"])
        nav.add(control_tab, text="Control")
        nav.add(experiment_tab, text="Experiment")
        nav.add(diagnostics_tab, text="Diagnostics")

        self._build_control_card(control_tab)
        self._build_voice_card(control_tab)
        self._build_activity_card(control_tab)
        self._build_trial_card(experiment_tab)
        self._build_error_controls_card(experiment_tab)
        self._build_state_card(diagnostics_tab)
        self._build_footer()
        self._bind_shortcuts()

        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(500, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    def _configure_ttk_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Panel.TNotebook",
            background=COLORS["card"],
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "Panel.TNotebook.Tab",
            background=COLORS["card_alt"],
            foreground=COLORS["muted"],
            padding=(14, 8),
            borderwidth=0,
        )
        style.map(
            "Panel.TNotebook.Tab",
            background=[("selected", COLORS["panel"])],
            foreground=[("selected", COLORS["text"])],
        )

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=COLORS["panel"], height=108)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        row = tk.Frame(header, bg=COLORS["panel"])
        row.pack(fill="x", padx=22, pady=(14, 0))

        title_wrap = tk.Frame(row, bg=COLORS["panel"])
        title_wrap.pack(side="left")
        tk.Label(
            title_wrap,
            text="Assistive Arm",
            fg=COLORS["text"],
            bg=COLORS["panel"],
            font=("Helvetica Neue", 19, "bold"),
        ).pack(side="left")
        tk.Label(
            title_wrap,
            text="  ·  Control Center",
            fg=COLORS["muted"],
            bg=COLORS["panel"],
            font=("Helvetica Neue", 13),
        ).pack(side="left")
        tk.Label(
            header,
            text="Legacy desktop panel — for the modern UI run: python main.py --web",
            fg=COLORS["accent"],
            bg=COLORS["panel"],
            font=("Helvetica Neue", 11),
        ).pack(anchor="w", padx=22, pady=(4, 0))

        self.estop_badge = self._make_badge(row, "E-STOP off", "success")
        self.estop_badge.pack(side="right")

        # Traffic-light chips: one per subsystem. Colour-coded health
        # gives the user an at-a-glance feel for what's alive.
        chip_row = tk.Frame(header, bg=COLORS["panel"])
        chip_row.pack(fill="x", padx=22, pady=(10, 0))
        for key, label in (
            ("serial", "Serial"),
            ("ai", "AI"),
            ("camera", "Camera"),
            ("voice", "Mic"),
            ("vision", "Vision"),
            ("gesture", "Gesture"),
        ):
            chip = self._make_chip(chip_row, label)
            chip.pack(side="left", padx=(0, 8))
            self.health_chips[key] = chip

        # Thin divider
        tk.Frame(self.root, bg=COLORS["border_soft"], height=1).pack(fill="x")

        # Compatibility: keep the old badge attributes alive but point
        # them at our first two chips so _apply_status still works.
        self.serial_badge = self.health_chips.get("serial")
        self.ai_badge = self.health_chips.get("ai")

    def _make_chip(self, parent: tk.Widget, label: str) -> tk.Label:
        chip = tk.Label(
            parent,
            text=f"●  {label}",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Menlo", 10, "bold"),
            padx=10,
            pady=4,
        )
        chip.configure(highlightbackground=COLORS["border"], highlightthickness=1, bd=0)
        return chip

    def _set_chip(self, key: str, state: str, detail: str = "") -> None:
        """state: 'ok' (green) / 'warn' (amber) / 'bad' (red) / 'idle' (gray)."""
        chip = self.health_chips.get(key)
        if chip is None:
            return
        color = {
            "ok": COLORS["success"],
            "warn": COLORS["warning"],
            "bad": COLORS["danger"],
            "idle": COLORS["muted"],
        }.get(state, COLORS["muted"])
        label = key.capitalize() if key != "voice" else "Mic"
        text = f"●  {label}"
        if detail:
            text = f"{text}  {detail}"
        chip.configure(text=text, fg=color)

    def _make_badge(self, parent: tk.Widget, text: str, accent: str) -> tk.Label:
        lbl = tk.Label(
            parent,
            text=text,
            fg="white",
            bg=COLORS[accent],
            font=("Menlo", 10, "bold"),
            padx=10,
            pady=3,
        )
        return lbl

    def _set_badge(self, badge: tk.Label | None, text: str, accent: str) -> None:
        if badge is None:
            return
        badge.configure(text=text, bg=COLORS[accent])

    def _build_control_card(self, parent: tk.Widget) -> None:
        if ACCESSIBILITY_MODE:
            self._build_adl_card(parent)

        card = tk.Frame(
            parent, bg=COLORS["card"], highlightbackground=COLORS["border"], highlightthickness=1,
        )
        card.pack(fill="x", pady=(12, 10))

        tk.Label(
            card,
            text="Manual Control",
            fg=COLORS["text"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 12, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(
            card,
            text="Click, use keyboard shortcuts, or speak a command. Fine/coarse step and speed apply to movement buttons.",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 8))

        precision = tk.Frame(card, bg=COLORS["card"])
        precision.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(
            precision,
            text="Step",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10, "bold"),
        ).pack(side="left", padx=(0, 8))
        for label, value in (("Fine 4°", 4), ("Normal 12°", 12), ("Coarse 24°", 24)):
            tk.Radiobutton(
                precision,
                text=label,
                variable=self.step_deg_var,
                value=value,
                fg=COLORS["text"],
                bg=COLORS["card"],
                selectcolor=COLORS["card_alt"],
                activebackground=COLORS["card"],
                activeforeground=COLORS["text"],
                font=("Helvetica Neue", 10),
            ).pack(side="left", padx=(0, 8))

        tk.Label(
            precision,
            text="Speed",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10, "bold"),
        ).pack(side="left", padx=(18, 8))
        speed = tk.Scale(
            precision,
            from_=15,
            to=80,
            orient="horizontal",
            variable=self.speed_pct_var,
            showvalue=True,
            bg=COLORS["card"],
            fg=COLORS["text"],
            troughcolor=COLORS["track"],
            highlightthickness=0,
            length=180,
            resolution=5,
        )
        speed.pack(side="left")

        grid_frame = tk.Frame(card, bg=COLORS["card"])
        grid_frame.pack(fill="x", padx=12, pady=(0, 12))
        for r, row in enumerate(BUTTON_GRID):
            for c, btn in enumerate(row):
                widget = self._make_button(grid_frame, btn)
                widget.grid(row=r, column=c, sticky="nsew", padx=6, pady=6, ipady=10)
                self.buttons[btn.label] = widget
            for c in range(len(row)):
                grid_frame.grid_columnconfigure(c, weight=1)

        stop = self._make_button(card, EMERGENCY_BUTTON, big=True)
        stop.pack(fill="x", pady=(2, 14), padx=12, ipady=18 if ACCESSIBILITY_MODE else 18)

    def _build_adl_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent, bg=COLORS["card"], highlightbackground=COLORS["border"], highlightthickness=1,
        )
        card.pack(fill="x", pady=(12, 8))
        tk.Label(
            card,
            text="Daily Tasks — tap what you need",
            fg=COLORS["text"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 16, "bold") if ACCESSIBILITY_MODE else ("Helvetica Neue", 12, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(
            card,
            text="No steady hands required — the arm moves to the object for you.",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 12),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 10))
        grid = tk.Frame(card, bg=COLORS["card"])
        grid.pack(fill="x", padx=12, pady=(0, 14))
        for idx, btn in enumerate(ADL_BUTTONS):
            widget = self._make_button(grid, btn, large=ACCESSIBILITY_MODE)
            row, col = divmod(idx, 2)
            widget.grid(row=row, column=col, sticky="nsew", padx=8, pady=8, ipady=14 if ACCESSIBILITY_MODE else 10)
            self.buttons[btn.label] = widget
        for c in range(2):
            grid.grid_columnconfigure(c, weight=1)

    def _build_voice_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent, bg=COLORS["card"], highlightbackground=COLORS["border"], highlightthickness=1,
        )
        card.pack(fill="x", pady=(8, 10))

        tk.Label(
            card,
            text="Voice & Typed Commands",
            fg=COLORS["text"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 12, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(
            card,
            text="What the computer thinks you said, and a box to type commands if it's wrong.",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 8))

        # --- "Heard" line + partial ---
        heard_row = tk.Frame(card, bg=COLORS["card"])
        heard_row.pack(fill="x", padx=16, pady=(0, 2))
        tk.Label(
            heard_row,
            text="Heard:",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10, "bold"),
            width=8,
            anchor="w",
        ).pack(side="left")
        self.voice_heard_label = tk.Label(
            heard_row,
            text="— (say something, or type below)",
            fg=COLORS["accent"],
            bg=COLORS["card"],
            font=("Menlo", 12, "bold"),
            anchor="w",
        )
        self.voice_heard_label.pack(side="left", fill="x", expand=True)

        partial_row = tk.Frame(card, bg=COLORS["card"])
        partial_row.pack(fill="x", padx=16, pady=(0, 2))
        tk.Label(
            partial_row,
            text="Partial:",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10),
            width=8,
            anchor="w",
        ).pack(side="left")
        self.voice_partial_label = tk.Label(
            partial_row,
            text="",
            fg=COLORS["dim"],
            bg=COLORS["card"],
            font=("Menlo", 11, "italic"),
            anchor="w",
        )
        self.voice_partial_label.pack(side="left", fill="x", expand=True)

        intent_row = tk.Frame(card, bg=COLORS["card"])
        intent_row.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(
            intent_row,
            text="Intent:",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10, "bold"),
            width=8,
            anchor="w",
        ).pack(side="left")
        self.voice_intent_label = tk.Label(
            intent_row,
            text="—",
            fg=COLORS["info_hover"],
            bg=COLORS["card"],
            font=("Menlo", 11, "bold"),
            anchor="w",
        )
        self.voice_intent_label.pack(side="left", fill="x", expand=True)

        # --- Type-a-command row ---
        type_row = tk.Frame(card, bg=COLORS["card"])
        type_row.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(
            type_row,
            text="Type:",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10, "bold"),
            width=8,
            anchor="w",
        ).pack(side="left")
        self.voice_entry = tk.Entry(
            type_row,
            bg=COLORS["card_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            font=("Menlo", 12),
            disabledbackground=COLORS["card_alt"],
        )
        self.voice_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.voice_entry.bind("<Return>", lambda _e: self._send_typed_command())
        self.voice_entry.bind("<Key>", self._on_voice_entry_key)

        send_btn = tk.Button(
            type_row,
            text="Send",
            fg="white",
            bg=COLORS["primary"],
            activebackground=COLORS["primary_hover"],
            activeforeground="white",
            font=("Helvetica Neue", 11, "bold"),
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=16,
            pady=6,
            command=self._send_typed_command,
        )
        send_btn.pack(side="left")

        # Recent commands are now shown in the unified Activity feed
        # at the bottom of the window — one scrolling log for voice,
        # gesture, vision, panel and arm events. Keeping the history
        # widget but hiding it keeps _apply_voice's tail append working
        # without cluttering the UI.
        hist_wrap = tk.Frame(card, bg=COLORS["card"])
        self.voice_history = tk.Text(
            hist_wrap,
            height=1,
            bg=COLORS["card_alt"],
            fg=COLORS["text"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Menlo", 10),
            wrap="word",
            state="disabled",
        )
        # hist_wrap is intentionally not .pack()'d so the hidden widget
        # stays addressable without taking screen space.

    def _on_voice_entry_key(self, event: tk.Event) -> str | None:
        # Prevent the single-letter shortcuts (h / o / c / space) from
        # firing while the user is typing in the text entry.
        if event.keysym == "Escape":
            self.root.focus()
            return "break"
        return None

    def _build_state_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent, bg=COLORS["card"], highlightbackground=COLORS["border"], highlightthickness=1,
        )
        card.pack(fill="both", expand=True)

        head = tk.Frame(card, bg=COLORS["card"])
        head.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(
            head,
            text="Arm State",
            fg=COLORS["text"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 12, "bold"),
            anchor="w",
        ).pack(side="left")
        self.range_label = tk.Label(
            head,
            text="range: —",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Menlo", 10),
        )
        self.range_label.pack(side="right")
        notebook = ttk.Notebook(card, style="Panel.TNotebook")
        notebook.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        overview = tk.Frame(notebook, bg=COLORS["card"])
        notebook.add(overview, text="Overview")

        bars = tk.Frame(overview, bg=COLORS["card"])
        bars.pack(fill="x", padx=8, pady=(8, 8))
        for idx, joint in enumerate(("base", "lift", "rotate", "claw")):
            lo, hi = SERVO_LIMITS[joint]
            bar = ServoBar(bars, joint.capitalize(), min_deg=lo, max_deg=hi)
            bar.frame.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=8, pady=8)
            self.servo_bars[joint] = bar
        for c in range(2):
            bars.grid_columnconfigure(c, weight=1, uniform="bars")

        self.feedback_label = tk.Label(
            overview,
            text="Waiting for arm state…",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 11),
            anchor="w",
        )
        self.feedback_label.pack(fill="x", padx=16, pady=(4, 12))

        lidar_tab = tk.Frame(notebook, bg=COLORS["card"])
        notebook.add(lidar_tab, text="LiDAR")
        self.lidar_summary_label = tk.Label(
            lidar_tab,
            text="No LiDAR samples yet.",
            fg=COLORS["text"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 11, "bold"),
            anchor="w",
        )
        self.lidar_summary_label.pack(fill="x", padx=16, pady=(12, 2))
        self.lidar_detail_label = tk.Label(
            lidar_tab,
            text="Once the base sweeps, returns will appear here as a radial map.",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10),
            anchor="w",
        )
        self.lidar_detail_label.pack(fill="x", padx=16, pady=(0, 8))
        self.lidar_canvas = tk.Canvas(
            lidar_tab,
            bg=COLORS["card_alt"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            bd=0,
            height=260,
        )
        self.lidar_canvas.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.lidar_canvas.bind("<Configure>", lambda _e: self._redraw_lidar())

        serial_tab = tk.Frame(notebook, bg=COLORS["card"])
        notebook.add(serial_tab, text="Arduino Serial")
        tk.Label(
            serial_tab,
            text="Live monitor of host packets, firmware ACKs, and MCU debug messages.",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(12, 8))
        serial_text = tk.Text(
            serial_tab,
            height=12,
            bg=COLORS["card_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            bd=0,
            wrap="none",
            font=("Menlo", 10),
            state="disabled",
            padx=10,
            pady=8,
        )
        serial_text.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        serial_text.tag_configure("serial_time", foreground=COLORS["dim"])
        serial_text.tag_configure("serial_channel", foreground=COLORS["accent"])
        serial_text.tag_configure("serial_ok", foreground=COLORS["success"])
        serial_text.tag_configure("serial_warn", foreground=COLORS["warning"])
        serial_text.tag_configure("serial_bad", foreground=COLORS["danger"])
        self.serial_monitor_text = serial_text

        # Make LiDAR the default selected tab on startup
        notebook.select(lidar_tab)

    def _build_pending_banner(self, parent: tk.Widget) -> None:
        """Slim banner that only shows up while the orchestrator is
        waiting for a yes/no confirmation. Clicking Yes/No routes the
        same intents as the voice `yes`/`no` commands."""
        # Outer wrapper always stays packed so the inner banner can
        # pack/unpack into a fixed top slot.
        wrapper = tk.Frame(parent, bg=COLORS["bg"])
        wrapper.pack(fill="x")
        frame = tk.Frame(
            wrapper,
            bg=COLORS["warning"],
            highlightbackground=COLORS["warning_hover"],
            highlightthickness=1,
        )
        self.pending_frame = frame

        row = tk.Frame(frame, bg=COLORS["warning"])
        row.pack(fill="x", padx=14, pady=10)

        tk.Label(
            row,
            text="⚠",
            fg="#111",
            bg=COLORS["warning"],
            font=("Helvetica Neue", 16, "bold"),
        ).pack(side="left", padx=(0, 10))

        self.pending_label = tk.Label(
            row,
            text="Awaiting confirmation",
            fg="#111",
            bg=COLORS["warning"],
            font=("Helvetica Neue", 13, "bold"),
            anchor="w",
        )
        self.pending_label.pack(side="left", fill="x", expand=True)

        self.pending_countdown_label = tk.Label(
            row,
            text="",
            fg="#111",
            bg=COLORS["warning"],
            font=("Menlo", 11, "bold"),
        )
        self.pending_countdown_label.pack(side="left", padx=(8, 10))

        def _yes():
            _emit({"type": "action", "intent": "confirm_yes", "payload": {}})

        def _no():
            _emit({"type": "action", "intent": "confirm_no", "payload": {}})

        tk.Button(
            row, text="Yes", command=_yes,
            bg=COLORS["success"], fg="white", activebackground=COLORS["success_hover"],
            activeforeground="white", relief="flat", bd=0, padx=16, pady=6,
            font=("Helvetica Neue", 12, "bold"), cursor="hand2",
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            row, text="No", command=_no,
            bg=COLORS["danger"], fg="white", activebackground=COLORS["danger_hover"],
            activeforeground="white", relief="flat", bd=0, padx=16, pady=6,
            font=("Helvetica Neue", 12, "bold"), cursor="hand2",
        ).pack(side="left")

    def _build_activity_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent, bg=COLORS["card"], highlightbackground=COLORS["border"], highlightthickness=1,
        )
        card.pack(fill="both", expand=True, pady=(10, 0))

        head = tk.Frame(card, bg=COLORS["card"])
        head.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(
            head,
            text="Activity",
            fg=COLORS["text"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            head,
            text="voice · gesture · vision · panel · arm",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10),
        ).pack(side="left", padx=(10, 0))

        text = tk.Text(
            card,
            height=9,
            bg=COLORS["card_alt"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            bd=0,
            wrap="word",
            font=("Menlo", 10),
            state="disabled",
            padx=10,
            pady=8,
            spacing1=1,
            spacing3=3,
        )
        text.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        # Register per-source tags once so updates are O(1).
        for src in ("voice", "gesture", "vision", "panel", "arm", "system", "typed"):
            text.tag_configure(f"src_{src}", foreground=_source_color(src))
        text.tag_configure("time", foreground=COLORS["dim"])
        text.tag_configure("kind", foreground=COLORS["muted"])

        self.activity_text = text

    def _build_trial_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent, bg=COLORS["card"], highlightbackground=COLORS["border"], highlightthickness=1,
        )
        card.pack(fill="both", expand=True, pady=(12, 10))

        tk.Label(
            card,
            text="Experiment Trials",
            fg=COLORS["text"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 12, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(
            card,
            text="Arthritis accessibility trials: 20+ runs per condition recommended. Log timing, corrections, 3D vision, tremor simulation.",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 10),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 10))

        form = tk.Frame(card, bg=COLORS["card"])
        form.pack(fill="x", padx=16, pady=(0, 10))

        tk.Label(form, text="Mode", fg=COLORS["muted"], bg=COLORS["card"], font=("Helvetica Neue", 10, "bold")).grid(row=0, column=0, sticky="w")
        mode_menu = tk.OptionMenu(
            form,
            self.trial_mode_var,
            "manual",
            "adaptive",
            "dual_perception",
            "voice",
            "gesture",
            "vision",
            "panel",
            "panel_adl",
        )
        mode_menu.configure(bg=COLORS["card_alt"], fg=COLORS["text"], activebackground=COLORS["primary"], activeforeground="white", relief="flat", bd=0)
        mode_menu.grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(2, 8))

        tk.Label(form, text="Target object", fg=COLORS["muted"], bg=COLORS["card"], font=("Helvetica Neue", 10, "bold")).grid(row=0, column=1, sticky="w")
        target_menu = tk.OptionMenu(
            form,
            self.trial_target_var,
            "water bottle",
            "tv remote",
            "medication bottle",
            "cup",
            "taped target",
            "custom",
        )
        target_menu.configure(bg=COLORS["card_alt"], fg=COLORS["text"], activebackground=COLORS["primary"], activeforeground="white", relief="flat", bd=0)
        target_menu.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(2, 8))

        tk.Label(form, text="Motor profile", fg=COLORS["muted"], bg=COLORS["card"], font=("Helvetica Neue", 10, "bold")).grid(row=0, column=2, sticky="w")
        motor_menu = tk.OptionMenu(form, self.trial_motor_var, "early", "moderate", "severe")
        motor_menu.configure(bg=COLORS["card_alt"], fg=COLORS["text"], activebackground=COLORS["primary"], activeforeground="white", relief="flat", bd=0)
        motor_menu.grid(row=1, column=2, sticky="ew", padx=(0, 10), pady=(2, 8))

        tk.Checkbutton(
            form,
            text="Tremor simulated (manual trials)",
            variable=self.trial_tremor_var,
            fg=COLORS["text"],
            bg=COLORS["card"],
            selectcolor=COLORS["card_alt"],
            activebackground=COLORS["card"],
            font=("Helvetica Neue", 10),
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 6))

        tk.Label(form, text="Final distance (cm)", fg=COLORS["muted"], bg=COLORS["card"], font=("Helvetica Neue", 10, "bold")).grid(row=3, column=0, sticky="w")
        distance = tk.Entry(form, textvariable=self.trial_distance_var, bg=COLORS["card_alt"], fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat", font=("Menlo", 11), width=14)
        distance.grid(row=4, column=0, sticky="ew", padx=(0, 10), pady=(2, 8), ipady=5)

        tk.Label(form, text="Alignment error (mm)", fg=COLORS["muted"], bg=COLORS["card"], font=("Helvetica Neue", 10, "bold")).grid(row=3, column=1, sticky="w")
        alignment = tk.Entry(form, textvariable=self.trial_alignment_var, bg=COLORS["card_alt"], fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat", font=("Menlo", 11), width=14)
        alignment.grid(row=4, column=1, sticky="ew", padx=(0, 10), pady=(2, 8), ipady=5)

        tk.Label(form, text="Note", fg=COLORS["muted"], bg=COLORS["card"], font=("Helvetica Neue", 10, "bold")).grid(row=3, column=2, sticky="w")
        note = tk.Entry(form, textvariable=self.trial_note_var, bg=COLORS["card_alt"], fg=COLORS["text"], insertbackground=COLORS["text"], relief="flat", font=("Menlo", 11))
        note.grid(row=4, column=2, sticky="ew", padx=(0, 10), pady=(2, 8), ipady=5)
        for c in range(3):
            form.grid_columnconfigure(c, weight=1)

        actions = tk.Frame(card, bg=COLORS["card"])
        actions.pack(fill="x", padx=16, pady=(0, 12))
        for label, command, accent in (
            ("Start Trial", self._send_trial_start, "primary"),
            ("+ Correction", self._send_trial_correction, "info"),
            ("Mark Success", self._send_trial_success, "success"),
            ("Mark Failure", self._send_trial_failure, "danger"),
            ("Add Note", self._send_trial_note, "info"),
        ):
            tk.Button(
                actions,
                text=label,
                command=command,
                fg="white",
                bg=COLORS[accent],
                activebackground=COLORS[f"{accent}_hover"],
                activeforeground="white",
                font=("Helvetica Neue", 11, "bold"),
                relief="flat",
                bd=0,
                cursor="hand2",
                padx=14,
                pady=8,
            ).pack(side="left", padx=(0, 8))

        self.lab_active_label = tk.Label(
            card,
            text="No active trial.",
            fg=COLORS["muted"],
            bg=COLORS["card"],
            font=("Menlo", 11),
            anchor="w",
        )
        self.lab_active_label.pack(fill="x", padx=16, pady=(0, 8))

        self.lab_log_path_label = tk.Label(
            card,
            text="Log path: waiting for app telemetry…",
            fg=COLORS["dim"],
            bg=COLORS["card"],
            font=("Menlo", 9),
            anchor="w",
        )
        self.lab_log_path_label.pack(fill="x", padx=16, pady=(0, 8))

        recent = tk.Text(
            card,
            height=8,
            bg=COLORS["card_alt"],
            fg=COLORS["text"],
            relief="flat",
            bd=0,
            wrap="none",
            font=("Menlo", 10),
            state="disabled",
            padx=10,
            pady=8,
        )
        recent.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        recent.tag_configure("ok", foreground=COLORS["success"])
        recent.tag_configure("bad", foreground=COLORS["danger"])
        recent.tag_configure("muted", foreground=COLORS["muted"])
        self.lab_recent_text = recent

    def _build_error_controls_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent, bg=COLORS["card"], highlightbackground=COLORS["border"], highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 10))
        tk.Label(
            card,
            text="Error Controls",
            fg=COLORS["text"],
            bg=COLORS["card"],
            font=("Helvetica Neue", 12, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 8))

        grid = tk.Frame(card, bg=COLORS["card"])
        grid.pack(fill="x", padx=16, pady=(0, 14))
        checks = (
            "Target mark reset",
            "Arm homed before trial",
            "Lighting/background checked",
            "Camera and sensor visible",
            "Same speed/step settings",
            "Final distance measured",
        )
        self.error_check_vars = []
        for idx, label in enumerate(checks):
            var = tk.BooleanVar(value=False)
            self.error_check_vars.append(var)
            tk.Checkbutton(
                grid,
                text=label,
                variable=var,
                fg=COLORS["text"],
                bg=COLORS["card"],
                selectcolor=COLORS["card_alt"],
                activebackground=COLORS["card"],
                activeforeground=COLORS["text"],
                font=("Helvetica Neue", 10),
                anchor="w",
            ).grid(row=idx // 2, column=idx % 2, sticky="ew", padx=(0, 12), pady=3)
        for c in range(2):
            grid.grid_columnconfigure(c, weight=1)

    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=COLORS["bg"])
        footer.pack(fill="x", side="bottom")
        tk.Label(
            footer,
            text="Shortcuts:  Esc / ⌘Q quit  ·  Space STOP  ·  H home  ·  O/C claw  ·  ←→ base  ·  ↑↓ lift  ·  type + Enter to send",
            fg=COLORS["muted"],
            bg=COLORS["bg"],
            font=("Helvetica Neue", 10),
        ).pack(side="left", padx=20, pady=8)

    def _bind_shortcuts(self) -> None:
        def guarded(fn):
            """Skip the shortcut while the text entry has focus so
            typed commands don't get intercepted."""
            def _inner(event):
                if self.voice_entry is not None and self.root.focus_get() is self.voice_entry:
                    return None
                return fn(event)
            return _inner

        self.root.bind("<Escape>", lambda _e: self._on_close())
        self.root.bind("<Command-q>", lambda _e: self._on_close())
        self.root.bind("<space>", guarded(lambda _e: self._send(EMERGENCY_BUTTON)))
        for key, intent in (
            ("h", "home"), ("H", "home"),
            ("o", "open_claw"), ("O", "open_claw"),
            ("c", "close_claw"), ("C", "close_claw"),
        ):
            self.root.bind(f"<{key}>", guarded(lambda _e, i=intent: self._send_by_intent(i)))
        self.root.bind("<Left>", guarded(lambda _e: self._send_by_intent("base_left")))
        self.root.bind("<Right>", guarded(lambda _e: self._send_by_intent("base_right")))
        self.root.bind("<Up>", guarded(lambda _e: self._send_by_intent("lift_up")))
        self.root.bind("<Down>", guarded(lambda _e: self._send_by_intent("lift_down")))

    def _make_button(self, parent: tk.Widget, btn: PanelButton, *, big: bool = False, large: bool = False) -> tk.Widget:
        """Frame+Label buttons — macOS Tk ignores bg on native tk.Button."""
        bg = COLORS[btn.accent]
        hover = COLORS[f"{btn.accent}_hover"]
        if big:
            font = ("Helvetica Neue", 17, "bold")
            pad_y = 18
        elif large:
            font = ("Helvetica Neue", 14, "bold")
            pad_y = 14
        else:
            font = ("Helvetica Neue", 12, "bold")
            pad_y = 10
        fg = "#030508" if btn.accent in {"primary", "success", "warning"} else "#f4f4f5"

        shell = tk.Frame(
            parent,
            bg=bg,
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            cursor="hand2",
        )
        label = tk.Label(
            shell,
            text=btn.label,
            fg=fg,
            bg=bg,
            font=font,
            padx=16 if large else 12,
            pady=pad_y,
            cursor="hand2",
        )
        label.pack(fill="both", expand=True)

        def activate(_event=None, b=btn) -> None:
            self._send(b)

        for w in (shell, label):
            w.bind("<Button-1>", activate)
            w.bind("<Enter>", lambda _e, s=shell, lb=label, c=hover, h=btn.hint: self._on_hover(s, lb, c, h))
            w.bind("<Leave>", lambda _e, s=shell, lb=label, c=bg: self._on_unhover(s, lb, c))
        return shell

    def _on_hover(self, shell: tk.Widget, label: tk.Label, color: str, hint: str) -> None:
        shell.configure(bg=color)
        label.configure(bg=color)
        if hint and self.feedback_label is not None:
            self.feedback_label.configure(text=hint, fg=COLORS["muted"])

    def _on_unhover(self, shell: tk.Widget, label: tk.Label, color: str) -> None:
        shell.configure(bg=color)
        label.configure(bg=color)

    def _restore_button_bg(self, shell: tk.Widget, color: str) -> None:
        shell.configure(bg=color)
        for child in shell.winfo_children():
            if isinstance(child, tk.Label):
                child.configure(bg=color)

    # ---------- actions ----------

    def _send(self, btn: PanelButton) -> None:
        payload = self._payload_for_button(btn)
        _emit({"type": "action", "intent": btn.intent, "payload": payload})
        if self.feedback_label is not None:
            step = payload.get("step_deg")
            speed = payload.get("speed_pct")
            suffix = ""
            if step is not None:
                suffix += f"  ·  step={step}°"
            if speed is not None:
                suffix += f"  ·  speed={speed}%"
            self.feedback_label.configure(
                text=f"Queued: {btn.label}  ·  intent={btn.intent}{suffix}",
                fg=COLORS["accent"],
            )
        widget = self.buttons.get(btn.label)
        if widget is not None:
            original = widget.cget("bg")
            for child in widget.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=COLORS["accent"])
            widget.configure(bg=COLORS["accent"])
            widget.after(180, lambda w=widget, c=original: self._restore_button_bg(w, c))

    def _payload_for_button(self, btn: PanelButton) -> dict:
        payload = dict(btn.payload)
        if btn.intent in MOTION_INTENTS:
            payload["step_deg"] = int(self.step_deg_var.get())
        if btn.intent in SPEED_INTENTS:
            payload["speed_pct"] = int(self.speed_pct_var.get())
        return payload

    def _send_typed_command(self) -> None:
        if self.voice_entry is None:
            return
        text = self.voice_entry.get().strip()
        if not text:
            return
        # Emit a spoken_text action so the orchestrator routes it
        # through the same LLM/heuristic pipeline as voice input.
        _emit({
            "type": "action",
            "intent": "spoken_text",
            "payload": {"text": text},
        })
        self.voice_entry.delete(0, "end")
        self._append_voice_history(f"> {text}")
        if self.voice_heard_label is not None:
            self.voice_heard_label.configure(
                text=f"(typed) {text}", fg=COLORS["success"],
            )
        if self.voice_intent_label is not None:
            self.voice_intent_label.configure(
                text="interpreting…", fg=COLORS["warning_hover"],
            )

    def _append_voice_history(self, line: str) -> None:
        if self.voice_history is None:
            return
        self.voice_history.configure(state="normal")
        self.voice_history.insert("end", line + "\n")
        # Keep only the last ~12 lines so the box doesn't grow forever.
        lines = int(self.voice_history.index("end-1c").split(".")[0])
        if lines > 12:
            self.voice_history.delete("1.0", f"{lines - 12}.0")
        self.voice_history.see("end")
        self.voice_history.configure(state="disabled")

    def _apply_voice(self, msg: dict) -> None:
        heard = str(msg.get("heard") or "").strip()
        partial = str(msg.get("partial") or "").strip()
        source = str(msg.get("source") or "")
        intent = str(msg.get("intent") or "").strip()
        payload = msg.get("payload") or {}
        status = str(msg.get("status") or "")

        if self.voice_heard_label is not None:
            if heard:
                prefix = "(typed) " if source == "typed" else ""
                self.voice_heard_label.configure(
                    text=f"{prefix}{heard}", fg=COLORS["accent"],
                )
            else:
                self.voice_heard_label.configure(
                    text="— (say something, or type below)", fg=COLORS["muted"],
                )

        if self.voice_partial_label is not None:
            self.voice_partial_label.configure(
                text=partial if partial else "",
            )

        if self.voice_intent_label is not None:
            if intent:
                pl = f"  ·  {payload}" if payload else ""
                self.voice_intent_label.configure(
                    text=f"{intent}{pl}  ({status or 'resolved'})",
                    fg=COLORS["success_hover"] if status == "resolved" or status == "matched" else COLORS["warning_hover"],
                )
            elif status:
                self.voice_intent_label.configure(
                    text=f"({status})", fg=COLORS["warning_hover"],
                )
            else:
                self.voice_intent_label.configure(
                    text="—", fg=COLORS["muted"],
                )

        # Append a line to history once per distinct "heard" event.
        if heard and heard != getattr(self, "_last_history_heard", ""):
            tag = "voice" if source == "voice" else "typed"
            self._last_history_heard = heard
            self._append_voice_history(f"[{tag}] {heard}")

    def _send_by_intent(self, intent: str) -> None:
        for row in BUTTON_GRID:
            for btn in row:
                if btn.intent == intent:
                    self._send(btn)
                    return
        if intent == EMERGENCY_BUTTON.intent:
            self._send(EMERGENCY_BUTTON)

    def _trial_payload(self) -> dict:
        return {
            "mode": self.trial_mode_var.get().strip(),
            "target": self.trial_target_var.get().strip(),
            "motor_level": self.trial_motor_var.get().strip(),
            "tremor_simulated": bool(self.trial_tremor_var.get()),
            "final_distance_cm": self.trial_distance_var.get().strip(),
            "alignment_error_mm": self.trial_alignment_var.get().strip(),
            "note": self.trial_note_var.get().strip(),
        }

    def _send_trial_start(self) -> None:
        _emit({"type": "action", "intent": "trial_start", "payload": self._trial_payload()})
        if self.lab_active_label is not None:
            self.lab_active_label.configure(text="Starting trial…", fg=COLORS["accent"])

    def _send_trial_correction(self) -> None:
        _emit({"type": "action", "intent": "trial_correction", "payload": self._trial_payload()})

    def _send_trial_note(self) -> None:
        _emit({"type": "action", "intent": "trial_note", "payload": self._trial_payload()})
        self.trial_note_var.set("")

    def _send_trial_success(self) -> None:
        _emit({"type": "action", "intent": "trial_success", "payload": self._trial_payload()})
        self.trial_note_var.set("")

    def _send_trial_failure(self) -> None:
        _emit({"type": "action", "intent": "trial_failure", "payload": self._trial_payload()})
        self.trial_note_var.set("")

    def _on_close(self) -> None:
        _emit({"type": "shutdown"})
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---------- stdin pump ----------

    def _apply_status(self, lines: list[str]) -> None:
        for line in lines:
            low = line.lower()
            if low.startswith("serial:"):
                if "live" in low:
                    state = "ok"
                elif "simulation" in low or "sim" in low:
                    state = "warn"
                else:
                    state = "bad"
                detail = "LIVE" if state == "ok" else ("SIM" if state == "warn" else "DOWN")
                self._set_chip("serial", state, detail)
            elif low.startswith("ai:"):
                if "off" in low or "heuristics" in low:
                    state = "warn"
                    detail = "heuristic only"
                else:
                    state = "ok"
                    # AI: provider/model → pull "groq" etc.
                    detail = line.split(":", 1)[1].strip().split("(")[0].strip()
                self._set_chip("ai", state, detail)

    def _apply_state(self, msg: dict) -> None:
        for joint in ("base", "lift", "rotate", "claw"):
            bar = self.servo_bars.get(joint)
            if bar is not None:
                bar.set_value(int(msg.get(joint, 0)))
        range_mm = int(msg.get("range_mm", -1))
        range_text = "range: —" if range_mm < 0 else f"range: {range_mm} mm"
        age = float(msg.get("age", 0.0))
        self.range_label.configure(text=f"{range_text}   ·   age {age:4.1f}s")

        estop = bool(msg.get("estop"))
        if estop:
            self._set_badge(self.estop_badge, "E-STOP ENGAGED", "danger")
        else:
            self._set_badge(self.estop_badge, "E-STOP off", "success")

        self._record_lidar_sample(
            base_deg=int(msg.get("base", 0)),
            range_mm=range_mm,
            age=age,
        )

    def _pump_stdin(self) -> None:
        try:
            while True:
                msg = self._stdin_q.get_nowait()
                kind = msg.get("type")
                if kind == "status":
                    self._apply_status([str(x) for x in (msg.get("lines") or [])])
                elif kind == "state":
                    self._apply_state(msg)
                elif kind == "voice":
                    self._apply_voice(msg)
                elif kind == "telemetry":
                    self._apply_telemetry(msg)
                elif kind == "quit":
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(120, self._pump_stdin)

    # ---------- telemetry ----------

    def _apply_telemetry(self, msg: dict) -> None:
        # Pending confirmation banner.
        pending = msg.get("pending")
        self._apply_pending(pending)

        self._apply_lab(msg.get("lab") or {})

        # Health chips beyond what _apply_status already sets.
        health = msg.get("health") or {}
        self._apply_health(health)

        # Activity feed. Parent only sends the payload when something
        # actually changed, so an empty list + not-changed is a no-op.
        if msg.get("activity_changed") and self.activity_text is not None:
            self._apply_activity(msg.get("activity") or [])
        if msg.get("serial_changed") and self.serial_monitor_text is not None:
            self._apply_serial_monitor(msg.get("serial_monitor") or [])

    def _apply_lab(self, lab: dict) -> None:
        active = lab.get("active")
        if self.lab_active_label is not None:
            if active:
                self.lab_active_label.configure(
                    text=(
                        f"Active #{active.get('trial_id')}  "
                        f"{active.get('mode')} · {active.get('target')}  "
                        f"{active.get('elapsed_s')}s  "
                        f"commands={active.get('commands')}  corrections={active.get('corrections')}"
                    ),
                    fg=COLORS["accent"],
                )
            else:
                self.lab_active_label.configure(text="No active trial.", fg=COLORS["muted"])
        if self.lab_log_path_label is not None and lab.get("path"):
            self.lab_log_path_label.configure(text=f"Log path: {lab.get('path')}")

        text = self.lab_recent_text
        if text is None:
            return
        rows = lab.get("recent") or []
        text.configure(state="normal")
        text.delete("1.0", "end")
        if not rows:
            text.insert("end", "No completed trials yet.\n", ("muted",))
        else:
            text.insert("end", "trial  mode       result   time    corr  cmds  dist\n", ("muted",))
            for row in rows:
                ok = bool(row.get("success"))
                tag = "ok" if ok else "bad"
                dist = row.get("final_distance_cm")
                dist_text = "—" if dist in {None, ""} else f"{float(dist):0.2f}"
                text.insert(
                    "end",
                    (
                        f"{int(row.get('trial_id', 0)):>5}  "
                        f"{str(row.get('mode', ''))[:10]:<10} "
                    ),
                )
                text.insert("end", "success " if ok else "failure ", (tag,))
                text.insert(
                    "end",
                    (
                        f"{float(row.get('duration_s', 0.0)):>6.1f}s "
                        f"{int(row.get('corrections', 0)):>5} "
                        f"{int(row.get('commands', 0)):>5} "
                        f"{dist_text:>5}\n"
                    ),
                )
        text.configure(state="disabled")

    def _apply_pending(self, pending: dict | None) -> None:
        if self.pending_frame is None:
            return
        if pending is None:
            if self._pending_active:
                try:
                    self.pending_frame.pack_forget()
                except Exception:
                    pass
                self._pending_active = False
            return
        intent = str(pending.get("intent") or "")
        payload = pending.get("payload") or {}
        detail = ""
        if intent == "preset_pose":
            detail = f"pose · {payload.get('name', '?')}"
        elif intent in {"pick_object", "place_object"}:
            verb = "pick" if intent == "pick_object" else "place"
            detail = f"{verb} · {payload.get('label', 'object')}"
        else:
            detail = intent.replace("_", " ")
        source = str(pending.get("source") or "")
        src_txt = f"  (from {source})" if source else ""
        if self.pending_label is not None:
            self.pending_label.configure(
                text=f"Awaiting confirmation: {detail}{src_txt} — say Yes / No or click below.",
            )
        remaining = pending.get("remaining_s")
        if self.pending_countdown_label is not None:
            try:
                rem = float(remaining) if remaining is not None else 0.0
            except (TypeError, ValueError):
                rem = 0.0
            self.pending_countdown_label.configure(text=f"{rem:0.1f}s")
        if not self._pending_active:
            try:
                self.pending_frame.pack(fill="x", pady=(8, 2))
                self.pending_frame.lift()
            except Exception:
                pass
            self._pending_active = True

    def _apply_health(self, health: dict) -> None:
        cam = health.get("camera") or {}
        cam_age = cam.get("age_s", -1.0)
        if cam.get("active_index") is None and not cam.get("active_name"):
            self._set_chip("camera", "idle", "off")
        elif cam_age < 0:
            self._set_chip("camera", "warn", "starting…")
        elif cam_age > 3.0:
            self._set_chip("camera", "bad", f"stale {cam_age:0.0f}s")
        else:
            name = str(cam.get("active_name") or f"#{cam.get('active_index')}")
            self._set_chip("camera", "ok", name[:18])

        feats = health.get("features") or {}

        def _age_chip(key: str, age: float, enabled_flag: bool, *, warn: float, bad: float, idle_label: str, ok_detail: str = "") -> None:
            if not enabled_flag:
                self._set_chip(key, "idle", "off")
                return
            if age < 0:
                self._set_chip(key, "warn", idle_label)
                return
            if age > bad:
                self._set_chip(key, "warn", f"quiet {age:0.0f}s")
            elif age > warn:
                self._set_chip(key, "ok", f"{age:0.1f}s ago")
            else:
                self._set_chip(key, "ok", ok_detail or f"{age:0.1f}s")

        _age_chip("voice", float(health.get("voice_age_s") or -1.0), bool(feats.get("voice")),
                  warn=10.0, bad=45.0, idle_label="listening", ok_detail="live")
        _age_chip("vision", float(health.get("vision_age_s") or -1.0), bool(feats.get("vision")),
                  warn=5.0, bad=20.0, idle_label="scanning", ok_detail="tracking")
        _age_chip("gesture", float(health.get("gesture_age_s") or -1.0), bool(feats.get("gesture")),
                  warn=15.0, bad=60.0, idle_label="ready", ok_detail="")

    def _apply_activity(self, events: list) -> None:
        if self.activity_text is None:
            return
        new = [e for e in events if int(e.get("id", 0)) not in self._last_activity_ids]
        if not new:
            return
        for ev in new:
            self._last_activity_ids.add(int(ev.get("id", 0)))
        # Keep the id set from growing unbounded.
        if len(self._last_activity_ids) > 400:
            self._last_activity_ids = set(list(self._last_activity_ids)[-200:])

        text = self.activity_text
        text.configure(state="normal")
        for ev in new:
            source = str(ev.get("source") or "system")
            kind = str(ev.get("kind") or "")
            body = str(ev.get("text") or "")
            age = ev.get("age_s", 0.0)
            try:
                age_f = float(age)
            except (TypeError, ValueError):
                age_f = 0.0
            prefix_age = self._fmt_age(age_f)
            text.insert("end", f"{prefix_age:>4}  ", ("time",))
            text.insert("end", f"{source:<7}", (f"src_{source}",))
            text.insert("end", f" {kind:<9}", ("kind",))
            text.insert("end", f" {body}\n")
        # Trim oldest lines so the widget doesn't grow forever.
        line_count = int(text.index("end-1c").split(".")[0])
        if line_count > 200:
            text.delete("1.0", f"{line_count - 180}.0")
        text.see("end")
        text.configure(state="disabled")

    @staticmethod
    def _fmt_age(age_s: float) -> str:
        if age_s < 1.0:
            return "now"
        if age_s < 60.0:
            return f"{int(age_s)}s"
        if age_s < 3600.0:
            return f"{int(age_s // 60)}m"
        return f"{int(age_s // 3600)}h"

    def _record_lidar_sample(self, *, base_deg: int, range_mm: int, age: float) -> None:
        now = time.monotonic()
        cutoff = now - 45.0
        stale_keys = [key for key, sample in self._lidar_samples.items() if float(sample.get("ts", 0.0)) < cutoff]
        for key in stale_keys:
            self._lidar_samples.pop(key, None)

        if range_mm > 0 and age <= 1.5:
            bucket = int(round(base_deg / 5.0) * 5)
            self._lidar_samples[bucket] = {
                "angle": base_deg,
                "range_mm": range_mm,
                "ts": now,
            }
        self._redraw_lidar()

    def _redraw_lidar(self) -> None:
        canvas = self.lidar_canvas
        if canvas is None:
            return
        now = time.monotonic()
        canvas.delete("all")
        width = max(1, int(canvas.winfo_width()))
        height = max(1, int(canvas.winfo_height()))
        cx = width / 2.0
        cy = height / 2.0
        radius = min(width, height) * 0.38

        samples = sorted(self._lidar_samples.values(), key=lambda sample: float(sample.get("ts", 0.0)))
        fresh = [sample for sample in samples if float(sample.get("range_mm", -1)) > 0]
        max_range = max([800, *[int(sample["range_mm"]) for sample in fresh]]) if fresh else 800

        for frac in (0.25, 0.5, 0.75, 1.0):
            rr = radius * frac
            canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=COLORS["border"])
            mm = int(max_range * frac)
            canvas.create_text(
                cx + 4,
                cy - rr - 10,
                text=f"{mm} mm",
                fill=COLORS["dim"],
                anchor="w",
                font=("Menlo", 9),
            )

        canvas.create_line(cx - radius, cy, cx + radius, cy, fill=COLORS["border"])
        canvas.create_line(cx, cy - radius, cx, cy + radius, fill=COLORS["border"])
        canvas.create_text(cx, cy + radius + 10, text="rear", fill=COLORS["dim"], font=("Helvetica Neue", 9))
        canvas.create_text(cx, cy - radius - 10, text="front", fill=COLORS["dim"], font=("Helvetica Neue", 9))

        servo_min, servo_max = SERVO_LIMITS["base"]
        servo_mid = (servo_min + servo_max) / 2.0

        for sample in fresh:
            relative = float(sample["angle"]) - servo_mid
            theta = math.radians(relative - 90.0)
            distance = min(radius, radius * (float(sample["range_mm"]) / max_range))
            x = cx + math.cos(theta) * distance
            y = cy + math.sin(theta) * distance
            canvas.create_line(cx, cy, x, y, fill=COLORS["border_soft"])
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=COLORS["accent"], outline="")

        canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=COLORS["warning"], outline="")

        if self.lidar_summary_label is not None and self.lidar_detail_label is not None:
            if not fresh:
                self.lidar_summary_label.configure(text="No fresh LiDAR return yet.")
                self.lidar_detail_label.configure(text="Check TF-Luna power, Serial1 wiring, and sweep posture.")
            else:
                newest = fresh[-1]
                newest_age = now - float(newest["ts"])
                farthest = max(int(sample["range_mm"]) for sample in fresh)
                self.lidar_summary_label.configure(
                    text=f"Latest return: {int(newest['range_mm'])} mm at base {int(newest['angle'])}°",
                )
                self.lidar_detail_label.configure(
                    text=(
                        f"{len(fresh)} sweep buckets cached  ·  farthest {farthest} mm  ·  "
                        f"updated {newest_age:0.1f}s ago"
                    ),
                )

    def _apply_serial_monitor(self, events: list) -> None:
        text = self.serial_monitor_text
        if text is None:
            return
        new_events = [event for event in events if int(event.get("id", 0)) not in self._last_serial_ids]
        if not new_events:
            return
        for event in new_events:
            self._last_serial_ids.add(int(event.get("id", 0)))
        if len(self._last_serial_ids) > 400:
            self._last_serial_ids = set(list(self._last_serial_ids)[-200:])

        text.configure(state="normal")
        for event in new_events:
            age = self._fmt_age(float(event.get("age_s", 0.0) or 0.0))
            channel = str(event.get("channel") or "SER")
            body = str(event.get("text") or "")
            level = str(event.get("level") or "info")
            level_tag = {
                "ok": "serial_ok",
                "warn": "serial_warn",
                "bad": "serial_bad",
            }.get(level, "")
            text.insert("end", f"{age:>4}  ", ("serial_time",))
            text.insert("end", f"{channel:<8}", ("serial_channel",))
            if level_tag:
                text.insert("end", f" {body}\n", (level_tag,))
            else:
                text.insert("end", f" {body}\n")
        line_count = int(text.index("end-1c").split(".")[0])
        if line_count > 220:
            text.delete("1.0", f"{line_count - 180}.0")
        text.see("end")
        text.configure(state="disabled")


def main() -> int:
    try:
        panel = Panel()
    except Exception as exc:
        sys.stderr.write(f"control-panel gui init failed: {exc}\n")
        return 1
    try:
        panel.root.mainloop()
    except Exception as exc:
        sys.stderr.write(f"control-panel gui mainloop error: {exc}\n")
        return 1
    _emit({"type": "shutdown"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

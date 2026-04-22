"""2026-style Virtual IT Service Desk — customtkinter GUI."""

from __future__ import annotations

import datetime
import os
import queue
import random
import sys
import threading
import time

import customtkinter as ctk
import psutil
from dotenv import load_dotenv, set_key

from agent.core import run_agent

load_dotenv()

# ── Font scaling ────────────────────────────────────────────────────────────
_FONT_SCALE: float = float(os.environ.get("FONT_SCALE", "1.0"))


def _f(size: int) -> int:
    """Return font size scaled by _FONT_SCALE, minimum 8."""
    return max(8, round(size * _FONT_SCALE))

# ── Design tokens ──────────────────────────────────────────────────────────
BG      = "#080C18"
PANEL   = "#0D1526"
SURFACE = "#111D33"
BORDER  = "#1A2B45"
CYAN    = "#00D4FF"
PURPLE  = "#8B5CF6"
GREEN   = "#00E676"
YELLOW  = "#FFB800"
RED     = "#FF4560"
TEXT    = "#E2ECF8"
MUTED   = "#3D5270"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ── Approval dialog ────────────────────────────────────────────────────────

class ApprovalDialog(ctk.CTkToplevel):

    def __init__(self, parent, explanation, tech_detail, event, result):
        super().__init__(parent)
        self._event = event
        self._result = result
        self.configure(fg_color=PANEL)
        self.title("Aktion bestätigen")
        self.geometry("560x300")
        self.resizable(False, False)
        self.grab_set()
        self.focus_set()
        self.lift()
        self.protocol("WM_DELETE_WINDOW", self._deny)
        self.bind("<Escape>", lambda _: self._deny())

        ctk.CTkFrame(self, height=3, fg_color=YELLOW, corner_radius=0).pack(fill="x")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=18)

        ctk.CTkLabel(body, text="⚡  AKTION ERFORDERLICH",
                     font=ctk.CTkFont(family="monospace", size=_f(13), weight="bold"),
                     text_color=YELLOW).pack(anchor="w", pady=(0, 10))

        ctk.CTkLabel(body, text=explanation,
                     font=ctk.CTkFont(size=_f(12)), text_color=TEXT,
                     wraplength=500, justify="left").pack(anchor="w", pady=(0, 12))

        chip = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=6)
        chip.pack(fill="x", pady=(0, 18))
        ctk.CTkLabel(chip, text=f"  $ {tech_detail}",
                     font=ctk.CTkFont(family="monospace", size=_f(11)),
                     text_color=CYAN).pack(anchor="w", padx=12, pady=7)

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(anchor="w")

        ctk.CTkButton(btns, text="✓  Bestätigen",
                      fg_color="#0A3D20", hover_color="#072D17",
                      text_color=GREEN, border_color=GREEN, border_width=1,
                      width=165, height=40, corner_radius=8,
                      font=ctk.CTkFont(size=_f(12), weight="bold"),
                      command=self._approve).pack(side="left", padx=(0, 12))

        ctk.CTkButton(btns, text="✗  Abbrechen (Esc)",
                      fg_color="#3D0A10", hover_color="#2D0509",
                      text_color=RED, border_color=RED, border_width=1,
                      width=165, height=40, corner_radius=8,
                      font=ctk.CTkFont(size=_f(12), weight="bold"),
                      command=self._deny).pack(side="left")

    def _approve(self):
        self._result[0] = True
        self.destroy()
        self._event.set()

    def _deny(self):
        self._result[0] = False
        self.destroy()
        self._event.set()


# ── Toast notification ─────────────────────────────────────────────────────

class Toast(ctk.CTkToplevel):
    """3-second non-blocking notification in top-right corner."""

    COLORS = {"ok": GREEN, "warn": YELLOW, "error": RED}
    ICONS  = {"ok": "✅", "warn": "⚠️", "error": "❌"}

    def __init__(self, parent, message: str, kind: str = "ok"):
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=SURFACE)

        color = self.COLORS.get(kind, GREEN)
        icon  = self.ICONS.get(kind, "✅")

        ctk.CTkFrame(self, height=3, fg_color=color, corner_radius=0).pack(fill="x")
        ctk.CTkLabel(self, text=f"  {icon}  {message}  ",
                     font=ctk.CTkFont(size=_f(12)), text_color=TEXT,
                     padx=8, pady=10).pack()

        # Position top-right
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        self.geometry(f"+{sw - self.winfo_width() - 20}+20")
        self.after(3000, self.destroy)


# ── Main window ────────────────────────────────────────────────────────────

class ServiceDeskApp(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.configure(fg_color=BG)
        self.title("IT Service Desk  ·  AI-Powered")
        self.geometry("1160x760")
        self.minsize(960 if _FONT_SCALE > 1.0 else 820,
                     600 if _FONT_SCALE > 1.0 else 520)

        self._queue: queue.Queue = queue.Queue()
        self._history: list[dict] = []
        self._busy = False
        self._thinking_frame: ctk.CTkFrame | None = None
        self._msg_count = 0
        self._ticket_id = f"#{random.randint(1000, 9999)}"
        self._ticket_status = "OFFEN"
        self._sidebar_visible = True
        self._last_agent_text = ""

        self._build_ui()
        self._start_stats_thread()
        self._welcome()
        self.after(100, self._poll)

        # Keyboard shortcuts
        self.bind("<Control-l>", lambda _: self._new_session())
        self.bind("<Control-L>", lambda _: self._new_session())

    # ── Layout ──────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self._build_header()
        self._build_sidebar()
        self._build_chat()
        self._build_inputbar()

    # ── Header ──────────────────────────────────────────────────────────

    def _build_header(self):
        h = ctk.CTkFrame(self, height=58, corner_radius=0,
                         fg_color=PANEL, border_color=BORDER, border_width=1)
        h.grid(row=0, column=0, columnspan=2, sticky="ew")
        h.grid_propagate(False)

        left = ctk.CTkFrame(h, fg_color="transparent")
        left.pack(side="left", padx=14, pady=10)

        # Sidebar toggle
        self._toggle_btn = ctk.CTkButton(
            left, text="◀", width=28, height=28, corner_radius=6,
            fg_color=SURFACE, hover_color=BORDER, text_color=MUTED,
            font=ctk.CTkFont(size=_f(12)),
            command=self._toggle_sidebar,
        )
        self._toggle_btn.pack(side="left", padx=(0, 10))

        self._dot = ctk.CTkFrame(left, width=10, height=10,
                                  corner_radius=5, fg_color=GREEN)
        self._dot.pack(side="left", padx=(0, 10))

        ctk.CTkLabel(left, text="IT SERVICE DESK",
                     font=ctk.CTkFont(family="monospace", size=_f(15), weight="bold"),
                     text_color=CYAN).pack(side="left")

        ctk.CTkLabel(left, text="  ·  AI-Powered Support",
                     font=ctk.CTkFont(size=_f(12)), text_color=MUTED).pack(side="left")

        right = ctk.CTkFrame(h, fg_color="transparent")
        right.pack(side="right", padx=16)

        # System health ampel (CPU / RAM / DISK)
        self._ampel: dict = {}
        for key, label in [("cpu", "CPU"), ("ram", "RAM"), ("disk", "DSK")]:
            f = ctk.CTkFrame(right, fg_color="transparent")
            f.pack(side="right", padx=6)
            ctk.CTkLabel(f, text=label,
                         font=ctk.CTkFont(family="monospace", size=_f(9)),
                         text_color=MUTED).pack()
            dot = ctk.CTkFrame(f, width=10, height=10, corner_radius=5,
                                fg_color=GREEN)
            dot.pack()
            self._ampel[key] = dot

        ctk.CTkFrame(right, width=1, fg_color=BORDER).pack(
            side="right", fill="y", pady=8, padx=8)

        self._ticket_lbl = ctk.CTkLabel(
            right, text=f"{self._ticket_id}  {self._ticket_status}",
            font=ctk.CTkFont(family="monospace", size=_f(11), weight="bold"),
            text_color=YELLOW)
        self._ticket_lbl.pack(side="right", padx=8)

        ctk.CTkFrame(right, width=1, fg_color=BORDER).pack(
            side="right", fill="y", pady=8, padx=8)

        self._clock = ctk.CTkLabel(right, text="",
                                    font=ctk.CTkFont(family="monospace", size=_f(11)),
                                    text_color=MUTED)
        self._clock.pack(side="right", padx=4)
        self._tick()

    # ── Sidebar ──────────────────────────────────────────────────────────

    def _build_sidebar(self):
        self._sidebar = ctk.CTkFrame(
            self, width=230, corner_radius=0,
            fg_color=PANEL, border_color=BORDER, border_width=1)
        self._sidebar.grid(row=1, column=0, sticky="nsew", rowspan=2)
        self._sidebar.grid_propagate(False)

        def section(text):
            ctk.CTkLabel(self._sidebar, text=text,
                         font=ctk.CTkFont(family="monospace", size=_f(9), weight="bold"),
                         text_color=MUTED).pack(anchor="w", padx=16, pady=(16, 5))

        section("SYSTEM STATUS")
        self._bars: dict = {}
        for key, label, color in [("cpu", "CPU", CYAN), ("ram", "SPEICHER", PURPLE),
                                    ("disk", "DISK", GREEN)]:
            card = ctk.CTkFrame(self._sidebar, fg_color=SURFACE, corner_radius=8)
            card.pack(fill="x", padx=12, pady=3)
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=(8, 2))
            ctk.CTkLabel(row, text=label,
                         font=ctk.CTkFont(family="monospace", size=_f(10), weight="bold"),
                         text_color=TEXT).pack(side="left")
            val = ctk.CTkLabel(row, text="—",
                                font=ctk.CTkFont(family="monospace", size=_f(10)),
                                text_color=color)
            val.pack(side="right")
            bar = ctk.CTkProgressBar(card, height=3, corner_radius=2,
                                      progress_color=color, fg_color=BORDER)
            bar.set(0)
            bar.pack(fill="x", padx=10, pady=(0, 8))
            self._bars[key] = (bar, val)

        ctk.CTkFrame(self._sidebar, height=1, fg_color=BORDER).pack(
            fill="x", padx=12, pady=12)

        section("SCHNELLAKTIONEN")
        for icon_label, prompt in [
            ("🌐  Netzwerk prüfen",     "Check my network connection"),
            ("⚡  CPU-Auslastung",      "Show me the top CPU processes"),
            ("💾  Speicherplatz",       "How much disk space do I have?"),
            ("🔐  Login-Aktivität",     "Show me recent failed logins and active sessions"),
            ("🌡️  Temperaturen",        "Check system temperatures"),
            ("📦  Updates verfügbar?",  "Are there package updates available?"),
            ("🔄  DNS leeren",          "Flush the DNS cache"),
        ]:
            ctk.CTkButton(self._sidebar, text=icon_label,
                          fg_color=SURFACE, hover_color=BORDER,
                          text_color=TEXT, anchor="w", height=32, corner_radius=6,
                          font=ctk.CTkFont(size=_f(12)),
                          command=lambda p=prompt: self._quick(p)).pack(
                fill="x", padx=12, pady=2)

        ctk.CTkFrame(self._sidebar, height=1, fg_color=BORDER).pack(
            fill="x", padx=12, pady=12)

        section("SESSION")
        self._session_lbl = ctk.CTkLabel(
            self._sidebar, text="Nachrichten: 0",
            font=ctk.CTkFont(family="monospace", size=_f(10)), text_color=MUTED)
        self._session_lbl.pack(anchor="w", padx=16)

        ctk.CTkButton(
            self._sidebar, text="＋  Neue Anfrage",
            fg_color=SURFACE, hover_color=BORDER,
            text_color=CYAN, anchor="w", height=32, corner_radius=6,
            font=ctk.CTkFont(size=_f(12)),
            command=self._new_session,
        ).pack(fill="x", padx=12, pady=(8, 4))

        ctk.CTkFrame(self._sidebar, height=1, fg_color=BORDER).pack(
            fill="x", padx=12, pady=12)

        section("SCHRIFTGRÖSSE")
        font_row = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        font_row.pack(fill="x", padx=12, pady=(0, 8))
        for label, scale in [("A−", 0.85), ("A", 1.0), ("A+", 1.2), ("A++", 1.4)]:
            active = abs(_FONT_SCALE - scale) < 0.05
            ctk.CTkButton(
                font_row, text=label,
                fg_color=CYAN if active else SURFACE,
                hover_color="#00AACC" if active else BORDER,
                text_color="#000000" if active else TEXT,
                width=46, height=30, corner_radius=6,
                font=ctk.CTkFont(size=_f(11), weight="bold"),
                command=lambda s=scale: self._set_font_scale(s),
            ).pack(side="left", padx=2)

    def _set_font_scale(self, scale: float) -> None:
        if self._busy:
            return
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        set_key(env_path, "FONT_SCALE", str(scale))
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _toggle_sidebar(self):
        if self._sidebar_visible:
            self._sidebar.grid_remove()
            self._toggle_btn.configure(text="▶")
        else:
            self._sidebar.grid()
            self._toggle_btn.configure(text="◀")
        self._sidebar_visible = not self._sidebar_visible

    # ── Chat area ────────────────────────────────────────────────────────

    def _build_chat(self):
        self._chat = ctk.CTkScrollableFrame(
            self, fg_color=BG,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=SURFACE)
        self._chat.grid(row=1, column=1, sticky="nsew")
        self._chat.grid_columnconfigure(0, weight=1)

    # ── Input bar ────────────────────────────────────────────────────────

    def _build_inputbar(self):
        bar = ctk.CTkFrame(self, height=78, corner_radius=0,
                           fg_color=PANEL, border_color=BORDER, border_width=1)
        bar.grid(row=2, column=1, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        self._entry = ctk.CTkEntry(
            bar,
            placeholder_text="Problem beschreiben…  (Ctrl+L = Neue Anfrage)",
            font=ctk.CTkFont(size=_f(13)),
            fg_color=SURFACE, border_color=BORDER, text_color=TEXT,
            height=46, corner_radius=10)
        self._entry.grid(row=0, column=0, padx=(16, 10), pady=16, sticky="ew")
        self._entry.bind("<Return>", lambda _: self._send())

        self._btn = ctk.CTkButton(
            bar, text="▶", fg_color=CYAN, hover_color="#00AACC",
            text_color="#000000", width=54, height=46, corner_radius=10,
            font=ctk.CTkFont(size=_f(16), weight="bold"),
            command=self._send)
        self._btn.grid(row=0, column=1, padx=(0, 16), pady=16)

    # ── Bubble rendering ─────────────────────────────────────────────────

    def _add_bubble(self, text: str, role: str) -> None:
        now = datetime.datetime.now().strftime("%H:%M")
        is_user = role == "user"
        cfg = {
            "user":   (SURFACE,    CYAN,   "🧑", "Du",      "e"),
            "agent":  (SURFACE,    TEXT,   "🤖", "Agent",   "w"),
            "tool":   ("#091A0E",  GREEN,  "⚙️", "System",  "w"),
            "system": ("#1A1000",  YELLOW, "⚡", "Hinweis", "w"),
        }
        bg, fg, icon, name, side = cfg.get(role, cfg["agent"])

        if role == "agent":
            self._last_agent_text = text

        outer = ctk.CTkFrame(self._chat, fg_color="transparent")
        outer.pack(fill="x", padx=16, pady=5, anchor=side)

        # Meta row: icon + name + time + copy button
        meta_row = ctk.CTkFrame(outer, fg_color="transparent")
        meta_row.pack(anchor="e" if is_user else "w",
                      padx=(60 if is_user else 2, 2 if is_user else 60))

        ctk.CTkLabel(meta_row, text=f"{icon}  {name}   {now}",
                     font=ctk.CTkFont(size=_f(10)), text_color=MUTED).pack(side="left")

        # Copy button (small, appears inline)
        copy_btn = ctk.CTkButton(
            meta_row, text="⎘", width=22, height=18,
            fg_color="transparent", hover_color=SURFACE,
            text_color=MUTED, font=ctk.CTkFont(size=_f(10)),
            command=lambda t=text: self._copy(t))
        copy_btn.pack(side="left", padx=(6, 0))

        lines = text.count("\n") + 1
        h = min(max(lines * 20 + 28, 48), 360)

        bubble = ctk.CTkTextbox(
            outer, wrap="word", fg_color=bg, text_color=fg,
            font=ctk.CTkFont(size=_f(12)), height=h,
            activate_scrollbars=False, border_spacing=12,
            border_color=BORDER, border_width=1, corner_radius=12)
        bubble.insert("1.0", text)
        bubble.configure(state="disabled")
        bubble.pack(
            anchor="e" if is_user else "w",
            fill="x" if not is_user else "none",
            expand=not is_user,
            padx=(80 if is_user else 0, 0 if is_user else 80))

        if is_user:
            self._msg_count += 1
            self._session_lbl.configure(text=f"Nachrichten: {self._msg_count}")
            self._set_ticket_status("IN BEARBEITUNG", CYAN)

        self.after(60, lambda: self._chat._parent_canvas.yview_moveto(1.0))

    def _show_thinking(self) -> None:
        f = ctk.CTkFrame(self._chat, fg_color=SURFACE, corner_radius=12,
                         border_color=BORDER, border_width=1)
        f.pack(anchor="w", padx=16, pady=5)
        f._is_thinking = True  # type: ignore[attr-defined]
        lbl = ctk.CTkLabel(f, text="🤖  ● ● ●",
                           font=ctk.CTkFont(family="monospace", size=_f(12)),
                           text_color=CYAN)
        lbl.pack(padx=18, pady=12)
        self._thinking_frame = f
        self._animate_dots(lbl, 0)
        self.after(60, lambda: self._chat._parent_canvas.yview_moveto(1.0))

    def _animate_dots(self, lbl, tick):
        if not self._busy:
            return
        frames = ["🤖  ●  ○  ○", "🤖  ●  ●  ○", "🤖  ●  ●  ●",
                  "🤖  ○  ●  ●", "🤖  ○  ○  ●", "🤖  ○  ○  ○"]
        lbl.configure(text=frames[tick % len(frames)])
        self.after(300, lambda: self._animate_dots(lbl, tick + 1))

    def _hide_thinking(self) -> None:
        if self._thinking_frame:
            self._thinking_frame.destroy()
            self._thinking_frame = None

    # ── Ticket status ─────────────────────────────────────────────────────

    def _set_ticket_status(self, status: str, color: str = YELLOW) -> None:
        self._ticket_status = status
        self._ticket_lbl.configure(
            text=f"{self._ticket_id}  {status}", text_color=color)

    # ── Copy to clipboard ─────────────────────────────────────────────────

    def _copy(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        Toast(self, "Text kopiert", "ok")

    # ── New session ───────────────────────────────────────────────────────

    def _new_session(self) -> None:
        if self._busy:
            return
        for w in self._chat.winfo_children():
            w.destroy()
        self._history = []
        self._msg_count = 0
        self._ticket_id = f"#{random.randint(1000, 9999)}"
        self._session_lbl.configure(text="Nachrichten: 0")
        self._set_ticket_status("OFFEN", YELLOW)
        self._welcome()

    # ── Send & agent thread ───────────────────────────────────────────────

    def _send(self) -> None:
        text = self._entry.get().strip()
        if not text or self._busy:
            return
        self._entry.delete(0, "end")
        self._add_bubble(text, "user")
        self._set_busy(True)
        threading.Thread(target=self._agent_thread, args=(text,), daemon=True).start()

    def _quick(self, prompt: str) -> None:
        if self._busy:
            return
        self._entry.delete(0, "end")
        self._entry.insert(0, prompt)
        self._send()

    def _agent_thread(self, query: str) -> None:
        def on_text(t):
            self._queue.put(("text", t))

        def on_tool(name):
            self._queue.put(("tool", f"Führe aus: {name}"))

        def on_approval(explanation, tech_detail):
            ev = threading.Event()
            res: list[bool] = [False]
            self._queue.put(("approval", explanation, tech_detail, ev, res))
            ev.wait()
            return res[0]

        def on_blocked(reason):
            self._queue.put(("system", f"Blockiert: {reason}"))

        try:
            updated = run_agent(
                query,
                history=self._history,
                on_text=on_text,
                on_tool=on_tool,
                on_approval=on_approval,
                on_blocked=on_blocked,
            )
            self._queue.put(("history", updated))
            self._queue.put(("toast", "Antwort bereit", "ok"))
        except Exception as exc:
            self._queue.put(("system", f"Fehler: {exc}"))
            self._queue.put(("toast", "Fehler aufgetreten", "error"))
        finally:
            self._queue.put(("done",))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self._btn.configure(state="disabled", text="…",
                                 fg_color=MUTED, text_color=TEXT)
            self._entry.configure(state="disabled")
            self._show_thinking()
            self._dot.configure(fg_color=YELLOW)
        else:
            self._btn.configure(state="normal", text="▶",
                                 fg_color=CYAN, text_color="#000000")
            self._entry.configure(state="normal")
            self._entry.focus()
            self._dot.configure(fg_color=GREEN)

    # ── Queue polling ─────────────────────────────────────────────────────

    def _poll(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]
                if kind == "text":
                    self._hide_thinking()
                    self._add_bubble(msg[1], "agent")
                elif kind == "tool":
                    self._add_bubble(msg[1], "tool")
                elif kind == "system":
                    self._hide_thinking()
                    self._add_bubble(msg[1], "system")
                elif kind == "approval":
                    _, expl, tech, ev, res = msg
                    ApprovalDialog(self, expl, tech, ev, res)
                elif kind == "history":
                    self._history = msg[1]
                elif kind == "stats":
                    _, cpu, ram, disk = msg
                    self._update_bars(cpu, ram, disk)
                elif kind == "toast":
                    _, message, toast_kind = msg
                    Toast(self, message, toast_kind)
                elif kind == "done":
                    self._hide_thinking()
                    self._set_busy(False)
                    self._set_ticket_status("✓ GELÖST", GREEN)
        except queue.Empty:
            pass
        except Exception:
            pass
        self.after(100, self._poll)

    # ── Live system stats ─────────────────────────────────────────────────

    def _start_stats_thread(self) -> None:
        def loop():
            while True:
                try:
                    cpu  = psutil.cpu_percent(interval=1)
                    ram  = psutil.virtual_memory().percent
                    disk = psutil.disk_usage("/").percent
                    self._queue.put(("stats", cpu, ram, disk))
                except Exception:
                    pass
                time.sleep(3)
        threading.Thread(target=loop, daemon=True).start()

    def _update_bars(self, cpu: float, ram: float, disk: float) -> None:
        for key, val in [("cpu", cpu), ("ram", ram), ("disk", disk)]:
            bar, lbl = self._bars[key]
            bar.set(val / 100)
            lbl.configure(text=f"{val:.0f}%")
            # Update header ampel
            color = GREEN if val < 70 else (YELLOW if val < 90 else RED)
            self._ampel[key].configure(fg_color=color)

    # ── Clock ─────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._clock.configure(
            text=datetime.datetime.now().strftime("%d.%m.%Y  %H:%M:%S"))
        self.after(1000, self._tick)

    # ── Welcome ───────────────────────────────────────────────────────────

    def _welcome(self) -> None:
        self._add_bubble(
            "Willkommen beim AI-gestützten IT Service Desk.\n\n"
            "Beschreibe dein Problem — ich diagnostiziere und löse es Schritt für Schritt.\n"
            "Nutze die Schnellaktionen links für häufige Anfragen.\n\n"
            "Tastenkürzel: Ctrl+L = Neue Anfrage  ·  Esc = Aktion ablehnen",
            "agent",
        )


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY nicht gesetzt.")
        return
    ServiceDeskApp().mainloop()


if __name__ == "__main__":
    main()

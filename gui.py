"""GUI frontend for the IT Service Desk Agent (Linux, customtkinter)."""

from __future__ import annotations

import os
import queue
import threading

import customtkinter as ctk
from dotenv import load_dotenv

from agent.core import run_agent

load_dotenv()

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ---------------------------------------------------------------------------
# Approval dialog
# ---------------------------------------------------------------------------

class ApprovalDialog(ctk.CTkToplevel):
    """Modal popup shown before any remediation action."""

    def __init__(
        self,
        parent: ctk.CTk,
        explanation: str,
        tech_detail: str,
        event: threading.Event,
        result: list[bool],
    ) -> None:
        super().__init__(parent)
        self._event = event
        self._result = result

        self.title("Genehmigung erforderlich")
        self.geometry("520x300")
        self.resizable(False, False)
        self.grab_set()
        self.focus_set()
        self.lift()
        self.protocol("WM_DELETE_WINDOW", self._deny)

        ctk.CTkLabel(
            self,
            text="⚠️  Genehmigung erforderlich",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#FFB800",
        ).pack(pady=(18, 4), padx=20)

        ctk.CTkLabel(
            self,
            text="Der Agent möchte folgende Aktion ausführen:",
            font=ctk.CTkFont(size=12),
        ).pack(padx=20)

        exp_box = ctk.CTkTextbox(
            self, height=80, wrap="word",
            font=ctk.CTkFont(size=12), activate_scrollbars=False,
        )
        exp_box.pack(fill="x", padx=20, pady=(8, 4))
        exp_box.insert("1.0", explanation)
        exp_box.configure(state="disabled")

        ctk.CTkLabel(
            self,
            text=f"Befehl:  {tech_detail}",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
        ).pack(padx=20, pady=(0, 14))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=4)

        ctk.CTkButton(
            btn_frame,
            text="✓  Ja, ausführen",
            fg_color="#2B7A3C",
            hover_color="#1F5C2D",
            width=160,
            command=self._approve,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            btn_frame,
            text="✗  Abbrechen",
            fg_color="#8B2020",
            hover_color="#6B1818",
            width=160,
            command=self._deny,
        ).pack(side="left", padx=10)

    def _approve(self) -> None:
        self._result[0] = True
        self.destroy()
        self._event.set()

    def _deny(self) -> None:
        self._result[0] = False
        self.destroy()
        self._event.set()


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class ChatApp(ctk.CTk):

    def __init__(self) -> None:
        super().__init__()
        self.title("🖥️  IT Service Desk Agent")
        self.geometry("820x620")
        self.minsize(600, 420)

        self._queue: queue.Queue = queue.Queue()
        self._history: list[dict] = []
        self._build_ui()
        self._add_bubble(
            "Hallo! Ich bin dein IT-Support-Agent.\n"
            "Beschreibe dein Problem in normaler Sprache.",
            role="agent",
        )
        self.after(100, self._poll)

    # ------------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header bar
        header = ctk.CTkFrame(self, height=52, corner_radius=0, fg_color="#0F172A")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        ctk.CTkLabel(
            header,
            text="🖥️   IT Service Desk Agent",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#93C5FD",
        ).pack(side="left", padx=18, pady=12)

        # Chat scroll area
        self._chat = ctk.CTkScrollableFrame(self, fg_color="#0D0D0D")
        self._chat.grid(row=1, column=0, sticky="nsew")
        self._chat.grid_columnconfigure(0, weight=1)

        # Input bar
        bar = ctk.CTkFrame(self, height=72, corner_radius=0, fg_color="#1E1E2E")
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        self._entry = ctk.CTkEntry(
            bar,
            placeholder_text="Problem beschreiben und Enter drücken…",
            font=ctk.CTkFont(size=13),
            height=42,
        )
        self._entry.grid(row=0, column=0, padx=(14, 8), pady=15, sticky="ew")
        self._entry.bind("<Return>", lambda _e: self._send())

        self._btn = ctk.CTkButton(
            bar, text="Senden", width=100, height=42,
            command=self._send,
        )
        self._btn.grid(row=0, column=1, padx=(0, 14), pady=15)

    # ------------------------------------------------------------------
    # Chat bubbles
    # ------------------------------------------------------------------

    def _add_bubble(self, text: str, role: str) -> None:
        """Render a chat bubble. role: 'user' | 'agent' | 'tool' | 'system'"""
        cfg = {
            "user":   {"bg": "#1D4ED8", "fg": "#F0F9FF", "side": "right"},
            "agent":  {"bg": "#1E293B", "fg": "#E2E8F0", "side": "left"},
            "tool":   {"bg": "#052E16", "fg": "#86EFAC", "side": "left"},
            "system": {"bg": "#2D1B00", "fg": "#FDE68A", "side": "left"},
        }
        c = cfg.get(role, cfg["agent"])

        row = ctk.CTkFrame(self._chat, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=3)

        # Estimate height from line count
        lines = text.count("\n") + 1
        height = min(max(lines * 22 + 22, 46), 320)

        box = ctk.CTkTextbox(
            row,
            wrap="word",
            fg_color=c["bg"],
            text_color=c["fg"],
            font=ctk.CTkFont(size=12),
            height=height,
            activate_scrollbars=False,
            border_spacing=10,
        )
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.pack(
            side=c["side"],  # type: ignore[arg-type]
            fill="x" if role != "user" else "none",
            expand=(role != "user"),
            padx=(0 if role == "user" else 4, 4 if role == "user" else 0),
        )

        # Scroll to bottom
        self.after(60, lambda: self._chat._parent_canvas.yview_moveto(1.0))

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self._btn.configure(state=state, text="…" if busy else "Senden")
        self._entry.configure(state=state)
        if not busy:
            self._entry.focus()

    # ------------------------------------------------------------------
    # Send & agent thread
    # ------------------------------------------------------------------

    def _send(self) -> None:
        text = self._entry.get().strip()
        if not text:
            return
        self._entry.delete(0, "end")
        self._add_bubble(text, "user")
        self._set_busy(True)
        threading.Thread(target=self._agent_thread, args=(text,), daemon=True).start()

    def _agent_thread(self, query: str) -> None:
        def on_text(t: str) -> None:
            self._queue.put(("text", t))

        def on_tool(name: str) -> None:
            self._queue.put(("tool", f"⚙️  Führe aus: {name}"))

        def on_approval(explanation: str, tech_detail: str) -> bool:
            event = threading.Event()
            result: list[bool] = [False]
            self._queue.put(("approval", explanation, tech_detail, event, result))
            event.wait()
            return result[0]

        def on_blocked(reason: str) -> None:
            self._queue.put(("system", f"🚫  Blockiert: {reason}"))

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
        except Exception as exc:
            self._queue.put(("system", f"❌  Fehler: {exc}"))
        finally:
            self._queue.put(("done",))

    # ------------------------------------------------------------------
    # Queue polling (main thread)
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]
                if kind == "text":
                    self._add_bubble(msg[1], "agent")
                elif kind == "tool":
                    self._add_bubble(msg[1], "tool")
                elif kind == "system":
                    self._add_bubble(msg[1], "system")
                elif kind == "approval":
                    _, explanation, tech_detail, event, result = msg
                    ApprovalDialog(self, explanation, tech_detail, event, result)
                elif kind == "history":
                    self._history = msg[1]
                elif kind == "done":
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._poll)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY nicht gesetzt. Bitte .env Datei prüfen.")
        return
    app = ChatApp()
    app.mainloop()


if __name__ == "__main__":
    main()

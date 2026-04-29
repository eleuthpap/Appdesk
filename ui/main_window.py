import time
import customtkinter as ctk
from tkinter import messagebox

import config
from client.input_forwarder import InputForwarder
from client.screen_receiver import ScreenReceiver
from host.input_executor import InputExecutor
from host.screen_server import ScreenServer
from ui.host_panel import HostPanel
from ui.session_window import SessionWindow

class MainWindow(ctk.CTkFrame):
    """Root window: lets the user either host a session or connect to one."""

    def __init__(self, master: ctk.CTk) -> None:
        super().__init__(master)
        self.pack(padx=24, pady=20)
        self._build_ui()

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        ctk.CTkLabel(
            self,
            text="Appdesk",
            text_color="#915614",
            font=("Times New Roman", 26, "bold"),
        ).pack(pady=(0, 2))
        ctk.CTkLabel(
            self,
            text="Remote desktop over relay",
            font=("Times New Roman", 12),
        ).pack(pady=(0, 16))

        ctk.CTkLabel(
            self,
            text="Relay",
            font=("Times New Roman", 14, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        relay_frame = ctk.CTkFrame(self)
        relay_frame.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(relay_frame, text="Relay host:port").pack(anchor="w")
        self._relay_var = ctk.StringVar(value=config.RELAY_SERVER or "")
        ctk.CTkEntry(relay_frame, textvariable=self._relay_var, width=280).pack(
            anchor="w", pady=(2, 0)
        )

        # ── Host section ──────────────────────────────────────────────────
        ctk.CTkLabel(
            self,
            text="Host a session",
            font=("Times New Roman", 14, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        host_frame = ctk.CTkFrame(self)
        host_frame.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            host_frame,
            font=("Times New Roman", 12, "bold"),
            text="Share your screen and allow remote control.",
        ).pack(anchor="w", pady=(0, 6))
        ctk.CTkButton(
            host_frame,
            text="Start Hosting",
            width=220,
            command=self._start_host,
            fg_color="#9B5403",
            hover_color="#573107"
        ).pack()

        # ── Connect section ───────────────────────────────────────────────
        ctk.CTkLabel(
            self,
            text="Join a session",
            font=("Times New Roman", 14, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        conn_frame = ctk.CTkFrame(self)
        conn_frame.pack(fill="x")
        ctk.CTkLabel(
            conn_frame,
            text="Session ID / Host IP",
            font=("Times New Roman", 12, "bold")
        ).pack(anchor="w")
        self._session_var = ctk.StringVar(value="")
        ctk.CTkEntry(conn_frame, textvariable=self._session_var, width=260).pack(
            anchor="w", pady=(2, 8)
        )
        ctk.CTkButton(
            conn_frame,
            text="Connect",
            width=220,
            command=self._start_connect,
            fg_color="#9B5403",
            hover_color="#573107"
        ).pack()

    # ----------------------------------------------------------------- actions

    def _start_host(self) -> None:
        relay_addr = self._relay_var.get().strip()
        screen_server = ScreenServer(relay_addr=relay_addr)
        input_executor = InputExecutor()

        if relay_addr:
            screen_server.on_session_ready = (
                lambda sid: input_executor.start_relay(sid, relay_addr)
            )

        screen_server.start()
        if not relay_addr:
            input_executor.start()
        else:
            # Wait briefly for relay to issue a session ID before showing panel.
            deadline = time.time() + 5
            while not screen_server.session_id and time.time() < deadline:
                time.sleep(0.05)
            if not screen_server.session_id:
                screen_server.stop()
                messagebox.showerror(
                    "Relay error",
                    "Could not register host at relay. Check relay host:port.",
                )
                return

        HostPanel(self, screen_server, input_executor, relay_addr=relay_addr)

    def _start_connect(self) -> None:
        session_or_host = self._session_var.get().strip()
        relay_addr = self._relay_var.get().strip()

        if relay_addr and not session_or_host:
            messagebox.showerror(
                "Relay settings",
                "Provide both Session ID and Relay host:port.",
            )
            return
        if not relay_addr and not session_or_host:
            messagebox.showerror(
                "Direct connection",
                "Leave Relay blank and enter the host LAN IP address.",
            )
            return

        direct_host = session_or_host if not relay_addr else "0.0.0.0"
        receiver = ScreenReceiver(direct_host)
        forwarder = InputForwarder(direct_host)

        try:
            receiver.connect(
                session_id=session_or_host.upper() if relay_addr else "",
                relay_addr=relay_addr,
            )
        except (OSError, ValueError, ConnectionError) as exc:
            messagebox.showerror(
                "Connection failed", f"Could not connect to screen stream:\n{exc}"
            )
            return

        try:
            forwarder.connect(
                session_id=session_or_host.upper() if relay_addr else "",
                relay_addr=relay_addr,
            )
        except (OSError, ValueError, ConnectionError) as exc:
            receiver.stop()
            messagebox.showerror(
                "Connection failed", f"Could not connect to input channel:\n{exc}"
            )
            return

        SessionWindow(self, receiver, forwarder)

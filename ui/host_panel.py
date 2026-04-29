import socket
import customtkinter as ctk

import config

_BG = "#0f172a"
_CARD = "#1e293b"
_TEXT = "#e2e8f0"
_MUTED = "#94a3b8"
_ACCENT = "#38bdf8"


class HostPanel(ctk.CTkToplevel):
    """
    Shows the hosting status, local IP address, and ports so the remote user
    knows how to connect.  Provides a Stop button to tear down the session.
    """

    def __init__(self, master, screen_server, input_executor, relay_addr: str = "") -> None:
        super().__init__(master)
        self.title("Appdesk – Hosting")
        self.resizable(False, False)
        self._screen_server = screen_server
        self._input_executor = input_executor
        self._relay_addr = relay_addr.strip()
        self._session_var = ctk.StringVar(value=screen_server.session_id or "-")
        self._status_var = ctk.StringVar(value="Waiting for connection…")
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._stop)

        # Callbacks are invoked from the ScreenServer background thread;
        # self.after() marshals them safely onto the Tk main thread.
        screen_server.on_client_connected = self._on_connected
        screen_server.on_client_disconnected = self._on_disconnected

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        outer = ctk.CTkFrame(self)
        outer.pack(padx=24, pady=20)

        ctk.CTkLabel(outer, text="Session Active", font=("Times New Roman", 16, "bold")).pack(
            pady=(0, 12)
        )

        grid = ctk.CTkFrame(outer)
        grid.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(grid, text="Your IP(s):").grid(row=0, column=0, sticky="nw")
        ctk.CTkLabel(
            grid,
            text="\n".join(self._local_ips()),
            font=("Times New Roman", 11, "bold"),
            justify="left",
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))

        ctk.CTkLabel(grid, text="Ports:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ctk.CTkLabel(
            grid, text=f"{config.SCREEN_PORT}  /  {config.INPUT_PORT}"
        ).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(4, 0))

        if self._relay_addr:
            ctk.CTkLabel(grid, text="Relay:").grid(row=2, column=0, sticky="w", pady=(4, 0))
            ctk.CTkLabel(grid, text=self._relay_addr).grid(
                row=2, column=1, sticky="w", padx=(10, 0), pady=(4, 0)
            )
            ctk.CTkLabel(grid, text="Session ID:").grid(row=3, column=0, sticky="w", pady=(4, 0))
            ctk.CTkLabel(grid, textvariable=self._session_var, font=("Times New Roman", 12, "bold")).grid(
                row=3, column=1, sticky="w", padx=(10, 0), pady=(4, 0)
            )
            ctk.CTkButton(
                grid,
                text="Copy Session ID",
                command=self._copy_session_id,
                width=160,
                fg_color="#9B5403",
                hover_color="#573107"
            ).grid(row=4, column=1, sticky="w", padx=(10, 0), pady=(8, 0))

        ctk.CTkLabel(outer, textvariable=self._status_var).pack(pady=(12, 12))

        ctk.CTkButton(
            outer,
            text="Stop Hosting",
            width=160,
            command=self._stop,
            fg_color="#9B5403",
            hover_color="#573107"
        ).pack()

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _local_ips() -> list:
        """Return every non-loopback IPv4 address on this machine."""
        ips = []
        try:
            _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
            ips = [a for a in addrs if not a.startswith("127.")]
        except Exception:
            pass
        # Fallback: UDP trick gives the preferred outbound interface
        if not ips:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    ips = [s.getsockname()[0]]
            except Exception:
                pass
        return ips or ["127.0.0.1"]

    # --------------------------------------------------------------- callbacks

    def _on_connected(self, addr: tuple) -> None:
        if self._relay_addr:
            self.after(0, lambda: self._status_var.set("Connected via relay"))
        else:
            self.after(0, lambda: self._status_var.set(f"Connected: {addr[0]}"))

    def _on_disconnected(self) -> None:
        self.after(0, lambda: self._status_var.set("Waiting for connection…"))

    def _copy_session_id(self) -> None:
        sid = self._session_var.get().strip()
        if sid and sid != "-":
            self.clipboard_clear()
            self.clipboard_append(sid)
            self._status_var.set("Session ID copied to clipboard")

    def _stop(self) -> None:
        self._screen_server.stop()
        self._input_executor.stop()
        self.destroy()

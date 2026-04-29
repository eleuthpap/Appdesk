from __future__ import annotations

import json
import socket

import config


class InputForwarder:
    """
    Connects to an InputExecutor on the host and sends newline-delimited JSON
    input events.

    Call connect() first, then use send_event() freely from the Tkinter thread.
    """

    def __init__(self, host: str) -> None:
        self._host = host
        self._sock: socket.socket | None = None

    # ------------------------------------------------------------------ public

    def connect(self, session_id: str = "", relay_addr: str = "") -> None:
        """Open TCP connection. Use relay when session_id+relay_addr are supplied."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        if session_id and relay_addr:
            host, port_str = relay_addr.rsplit(":", 1)
            self._sock.connect((host.strip(), int(port_str.strip())))
            from network.relay import handshake

            handshake(
                self._sock,
                {"role": "client", "channel": "input", "id": session_id.strip()},
            )
        else:
            self._sock.connect((self._host, config.INPUT_PORT))

    def stop(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    def send_event(self, event: dict) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendall((json.dumps(event) + "\n").encode("utf-8"))
        except OSError:
            pass

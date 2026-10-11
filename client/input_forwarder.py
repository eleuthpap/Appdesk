from __future__ import annotations

import json
import socket
import threading
from collections import deque

import config


class InputForwarder:
    """
    Connects to an InputExecutor on the host and sends newline-delimited JSON
    input events.

    Call connect() first, then queue events from the Tkinter thread. A worker
    sends them so network stalls cannot block screen rendering or local input.
    """

    def __init__(self, host: str) -> None:
        self._host = host
        self._sock: socket.socket | None = None
        self._events: deque[dict] = deque()
        self._condition = threading.Condition()
        self._running = False
        self._thread: threading.Thread | None = None

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
        self._running = True
        self._thread = threading.Thread(target=self._send_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._running = False
            self._events.clear()
            self._condition.notify_all()
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass

    def send_event(self, event: dict) -> None:
        with self._condition:
            if not self._running:
                return
            # Only the newest unsent move matters. Keep clicks, keys, scrolls,
            # and the order between those events unchanged.
            if event.get("type") == "move" and self._events and self._events[-1].get("type") == "move":
                self._events[-1] = event
            else:
                self._events.append(event)
            self._condition.notify()

    def _send_loop(self) -> None:
        sock = self._sock
        while True:
            with self._condition:
                while self._running and not self._events:
                    self._condition.wait()
                if not self._running:
                    return
                event = self._events.popleft()
            try:
                sock.sendall((json.dumps(event) + "\n").encode("utf-8"))
            except OSError:
                with self._condition:
                    self._running = False
                    self._events.clear()
                return

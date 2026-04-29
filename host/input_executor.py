from __future__ import annotations

import json
import socket
import threading

import mss
import pynput.keyboard
import pynput.mouse

import config


class InputExecutor:
    """
    Listens on INPUT_PORT for newline-delimited JSON input events from the
    client and executes them on the host machine via pynput.

    Event formats (all coords are relative floats 0.0–1.0):
        {"type":"move",   "x":0.5, "y":0.5}
        {"type":"click",  "x":0.5, "y":0.5, "button":"left"|"right", "pressed":true}
        {"type":"scroll", "x":0.5, "y":0.5, "dx":0, "dy":-3}
        {"type":"key",    "key":"a"|"enter"|..., "special":false|true, "pressed":true}
    """

    def __init__(self) -> None:
        self._running = False
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._relay_addr: str = ""
        self._session_id: str = ""
        self._mouse = pynput.mouse.Controller()
        self._keyboard = pynput.keyboard.Controller()
        self._screen_w, self._screen_h = self._get_screen_size()

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def start_relay(self, session_id: str, relay_addr: str) -> None:
        """Connect to relay as host on input channel and process client events."""
        self._session_id = session_id.strip()
        self._relay_addr = relay_addr.strip()
        self._running = True
        self._thread = threading.Thread(target=self._serve_relay, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass

    # ----------------------------------------------------------------- private

    @staticmethod
    def _get_screen_size() -> tuple[int, int]:
        with mss.mss() as sct:
            m = sct.monitors[1]
            return m["width"], m["height"]

    def _serve(self) -> None:
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("", config.INPUT_PORT))
        self._server_sock.listen(1)
        self._server_sock.settimeout(1.0)

        while self._running:
            try:
                conn, _ = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            try:
                self._handle_client(conn)
            finally:
                conn.close()

    def _serve_relay(self) -> None:
        from network.relay import handshake

        try:
            host, port_str = self._relay_addr.rsplit(":", 1)
            relay_target = (host.strip(), int(port_str.strip()))
        except (ValueError, TypeError):
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(relay_target)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            handshake(
                sock,
                {
                    "role": "host",
                    "channel": "input",
                    "id": self._session_id,
                },
            )
            self._handle_client(sock)
        except (OSError, ConnectionError):
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _handle_client(self, conn: socket.socket) -> None:
        buf = b""
        while self._running:
            try:
                chunk = conn.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line:
                    try:
                        self._execute(json.loads(line.decode("utf-8")))
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass

    def _execute(self, event: dict) -> None:
        etype = event.get("type")
        sw, sh = self._screen_w, self._screen_h

        if etype == "move":
            self._mouse.position = (int(event["x"] * sw), int(event["y"] * sh))

        elif etype == "click":
            self._mouse.position = (int(event["x"] * sw), int(event["y"] * sh))
            btn_name = event.get("button", "left")
            if btn_name == "right":
                btn = pynput.mouse.Button.right
            elif btn_name == "middle":
                btn = pynput.mouse.Button.middle
            else:
                btn = pynput.mouse.Button.left
            if event.get("pressed"):
                self._mouse.press(btn)
            else:
                self._mouse.release(btn)

        elif etype == "scroll":
            self._mouse.position = (int(event["x"] * sw), int(event["y"] * sh))
            self._mouse.scroll(int(event.get("dx", 0)), int(event.get("dy", 0)))

        elif etype == "key":
            key_str = event.get("key")
            if not key_str:
                return
            try:
                if event.get("special"):
                    key = pynput.keyboard.Key[key_str]
                else:
                    key = pynput.keyboard.KeyCode.from_char(key_str)
                if event.get("pressed"):
                    self._keyboard.press(key)
                else:
                    self._keyboard.release(key)
            except Exception:
                pass

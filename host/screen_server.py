from __future__ import annotations

import io
import socket
import struct
import threading
import time

import mss
from PIL import Image

import config

try:
    _RESAMPLE = Image.Resampling.BILINEAR
except AttributeError:          # Pillow < 9.1
    _RESAMPLE = Image.BILINEAR  # type: ignore[attr-defined]

# ── Windows cursor detection ───────────────────────────────────────────────────
# Maps Windows IDC cursor IDs → Tkinter cursor names
_IDC_TO_TK = {
    32512: "arrow",              # IDC_ARROW
    32513: "xterm",              # IDC_IBEAM  (text cursor)
    32514: "watch",              # IDC_WAIT
    32515: "crosshair",          # IDC_CROSS
    32516: "arrow",              # IDC_UPARROW
    32642: "sizing",             # IDC_SIZENWSE
    32643: "sizing",             # IDC_SIZENESW
    32644: "sb_h_double_arrow",  # IDC_SIZEWE
    32645: "sb_v_double_arrow",  # IDC_SIZENS
    32646: "fleur",              # IDC_SIZEALL  (move)
    32648: "no",                 # IDC_NO
    32649: "hand2",              # IDC_HAND  (pointer / link)
    32650: "watch",              # IDC_APPSTARTING
    32651: "question_arrow",     # IDC_HELP
}

try:
    import ctypes as _ctypes
    import ctypes.wintypes as _wt

    class _POINT(_ctypes.Structure):
        _fields_ = [("x", _wt.LONG), ("y", _wt.LONG)]

    class _CURSORINFO(_ctypes.Structure):
        _fields_ = [
            ("cbSize",     _wt.DWORD),
            ("flags",      _wt.DWORD),
            ("hCursor",    _wt.HANDLE),
            ("ptScreenPos", _POINT),
        ]

    # Pre-load the system cursor handles so we can compare at runtime
    _HANDLE_MAP: dict = {}
    for _idc, _tk in _IDC_TO_TK.items():
        _h = _ctypes.windll.user32.LoadCursorW(None, _idc)
        if _h:
            _HANDLE_MAP[_h] = _tk

    def _current_cursor() -> str:
        info = _CURSORINFO()
        info.cbSize = _ctypes.sizeof(_CURSORINFO)
        _ctypes.windll.user32.GetCursorInfo(_ctypes.byref(info))
        return _HANDLE_MAP.get(info.hCursor, "arrow")

except Exception:
    def _current_cursor() -> str:  # type: ignore[misc]
        return "arrow"


class ScreenServer:
    """
    Captures the primary screen and streams JPEG frames to one TCP client.

    Protocol (SCREEN_PORT) per frame:
        [4-byte big-endian JPEG size][JPEG bytes]
        [1-byte cursor-name length][cursor name ASCII]
        [8-byte big-endian double: echoed client acknowledgement time]
    The client acknowledges each complete frame with its monotonic timestamp.
    An initial value of -1 means no acknowledgement has arrived yet. At most two
    frames may be in flight, so a slow link cannot build a stale backlog.
    """

    def __init__(self, relay_addr: str = "") -> None:
        self._relay_addr = relay_addr.strip()
        self.session_id: str = ""
        self.on_session_ready = None      # callback(session_id: str) for relay mode
        self.on_client_connected = None     # callback(addr: tuple) — thread-safe via after()
        self.on_client_disconnected = None  # callback()
        self._running = False
        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._client_sock:
            try:
                self._client_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass

    # ----------------------------------------------------------------- private

    def _serve(self) -> None:
        if self._relay_addr:
            self._serve_relay()
        else:
            self._serve_direct()

    def _serve_direct(self) -> None:
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("", config.SCREEN_PORT))
        self._server_sock.listen(1)
        self._server_sock.settimeout(1.0)

        while self._running:
            try:
                conn, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

            if self.on_client_connected:
                self.on_client_connected(addr)
            try:
                self._stream_to(conn)
            finally:
                conn.close()
            if self.on_client_disconnected:
                self.on_client_disconnected()

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
            resp = handshake(sock, {"role": "host", "channel": "screen"})
            self.session_id = resp["id"]
            print(f"[HOST SCREEN] Handshake complete! Session ID: {self.session_id}")
            print(f"[HOST SCREEN] Registered session ID: {self.session_id}")
            if self.on_session_ready:
                self.on_session_ready(self.session_id)

            print("[HOST SCREEN] Waiting for client to connect before streaming...")
            # Wait for relay signal that client has connected
            ready_line = sock.recv(1024)
            if not ready_line or b"start" not in ready_line:
                print(f"[HOST SCREEN] Did not receive valid start signal: {ready_line}")
                return

            print("[HOST SCREEN] Client connected! Starting stream...")
            if self.on_client_connected:
                self.on_client_connected(("relay", 0))
            self._stream_to(sock)
        except Exception as exc:
            print(f"[HOST SCREEN ERROR] Relay connection failed or dropped: {exc}")
            import traceback
            traceback.print_exc()
        finally:
            print("[HOST SCREEN] Closing socket.")
            try:
                sock.close()
            except OSError:
                pass
        if self.on_client_disconnected:
            self.on_client_disconnected()

    def _stream_to(self, conn: socket.socket) -> None:
        self._client_sock = conn
        try:
            self._send_frames(conn)
        finally:
            self._client_sock = None

    def _send_frames(self, conn: socket.socket) -> None:
        interval = 1.0 / config.FPS
        frames_sent = 0
        last_ack_time = -1.0
        print("[HOST STREAM] Starting screen capture and stream...")
        with mss.mss() as sct:
            monitor = sct.monitors[1]   # primary monitor
            print(f"[HOST STREAM] Primary monitor size: {monitor['width']}x{monitor['height']}")
            while self._running:
                # Limit frames in flight so a slow connection cannot accumulate
                # seconds of old images in TCP buffers.
                if frames_sent >= 2:
                    try:
                        ack = bytearray(8)
                        view = memoryview(ack)
                        received = 0
                        while received < len(ack):
                            count = conn.recv_into(view[received:])
                            if count == 0:
                                raise ConnectionError("Client disconnected")
                            received += count
                        last_ack_time = struct.unpack(">d", ack)[0]
                    except (OSError, ConnectionError):
                        break
                t0 = time.monotonic()

                # Capture → PIL → resize → JPEG
                raw = sct.grab(monitor)
                img = Image.frombytes("RGB", raw.size, bytes(raw.raw), "raw", "BGRX")
                if img.size != (config.STREAM_WIDTH, config.STREAM_HEIGHT):
                    img = img.resize((config.STREAM_WIDTH, config.STREAM_HEIGHT), _RESAMPLE)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=config.JPEG_QUALITY)
                data = buf.getvalue()

                # Echo the client's clock value so it can measure a frame
                # response without comparing clocks on different computers.
                cursor = _current_cursor().encode("ascii")
                ack_time = struct.pack(">d", last_ack_time)

                # Send: [4-byte JPEG size][JPEG][1-byte cursor len][cursor][8-byte echoed time]
                try:
                    conn.sendall(
                        struct.pack(">L", len(data)) + data
                        + struct.pack("B", len(cursor)) + cursor
                        + ack_time
                    )
                    frames_sent += 1
                    if frames_sent <= 5 or frames_sent % 100 == 0:
                        print(f"[HOST STREAM] Sent frame #{frames_sent} ({len(data)} bytes)")
                except OSError as exc:
                    print(f"[HOST STREAM ERROR] Socket error while sending: {exc}")
                    break

                # Throttle to target FPS
                remaining = interval - (time.monotonic() - t0)
                if remaining > 0:
                    time.sleep(remaining)

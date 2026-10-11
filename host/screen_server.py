from __future__ import annotations

import io
import select
import socket
import struct
import threading
import time
from collections import deque

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
    An initial value of -1 means no acknowledgement has arrived yet. The
    sender adapts its in-flight frame limit to the connection's response time.
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
        self.stream_stats: tuple[float, float, float, float, int, float] | None = None

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
        self.stream_stats = None
        last_ack_time = -1.0
        last_rtt = 0.0
        pending: deque[float] = deque()
        condition = threading.Condition()
        window = 2
        min_rtt: float | None = None
        good_acks = 0
        ack_closed = False
        ack_stop = threading.Event()

        def read_acks() -> None:
            nonlocal last_ack_time, last_rtt, window, min_rtt, good_acks, ack_closed
            try:
                while self._running and not ack_stop.is_set():
                    ack = bytearray(8)
                    view = memoryview(ack)
                    received = 0
                    while received < len(ack):
                        ready, _, _ = select.select([conn], [], [], 0.2)
                        if not ready:
                            if ack_stop.is_set() or not self._running:
                                return
                            continue
                        count = conn.recv_into(view[received:])
                        if count == 0:
                            raise ConnectionError("Client disconnected")
                        received += count
                    now = time.monotonic()
                    with condition:
                        if not pending:
                            raise ConnectionError("Unexpected frame acknowledgement")
                        rtt = now - pending.popleft()
                        last_rtt = rtt
                        last_ack_time = struct.unpack(">d", ack)[0]
                        min_rtt = rtt if min_rtt is None else min(min_rtt, rtt)
                        # Grow the pipeline on a clear link; back off when the
                        # response exceeds the best observed time by enough to
                        # indicate that frames are waiting in a buffer.
                        if rtt > min_rtt + max(0.1, min_rtt * 0.25):
                            window = max(2, window - 2)
                            good_acks = 0
                        else:
                            good_acks += 1
                            if good_acks >= max(2, window // 2):
                                window = min(config.MAX_IN_FLIGHT_FRAMES, window + 1)
                                good_acks = 0
                        condition.notify_all()
            except (OSError, ConnectionError, ValueError):
                pass
            finally:
                with condition:
                    ack_closed = True
                    condition.notify_all()

        ack_thread = threading.Thread(target=read_acks, daemon=True)
        ack_thread.start()
        print("[HOST STREAM] Starting screen capture and stream...")
        sample_start = time.monotonic()
        sample_frames = 0
        sample_bytes = 0
        sample_encode = 0.0
        sample_send = 0.0
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]   # primary monitor
                print(f"[HOST STREAM] Primary monitor size: {monitor['width']}x{monitor['height']}")
                while self._running:
                    with condition:
                        while len(pending) >= window and not ack_closed and self._running:
                            condition.wait(timeout=0.5)
                        if ack_closed or not self._running:
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
                    encoded_at = time.monotonic()

                    # Echo the client's clock value so it can measure a frame
                    # response without comparing clocks on different computers.
                    cursor = _current_cursor().encode("ascii")
                    with condition:
                        if ack_closed or not self._running:
                            break
                        ack_time = struct.pack(">d", last_ack_time)
                        pending.append(time.monotonic())

                    # Send: [4-byte JPEG size][JPEG][1-byte cursor len][cursor][8-byte echoed time]
                    try:
                        send_started = time.monotonic()
                        conn.sendall(
                            struct.pack(">L", len(data)) + data
                            + struct.pack("B", len(cursor)) + cursor
                            + ack_time
                        )
                        sent_at = time.monotonic()
                        frames_sent += 1
                        sample_frames += 1
                        sample_bytes += len(data)
                        sample_encode += encoded_at - t0
                        sample_send += sent_at - send_started
                        elapsed = sent_at - sample_start
                        if elapsed >= 2.0:
                            with condition:
                                current_window = window
                                current_rtt = last_rtt
                            self.stream_stats = (
                                sample_frames / elapsed,
                                sample_encode * 1000 / sample_frames,
                                sample_send * 1000 / sample_frames,
                                sample_bytes / 1024 / sample_frames,
                                current_window,
                                current_rtt * 1000,
                            )
                            sample_start = sent_at
                            sample_frames = 0
                            sample_bytes = 0
                            sample_encode = 0.0
                            sample_send = 0.0
                        if frames_sent <= 5 or frames_sent % 100 == 0:
                            print(f"[HOST STREAM] Sent frame #{frames_sent} ({len(data)} bytes)")
                    except OSError as exc:
                        print(f"[HOST STREAM ERROR] Socket error while sending: {exc}")
                        break

                    # Throttle to target FPS
                    remaining = interval - (time.monotonic() - t0)
                    if remaining > 0:
                        time.sleep(remaining)
        finally:
            self.stream_stats = None
            ack_stop.set()
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            ack_thread.join(timeout=1.0)

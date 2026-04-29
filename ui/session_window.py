from __future__ import annotations

import queue
import time
import tkinter as tk
import customtkinter as ctk
from collections import deque
from tkinter import messagebox

from PIL import Image, ImageTk

import config

try:
    _RESAMPLE = Image.Resampling.BILINEAR
except AttributeError:          # Pillow < 9.1
    _RESAMPLE = Image.BILINEAR  # type: ignore[attr-defined]

# Maps Tkinter keysym names → pynput Key names for non-printable keys
_SPECIAL: dict[str, str] = {
    "Return": "enter",      "BackSpace": "backspace",  "Tab": "tab",
    "Escape": "esc",        "Delete": "delete",        "Insert": "insert",
    "Home": "home",         "End": "end",
    "Prior": "page_up",     "Next": "page_down",
    "Up": "up",             "Down": "down",
    "Left": "left",         "Right": "right",
    "Shift_L": "shift",     "Shift_R": "shift_r",
    "Control_L": "ctrl",    "Control_R": "ctrl_r",
    "Alt_L": "alt",         "Alt_R": "alt_r",
    "space": "space",       "caps_lock": "caps_lock",
    "super_l": "cmd",       "super_r": "cmd_r",
    "F1": "f1",  "F2": "f2",  "F3": "f3",  "F4": "f4",
    "F5": "f5",  "F6": "f6",  "F7": "f7",  "F8": "f8",
    "F9": "f9",  "F10": "f10", "F11": "f11", "F12": "f12",
}


class SessionWindow(ctk.CTkToplevel):
    """
    Displays the remote screen on a resizable Canvas and forwards all mouse /
    keyboard events back to the host via InputForwarder.
    """

    def __init__(self, master, receiver, forwarder) -> None:
        super().__init__(master)
        self.title("Appdesk – Remote Session")
        self._receiver = receiver
        self._forwarder = forwarder
        self._photo: ImageTk.PhotoImage | None = None   # prevent GC
        self._image_item: int | None = None
        self._canvas_w = config.STREAM_WIDTH
        self._canvas_h = config.STREAM_HEIGHT
        self._frame_times: deque = deque()   # timestamps of recent frames for FPS
        self._last_cursor: str = "arrow"
        self._last_move_sent = 0.0
        self._move_interval = 1.0 / max(1, int(config.MOUSE_MOVE_MAX_HZ))
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._receiver.start()
        self.after(100, self._focus_remote_input)
        self.after(config.UI_POLL_MS, self._update_frame)

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        self._canvas = tk.Canvas(
            self,
            width=config.STREAM_WIDTH,
            height=config.STREAM_HEIGHT,
            bg="black",
            cursor="crosshair",
            highlightthickness=0,
            takefocus=True,
        )
        self._canvas.pack(fill="both", expand=True)

        self._canvas.bind("<Configure>",      self._on_resize)
        self._canvas.bind("<Enter>",          self._focus_remote_input)
        # <Motion> fires only when NO button is held; Bx-Motion covers held-drag
        self._canvas.bind("<Motion>",         self._on_mouse_move)
        self._canvas.bind("<B1-Motion>",      self._on_mouse_move)
        self._canvas.bind("<B2-Motion>",      self._on_mouse_move)
        self._canvas.bind("<B3-Motion>",      self._on_mouse_move)
        self._canvas.bind("<ButtonPress>",    self._on_mouse_press)
        self._canvas.bind("<ButtonRelease>",  self._on_mouse_release)
        self._canvas.bind("<MouseWheel>",     self._on_scroll)
        self._canvas.bind("<KeyPress>",       self._on_key_press)
        self._canvas.bind("<KeyRelease>",     self._on_key_release)
        self.bind("<KeyPress>",               self._on_key_press)
        self.bind("<KeyRelease>",             self._on_key_release)

    # ------------------------------------------------------------------ frame

    def _update_frame(self) -> None:
        try:
            img, cursor_name, sent_time = self._receiver.frame_queue.get_nowait()
            # Always render the freshest frame to avoid display lag under load.
            while True:
                try:
                    img, cursor_name, sent_time = self._receiver.frame_queue.get_nowait()
                except queue.Empty:
                    break
            now = time.time()

            # FPS: count frames that arrived within the last second
            self._frame_times.append(now)
            while self._frame_times and self._frame_times[0] < now - 1.0:
                self._frame_times.popleft()
            fps = len(self._frame_times)

            # Latency estimate (accurate on loopback; requires NTP sync on LAN)
            latency_ms = max(0, int((now - sent_time) * 1000))

            self.title(f"Appdesk – Remote Session  |  {fps} FPS  |  {latency_ms} ms")

            # Sync cursor shape to host cursor
            if cursor_name != self._last_cursor:
                self._last_cursor = cursor_name
                self._canvas.config(cursor=cursor_name)

            if (
                self._canvas_w > 0
                and self._canvas_h > 0
                and img.size != (self._canvas_w, self._canvas_h)
            ):
                img = img.resize((self._canvas_w, self._canvas_h), _RESAMPLE)
            self._photo = ImageTk.PhotoImage(img)
            if self._image_item is None:
                self._image_item = self._canvas.create_image(
                    0, 0, anchor="nw", image=self._photo
                )
            else:
                self._canvas.itemconfig(self._image_item, image=self._photo)
        except queue.Empty:
            pass

        if self._receiver._running:
            self.after(config.UI_POLL_MS, self._update_frame)
        else:
            self.after(0, self._on_disconnect)

    # ----------------------------------------------------------------- events

    def _on_resize(self, event: tk.Event) -> None:
        self._canvas_w = event.width
        self._canvas_h = event.height

    def _focus_remote_input(self, event: tk.Event | None = None) -> None:
        self.lift()
        self._canvas.focus_force()

    def _rel(self, event: tk.Event) -> tuple[float, float]:
        """Convert canvas pixel coords to relative 0.0–1.0 values."""
        w = self._canvas_w or 1
        h = self._canvas_h or 1
        return event.x / w, event.y / h

    def _on_mouse_move(self, event: tk.Event) -> None:
        now = time.monotonic()
        if now - self._last_move_sent < self._move_interval:
            return
        self._last_move_sent = now
        rx, ry = self._rel(event)
        self._forwarder.send_event({"type": "move", "x": rx, "y": ry})

    def _on_mouse_press(self, event: tk.Event) -> None:
        self._focus_remote_input()
        rx, ry = self._rel(event)
        btn = "right" if event.num == 3 else "middle" if event.num == 2 else "left"
        self._forwarder.send_event(
            {"type": "click", "x": rx, "y": ry, "button": btn, "pressed": True}
        )

    def _on_mouse_release(self, event: tk.Event) -> None:
        rx, ry = self._rel(event)
        btn = "right" if event.num == 3 else "middle" if event.num == 2 else "left"
        self._forwarder.send_event(
            {"type": "click", "x": rx, "y": ry, "button": btn, "pressed": False}
        )

    def _on_scroll(self, event: tk.Event) -> None:
        rx, ry = self._rel(event)
        dy = event.delta // 120     # Windows: delta is multiples of 120
        self._forwarder.send_event(
            {"type": "scroll", "x": rx, "y": ry, "dx": 0, "dy": dy}
        )

    def _key_event(self, event: tk.Event, pressed: bool) -> None:
        keysym = event.keysym
        if keysym in _SPECIAL:
            self._forwarder.send_event(
                {"type": "key", "key": _SPECIAL[keysym], "special": True, "pressed": pressed}
            )
        elif event.char and event.char.isprintable():
            self._forwarder.send_event(
                {"type": "key", "key": event.char, "special": False, "pressed": pressed}
            )

    def _on_key_press(self, event: tk.Event) -> str:
        self._key_event(event, True)
        return "break"

    def _on_key_release(self, event: tk.Event) -> str:
        self._key_event(event, False)
        return "break"

    # -------------------------------------------------------------- lifecycle

    def _on_disconnect(self) -> None:
        messagebox.showwarning("Appdesk", "The connection to the host was lost.")
        self._close()

    def _close(self) -> None:
        self._receiver.stop()
        self._forwarder.stop()
        self.destroy()

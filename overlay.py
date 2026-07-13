"""Floating voice overlay — a Wispr-Flow-style pill at the bottom of the screen
showing a live waveform while dictating and pulsing dots while processing."""

import collections
import ctypes
import math
import tkinter as tk

_MAGIC = "#010203"  # color key rendered fully transparent
W, H = 280, 64
_PILL_H = 44
_PILL_COLOR = "#1e1e28"
_BAR_COLOR = "#e8564a"
_DOT_COLOR = "#f3a712"


class Overlay:
    """Owns a hidden always-on-top Tk window; must be created and run() on one thread
    (the main thread). Other threads only call set_mode()."""

    def __init__(self, recorder, quit_event):
        self._recorder = recorder
        self._quit = quit_event
        self.mode = "hidden"  # hidden | listening | processing
        self._visible = False
        self._levels = collections.deque([0.0] * 36, maxlen=36)
        self._phase = 0

        self.root = tk.Tk()
        self.root.overrideredirect(True)  # no title bar / borders
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", _MAGIC)
        self.root.config(bg=_MAGIC)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw - W) // 2}+{sh - H - 80}")
        self.canvas = tk.Canvas(self.root, width=W, height=H, bg=_MAGIC, highlightthickness=0)
        self.canvas.pack()
        self.root.withdraw()
        self._prevent_focus_steal()

    def _prevent_focus_steal(self):
        """The paste goes to the focused app — the overlay must NEVER take focus.
        WS_EX_NOACTIVATE stops activation; WS_EX_TOOLWINDOW hides it from Alt-Tab."""
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x80
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            )
        except Exception:
            pass

    # --- thread-safe API (called from worker/hotkey threads) ---

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    # --- drawing (tk thread only) ---

    def _draw_pill(self):
        c = self.canvas
        c.delete("all")
        r = _PILL_H // 2
        y0, y1 = (H - _PILL_H) // 2, (H + _PILL_H) // 2
        c.create_oval(0, y0, 2 * r, y1, fill=_PILL_COLOR, outline="")
        c.create_oval(W - 2 * r, y0, W, y1, fill=_PILL_COLOR, outline="")
        c.create_rectangle(r, y0, W - r, y1, fill=_PILL_COLOR, outline="")

    def _draw_listening(self):
        self._draw_pill()
        self._levels.append(getattr(self._recorder, "level", 0.0))
        pad = _PILL_H // 2 + 4
        span = W - 2 * pad
        n = len(self._levels)
        bw = span / n
        mid = H / 2
        for i, lvl in enumerate(self._levels):
            # sqrt for perceptual scaling; 0.12 RMS ~= loud speech
            h = 3 + 26 * min(1.0, (lvl / 0.12) ** 0.5)
            x = pad + i * bw
            self.canvas.create_rectangle(
                x, mid - h / 2, x + bw - 2, mid + h / 2, fill=_BAR_COLOR, outline=""
            )

    def _draw_processing(self):
        self._draw_pill()
        self._phase += 1
        for i in range(3):
            s = 4 + 3 * (1 + math.sin(self._phase / 3 + i * 2.1)) / 2
            cx = W / 2 + (i - 1) * 24
            self.canvas.create_oval(cx - s, H / 2 - s, cx + s, H / 2 + s, fill=_DOT_COLOR, outline="")

    def _tick(self):
        if self._quit.is_set():
            self.root.destroy()
            return
        mode = self.mode
        if mode == "hidden":
            if self._visible:
                self.root.withdraw()
                self._visible = False
                self._levels.extend([0.0] * self._levels.maxlen)
        else:
            if not self._visible:
                self.root.deiconify()
                self._prevent_focus_steal()
                self._visible = True
            if mode == "listening":
                self._draw_listening()
            else:
                self._draw_processing()
        self.root.after(40, self._tick)  # ~25 fps; also lets Ctrl+C handlers run

    def run(self) -> None:
        """Blocks the calling thread until quit_event is set."""
        self._tick()
        self.root.mainloop()

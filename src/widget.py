"""
MeetingRecorder Widget — floating controls for meeting recording.
Shows recording status with elapsed time.
Buttons: [⏺] [⏹] [_] [✕]
"""
import tkinter as tk
import time

# Colors
COLOR_BG = "#1a1a2e"
COLOR_CAPTION_BG = "#0f0f23"
COLOR_RECORDING = "#e74c3c"
COLOR_IDLE = "#2ecc71"
COLOR_BTN = "#16213e"
COLOR_TEXT = "#eaeaea"
COLOR_CAPTION_TEXT = "#ffffff"
COLOR_TITLEBAR = "#0f0f1a"
COLOR_TIMER = "#e74c3c"

WIDGET_WIDTH = 350
WIDGET_HEIGHT = 80


class RecorderWidget:
    """
    Floating widget for MeetingRecorder.
    Shows recording status with elapsed time and Start/Stop controls.

    Callbacks:
        on_start():  User clicked Start
        on_stop():   User clicked Stop
    """

    def __init__(self, on_start, on_stop):
        self.on_start = on_start
        self.on_stop = on_stop
        self._recording = False
        self._record_start = None
        self._timer_id = None

        self.window = tk.Tk()
        self.window.title("MeetingRecorder")
        self.window.geometry(f"{WIDGET_WIDTH}x{WIDGET_HEIGHT}")
        self.window.overrideredirect(True)
        self.window.wm_attributes("-topmost", True)
        self.window.wm_attributes("-alpha", 0.92)
        self.window.configure(bg=COLOR_BG)

        # Position: bottom-right of screen
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        self.window.geometry(f"+{screen_w - WIDGET_WIDTH - 20}+{screen_h - WIDGET_HEIGHT - 60}")

        # ── Title bar (draggable) ──
        titlebar = tk.Frame(self.window, bg=COLOR_TITLEBAR, height=30)
        titlebar.pack(fill=tk.X, side=tk.TOP)
        titlebar.pack_propagate(False)

        # Status dot
        self.status_canvas = tk.Canvas(titlebar, width=12, height=12,
                                        bg=COLOR_TITLEBAR, highlightthickness=0)
        self.status_canvas.pack(side=tk.LEFT, padx=(8, 4), pady=9)
        self.status_dot = self.status_canvas.create_oval(2, 2, 10, 10,
                                                          fill=COLOR_IDLE, outline="")

        # App label
        tk.Label(titlebar, text="MeetingRecorder", font=("Segoe UI", 8),
                 bg=COLOR_TITLEBAR, fg="#888").pack(side=tk.LEFT, padx=(2, 0), pady=4)

        # Start/Stop buttons
        self.btn_start = tk.Button(titlebar, text="\u23FA", width=3,
                                   font=("Segoe UI", 10),
                                   bg=COLOR_BTN, fg=COLOR_TEXT, relief=tk.FLAT,
                                   command=self._on_start)
        self.btn_start.pack(side=tk.LEFT, padx=(8, 1), pady=4)

        self.btn_stop = tk.Button(titlebar, text="\u23F9", width=3,
                                  font=("Segoe UI", 10),
                                  bg=COLOR_BTN, fg=COLOR_TEXT, relief=tk.FLAT,
                                  state=tk.DISABLED,
                                  command=self._on_stop)
        self.btn_stop.pack(side=tk.LEFT, padx=1, pady=4)

        # Window control buttons (right side)
        self.btn_close = tk.Button(titlebar, text="\u2715", width=2,
                                   font=("Segoe UI", 8),
                                   bg=COLOR_TITLEBAR, fg="#888", relief=tk.FLAT,
                                   command=self.hide)
        self.btn_close.pack(side=tk.RIGHT, padx=(0, 4), pady=4)

        self.btn_minimize = tk.Button(titlebar, text="\u2500", width=2,
                                      font=("Segoe UI", 8),
                                      bg=COLOR_TITLEBAR, fg="#888", relief=tk.FLAT,
                                      command=self._minimize)
        self.btn_minimize.pack(side=tk.RIGHT, padx=0, pady=4)

        # Drag support on title bar
        titlebar.bind("<ButtonPress-1>", self._start_move)
        titlebar.bind("<B1-Motion>", self._do_move)

        # ── Status display area ──
        status_frame = tk.Frame(self.window, bg=COLOR_CAPTION_BG)
        status_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))

        self.status_label = tk.Label(status_frame,
                                      text="Idle — waiting for mic activity",
                                      bg=COLOR_CAPTION_BG,
                                      fg="#888",
                                      font=("Segoe UI", 11),
                                      anchor="w",
                                      padx=10, pady=8)
        self.status_label.pack(fill=tk.BOTH, expand=True)

        # Minimized state tracking
        self._minimized = False
        self._full_height = WIDGET_HEIGHT

    def _on_start(self):
        self.set_recording(True)
        self.on_start()

    def _on_stop(self):
        self.set_recording(False)
        self.on_stop()

    def set_recording(self, recording: bool):
        """Update UI to reflect recording state."""
        self._recording = recording
        color = COLOR_RECORDING if recording else COLOR_IDLE
        self.status_canvas.itemconfig(self.status_dot, fill=color)

        if recording:
            self.btn_start.configure(state=tk.DISABLED)
            self.btn_stop.configure(state=tk.NORMAL)
            self._record_start = time.time()
            self._update_timer()
        else:
            self.btn_start.configure(state=tk.NORMAL)
            self.btn_stop.configure(state=tk.DISABLED)
            self._record_start = None
            if self._timer_id:
                self.window.after_cancel(self._timer_id)
                self._timer_id = None
            self.status_label.configure(text="Idle — waiting for mic activity", fg="#888")

    def set_status(self, text: str):
        """Set a custom status message."""
        self.status_label.configure(text=text, fg=COLOR_CAPTION_TEXT)

    @property
    def is_recording(self):
        return self._recording

    def _update_timer(self):
        """Update the recording timer display."""
        if self._record_start and self._recording:
            elapsed = int(time.time() - self._record_start)
            mins = elapsed // 60
            secs = elapsed % 60
            self.status_label.configure(
                text=f"Recording  {mins:02d}:{secs:02d}",
                fg=COLOR_TIMER,
            )
            self._timer_id = self.window.after(1000, self._update_timer)

    def show(self):
        """Show the widget window."""
        self.window.deiconify()

    def hide(self):
        """Hide the widget window."""
        self.window.withdraw()

    def _minimize(self):
        """Toggle between minimized (title bar only) and full view."""
        if self._minimized:
            self.window.geometry(f"{WIDGET_WIDTH}x{self._full_height}")
            self._minimized = False
        else:
            self.window.geometry(f"{WIDGET_WIDTH}x30")
            self._minimized = True

    def destroy(self):
        """Destroy the widget."""
        self.window.destroy()

    def _start_move(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _do_move(self, event):
        x = self.window.winfo_x() + event.x - self._drag_x
        y = self.window.winfo_y() + event.y - self._drag_y
        self.window.geometry(f"+{x}+{y}")

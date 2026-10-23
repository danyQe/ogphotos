"""Native "choose folder" dialog. The server runs on the user's own machine, so a Tk dialog
opened here appears on their screen; browsers can't reveal a local folder's path."""

import threading
from pathlib import Path

_lock = threading.Lock()  # one dialog at a time


class PickerUnavailable(RuntimeError):
    pass


def pick_folder(initial: Path | None = None, title: str = "Choose a folder") -> Path | None:
    """Block until the user picks a folder (Path) or cancels (None)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as e:
        raise PickerUnavailable("this Python has no tkinter") from e
    if not _lock.acquire(blocking=False):
        raise PickerUnavailable("a folder dialog is already open")
    try:
        try:
            root = tk.Tk()
        except tk.TclError as e:  # e.g. no display on a headless Linux box
            raise PickerUnavailable(f"can't open a window: {e}") from e
        try:
            root.withdraw()
            root.attributes("-topmost", True)
            start = initial if initial and initial.is_dir() else Path.home()
            chosen = filedialog.askdirectory(
                parent=root, initialdir=str(start), mustexist=False, title=title
            )
        finally:
            root.destroy()
        return Path(chosen) if chosen else None
    finally:
        _lock.release()

"""Arrow-key picker for `ai auth` (stdlib only: msvcrt on Windows, termios elsewhere).

`arrow_select` returns None when the terminal cannot do it (no TTY, no ANSI support, no raw
mode); the caller then falls back to the numbered prompt. Ctrl+C and Esc raise KeyboardInterrupt
so `ai auth` reports "Cancelled" exactly as it does for the numbered prompt.
"""

from __future__ import annotations

import shutil
import sys
from typing import Callable, List, Optional, Tuple

UP, DOWN, ENTER, CANCEL = "up", "down", "enter", "cancel"

_HIDE, _SHOW, _CLEAR = "\x1b[?25l", "\x1b[?25h", "\x1b[2K"


def step(index: int, key: str, count: int) -> Tuple[int, bool]:
    """Apply one key to the highlighted index (0-based). Returns (index, done). Wraps around."""
    if key == UP:
        return (index - 1) % count, False
    if key == DOWN:
        return (index + 1) % count, False
    if key == ENTER:
        return index, True
    if key.isdigit() and 1 <= int(key) <= count:
        return int(key) - 1, False
    return index, False


def fit(text: str, width: int) -> str:
    """Cut plain text to fewer than `width` cells, so a line never wraps and redraw math stays exact."""
    limit = max(width - 1, 1)
    return text if len(text) <= limit else text[: max(limit - 3, 1)] + "..."


def render(prompt: str, options: List[str], index: int, width: int = 80) -> List[str]:
    lines = [fit(f"{prompt} (Up/Down to move, Enter to select):", width)]
    for i, text in enumerate(options):
        mark = ">" if i == index else " "
        line = fit(f"  {mark} {i + 1}) {text}", width)
        lines.append(f"\x1b[7m{line}\x1b[0m" if i == index else line)
    return lines


def _columns() -> int:
    return shutil.get_terminal_size((80, 24)).columns


# --------------------------------------------------------------------------- key readers

def _windows_keys() -> Optional[Callable[[], str]]:
    import msvcrt

    def read() -> str:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": UP, "P": DOWN}.get(msvcrt.getwch(), "")
        if ch in ("\r", "\n"):
            return ENTER
        if ch in ("\x03", "\x1b"):
            return CANCEL
        return {"k": UP, "j": DOWN}.get(ch, ch)

    return read


def _posix_keys(fd: int) -> Callable[[], str]:
    import os
    import select

    def byte(timeout: Optional[float] = None) -> str:
        if timeout is not None and not select.select([fd], [], [], timeout)[0]:
            return ""
        return os.read(fd, 1).decode("utf-8", "ignore")

    def read() -> str:
        ch = byte()
        if ch == "\x1b":
            if byte(0.05) not in ("[", "O"):
                return CANCEL
            return {"A": UP, "B": DOWN}.get(byte(0.05), "")
        if ch in ("\r", "\n"):
            return ENTER
        if ch in ("\x03", "\x04"):
            return CANCEL
        return {"k": UP, "j": DOWN}.get(ch, ch)

    return read


def _enable_windows_ansi() -> bool:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(mode.value & 0x4 or kernel32.SetConsoleMode(handle, mode.value | 0x4))
    except Exception:
        return False


# --------------------------------------------------------------------------- picker

def _run(prompt: str, options: List[str], index: int, read: Callable[[], str], out,
         columns: Callable[[], int] = _columns) -> int:
    # Every line is cut to the terminal width (re-read on each draw), so one line is one row and
    # moving the cursor up `drawn` rows always lands on the first row of the previous frame.
    drawn = 0

    def draw(lines: List[str]) -> None:
        nonlocal drawn
        if drawn:
            out.write(f"\x1b[{drawn}F")
        out.write("".join(f"{_CLEAR}{line}\n" for line in lines))
        for _ in range(drawn - len(lines)):
            out.write(f"{_CLEAR}\n")
        extra = max(drawn - len(lines), 0)
        if extra:
            out.write(f"\x1b[{extra}F")
        drawn = len(lines)
        out.flush()

    out.write(_HIDE)
    try:
        while True:
            draw(render(prompt, options, index, columns()))
            key = read()
            if key == CANCEL:
                raise KeyboardInterrupt
            index, done = step(index, key, len(options))
            if done:
                draw([fit(f"{prompt}: {options[index]}", columns())])
                return index + 1
    finally:
        out.write(_SHOW)
        out.flush()


def arrow_select(prompt: str, options: List[str], default: int = 1) -> Optional[int]:
    """Return the chosen 1-based option, or None when this terminal cannot show the picker."""
    if not options or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    index = min(max(default, 1), len(options)) - 1
    if sys.platform == "win32":
        if not _enable_windows_ansi():
            return None
        return _run(prompt, options, index, _windows_keys(), sys.stdout)
    try:
        import termios
        import tty
    except ImportError:
        return None
    fd = sys.stdin.fileno()
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        return None
    try:
        tty.setcbreak(fd)
        return _run(prompt, options, index, _posix_keys(fd), sys.stdout)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)

"""Presentasi CLI dengan fallback teks biasa untuk pipe dan berkas."""
from __future__ import annotations

import os
import re
import sys


def clean(value: object) -> str:
    """Cegah karakter kontrol dari artefak mengubah tampilan terminal."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", str(value))


def color_available() -> bool:
    if not sys.stdout.isatty() or "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return False
    if os.name != "nt":
        return True
    # Enable virtual terminal processing in classic Windows consoles.
    import ctypes
    import msvcrt
    from ctypes import wintypes
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(sys.stdout.fileno()))
    mode = wintypes.DWORD()
    kernel = ctypes.windll.kernel32
    return bool(kernel.GetConsoleMode(handle, ctypes.byref(mode))
                and kernel.SetConsoleMode(handle, mode.value | 4))


def accent(value: str, style: str = "1;36") -> str:
    return f"\x1b[{style}m{value}\x1b[0m" if color_available() else value


def heading(title: str, *, level: int = 1) -> None:
    title = clean(title)
    marker = "=" if level == 1 else "-"
    label = f"{marker * 2} {title} {marker * 2}"
    print("\n" + accent(label))


def fields(*entries: tuple[str, object]) -> None:
    width = max(len(label) for label, _ in entries)
    for label, value in entries:
        print(f"  {label:<{width}}  {clean(value)}")


def table(headers, rows) -> None:
    headers = tuple(headers)
    rows = [tuple(clean(value) for value in row) for row in rows]
    if not rows:
        return
    columns = zip(headers, *rows)
    widths = [max(map(len, column)) for column in columns]

    def line(values):
        return "  " + "  ".join(
            f"{value:<{width}}" for value, width in zip(values, widths)
        ).rstrip()

    print(line(headers))
    print(line("-" * width for width in widths))
    for row in rows:
        print(line(row))

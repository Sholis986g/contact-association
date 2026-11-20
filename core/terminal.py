"""Presentasi CLI dengan fallback teks biasa untuk pipe dan berkas."""
from __future__ import annotations

import os
import re
import sys


def clean(value: object) -> str:
    """Cegah karakter kontrol dari artefak mengubah tampilan terminal."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", str(value))


def heading(title: str, *, level: int = 1) -> None:
    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ and (
        os.environ.get("TERM") != "dumb"
    ) and (os.name != "nt" or bool(os.environ.get("WT_SESSION")))
    title = clean(title)
    marker = "=" if level == 1 else "-"
    label = f"{marker * 2} {title} {marker * 2}"
    print(f"\n\x1b[1;36m{label}\x1b[0m" if color else f"\n{label}")


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

"""
Utilitas parsing timestamp, normalisasi zona waktu ke UTC,
dan penanganan rentang tanggal kalender inklusif (--from dan --to).
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import re
from typing import Optional, Tuple


WIB = timezone(timedelta(hours=7))
WITA = timezone(timedelta(hours=8))
WIT = timezone(timedelta(hours=9))

TZ_OFFSETS = {
    "WIB": WIB,
    "WITA": WITA,
    "WIT": WIT,
    "UTC": timezone.utc,
    "GMT": timezone.utc,
    "Z": timezone.utc,
}


def parse_datetime_to_utc(raw_dt: str | int | float | None) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Mengurai berbagai format tanggal/waktu.
    Returns:
        (timestamp_orig, timestamp_utc, tz_known)
    """
    if raw_dt is None:
        return None, None, False

    orig_str = str(raw_dt).strip()
    if not orig_str:
        return None, None, False

    # Cek Unix timestamp numerik (detik atau milidetik)
    if orig_str.isdigit() or (orig_str.replace('.', '', 1).isdigit() and '.' in orig_str):
        try:
            val = float(orig_str)
            # jika milidetik (> 10 digit)
            if val > 1e11:
                val /= 1000.0
            dt = datetime.fromtimestamp(val, tz=timezone.utc)
            return orig_str, dt.strftime("%Y-%m-%dT%H:%M:%SZ"), True
        except Exception:
            pass

    # Pembersihan string
    s = orig_str.replace("[", "").replace("]", "").strip()

    # Cek penanda zona waktu eksplisit (WIB/WITA/WIT)
    tz_override = None
    for tz_name, tz_obj in TZ_OFFSETS.items():
        if tz_name in s.upper():
            tz_override = tz_obj
            s = re.sub(rf'\b{tz_name}\b', '', s, flags=re.IGNORECASE).strip()
            break

    # Format-format yang umum didukung
    formats = [
        # ISO formats
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        # WhatsApp & Indonesian date formats (DD/MM/YYYY or DD/MM/YY)
        "%d/%m/%Y, %H:%M:%S",
        "%d/%m/%Y, %H:%M",
        "%d/%m/%y, %H:%M:%S",
        "%d/%m/%y, %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
        # Dash variants
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%y %H:%M:%S",
        "%d-%m-%y %H:%M",
        "%d-%m-%Y",
        # 12-hour AM/PM
        "%d/%m/%Y, %I:%M:%S %p",
        "%d/%m/%Y, %I:%M %p",
        "%d/%m/%y, %I:%M:%S %p",
        "%d/%m/%y, %I:%M %p",
        "%m/%d/%Y, %I:%M:%S %p",
        "%m/%d/%Y, %I:%M %p",
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            if tz_override:
                dt = dt.replace(tzinfo=tz_override)
                dt_utc = dt.astimezone(timezone.utc)
                return orig_str, dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), True
            elif dt.tzinfo is not None:
                dt_utc = dt.astimezone(timezone.utc)
                return orig_str, dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), True
            else:
                # Zona waktu tidak diketahui (asumsikan waktu lokal tanpa konversi sembrono)
                return orig_str, dt.strftime("%Y-%m-%dT%H:%M:%S"), False
        except ValueError:
            continue

    # Jika tidak dapat diparsing, simpan nilai asli saja
    return orig_str, None, False


def parse_date_boundary(date_str: str | None, is_end_date: bool = False) -> Tuple[Optional[str], Optional[datetime]]:
    """
    Mengurai batas --from dan --to.
    Untuk tanggal kalender 'YYYY-MM-DD', jika is_end_date=True,
    maka diperlakukan sebagai akhir inklusif pada hari itu (23:59:59.999999).
    """
    if not date_str:
        return None, None

    s = date_str.strip()
    # Cek format kalender YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        if is_end_date:
            dt = datetime.strptime(s + " 23:59:59", "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%dT23:59:59"), dt
        else:
            dt = datetime.strptime(s + " 00:00:00", "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%dT00:00:00"), dt

    # Format ISO lengkap atau YYYY-MM-DD HH:MM:SS
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S"), dt
        except ValueError:
            continue

    raise ValueError(f"Format batas tanggal tidak valid: '{date_str}'. Gunakan format YYYY-MM-DD atau ISO 8601.")

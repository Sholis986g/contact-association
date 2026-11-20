"""
Parser untuk berkas CSV (CDR, Call Log, SMS, dll.) berbasis profil pemetaan.
"""
from __future__ import annotations
import csv
import os
from typing import Any, Dict, List, Optional
from .base import BaseParser, ParseResult
from ..models import NormalizedEvent
from ..normalizer import try_normalize_phone
from ..time_utils import parse_datetime_to_utc


class CSVParser(BaseParser):
    parser_name = "CSVParser"
    parser_version = "1.0.0"

    def parse(
        self,
        file_path: str,
        profile: Dict[str, Any] | None = None,
        options: Dict[str, Any] | None = None,
    ) -> ParseResult:
        result = ParseResult()
        profile = profile or {}
        mapping = profile.get("mapping", {})
        default_event_type = profile.get("event_type", "CALL").upper()
        source_name = (options or {}).get("source_name") or os.path.basename(file_path)

        # Cek profil kolom
        col_party_a = mapping.get("party_a") or mapping.get("caller") or mapping.get("from") or "party_a"
        col_party_b = mapping.get("party_b") or mapping.get("callee") or mapping.get("to") or "party_b"
        col_time = mapping.get("timestamp") or mapping.get("date") or mapping.get("call_time") or "timestamp"
        col_type = mapping.get("event_type") or mapping.get("activity_type") or mapping.get("type")
        col_dir = mapping.get("direction") or "direction"
        col_status = mapping.get("status") or "status"
        col_dur = mapping.get("duration") or "duration"
        col_eid = mapping.get("event_id") or mapping.get("call_id") or mapping.get("id") or "event_id"
        col_thread = mapping.get("thread_id") or "thread_id"

        # Buka file dalam mode read-only
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            # Sniff dialect jika memungkinkan
            sample = f.read(4096)
            f.seek(0)
            delimiter = ","
            try:
                sniffer = csv.Sniffer()
                dialect = sniffer.sniff(sample)
                delimiter = dialect.delimiter
            except Exception:
                # Default comma, jika ada titik koma lebih banyak gunakan semicolon
                if sample.count(";") > sample.count(","):
                    delimiter = ";"

            reader = csv.DictReader(f, delimiter=delimiter)
            if not reader.fieldnames:
                result.rejected_records.append({
                    "ref": "line:1",
                    "reason": "Header CSV kosong atau berkas tidak valid"
                })
                return result

            # Standarisasi lookup fieldnames (case-insensitive)
            field_map = {fn.strip().lower(): fn for fn in reader.fieldnames if fn}

            def get_val(row_dict: dict, col_name: Optional[str]) -> Optional[str]:
                if not col_name:
                    return None
                c_low = col_name.strip().lower()
                actual_key = field_map.get(c_low)
                if actual_key and actual_key in row_dict:
                    val = row_dict[actual_key]
                    return str(val).strip() if val is not None else None
                return None

            line_no = 1  # Baris 1 adalah header
            for row in reader:
                line_no += 1
                result.total_rows += 1

                raw_a = get_val(row, col_party_a)
                raw_b = get_val(row, col_party_b)
                raw_time = get_val(row, col_time)
                raw_status = get_val(row, col_status) or "UNKNOWN"
                raw_dir = (get_val(row, col_dir) or "UNKNOWN").upper()
                raw_dur_str = get_val(row, col_dur)
                raw_eid = get_val(row, col_eid)
                raw_thread = get_val(row, col_thread)

                # Event type
                ev_type = default_event_type
                if col_type:
                    val_type = get_val(row, col_type)
                    if val_type:
                        val_up = val_type.upper()
                        if "SMS" in val_up or "MSG" in val_up or "CHAT" in val_up:
                            ev_type = "MESSAGE"
                        elif "CALL" in val_up or "VOICE" in val_up:
                            ev_type = "CALL"

                # Parse duration
                duration = None
                if raw_dur_str:
                    try:
                        duration = int(float(raw_dur_str))
                    except ValueError:
                        pass

                # Normalisasi nomor telepon party A dan party B
                canon_a, err_a = try_normalize_phone(raw_a) if raw_a else (None, "Nomor party A kosong")
                canon_b, err_b = try_normalize_phone(raw_b) if raw_b else (None, "Nomor party B kosong")

                # Setidaknya salah satu pihak harus valid nomor telepon Indonesia
                if not canon_a and not canon_b:
                    result.rejected_records.append({
                        "ref": f"line:{line_no}",
                        "reason": f"Kedua pihak tidak memiliki nomor valid: A='{raw_a}' ({err_a}), B='{raw_b}' ({err_b})"
                    })
                    continue

                # Parse timestamp
                orig_time, time_utc, tz_known = parse_datetime_to_utc(raw_time)

                # Normalisasi status panggilan jika tersedia
                norm_status = raw_status.upper()
                if "ANSWER" in norm_status or "TERJAWAB" in norm_status or (duration and duration > 0):
                    norm_status = "ANSWERED"
                elif "MISS" in norm_status or "TIDAK TERJAWAB" in norm_status:
                    norm_status = "MISSED"
                elif "ATTEMPT" in norm_status or "DIAL" in norm_status or "COBA" in norm_status:
                    norm_status = "ATTEMPTED"
                elif "BUSY" in norm_status or "SIBUK" in norm_status:
                    norm_status = "BUSY"

                # Normalisasi direction
                norm_dir = raw_dir
                if norm_dir.startswith("OUT") or "KELUAR" in norm_dir:
                    norm_dir = "OUTGOING"
                elif norm_dir.startswith("IN") or "MASUK" in norm_dir:
                    norm_dir = "INCOMING"
                else:
                    norm_dir = "UNKNOWN"

                event = NormalizedEvent(
                    event_type=ev_type,
                    source_name=source_name,
                    record_ref=f"line:{line_no}",
                    event_id=raw_eid,
                    timestamp_orig=orig_time,
                    timestamp_utc=time_utc,
                    tz_known=tz_known,
                    party_a=canon_a,
                    party_a_raw=raw_a,
                    party_b=canon_b,
                    party_b_raw=raw_b,
                    direction=norm_dir,
                    status=norm_status,
                    duration=duration,
                    thread_id=raw_thread,
                )
                result.events.append(event)

        return result

"""
Parser untuk berkas ekspor percakapan WhatsApp (.txt).
Mendukung percakapan pribadi (DIRECT_MESSAGE) dan percakapan grup (SHARED_GROUP).
Hanya mengekstrak metadata pengirim/peserta dan timestamp, tidak menyimpan isi percakapan.
"""
from __future__ import annotations
import os
import re
from typing import Any, Dict, List, Optional
from .base import BaseParser, ParseResult
from ..models import NormalizedEvent
from ..normalizer import try_normalize_phone
from ..time_utils import parse_datetime_to_utc


class WhatsAppParser(BaseParser):
    parser_name = "WhatsAppParser"
    parser_version = "1.0.0"

    # Regex untuk pola timestamp WhatsApp
    # Pola 1: [DD/MM/YY, HH:MM:SS] Sender: Message atau [YYYY-MM-DD HH:MM:SS] Sender: Message
    PATTERN_BRACKET = re.compile(r'^\[(\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4}[,\s]+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\]\s+([^:]+):\s+(.*)$')
    # Pola 2: DD/MM/YY, HH:MM - Sender: Message
    PATTERN_DASH = re.compile(r'^(\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4}[,\s]+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\s+-\s+([^:]+):\s+(.*)$')
    # Pola pesan sistem WhatsApp (tanpa titik dua pengirim)
    PATTERN_SYSTEM = re.compile(r'^(?:\[(\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4}[,\s]+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\]|\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4}[,\s]+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?\s+-)\s+(.*)$')

    def parse(
        self,
        file_path: str,
        profile: Dict[str, Any] | None = None,
        options: Dict[str, Any] | None = None,
    ) -> ParseResult:
        result = ParseResult()
        profile = profile or {}
        options = options or {}
        source_name = options.get("source_name") or os.path.basename(file_path)
        forced_group = options.get("is_group") if "is_group" in options else profile.get("is_group")
        group_id_opt = options.get("group_id") or profile.get("group_id")

        raw_messages: list[dict] = []
        is_group_detected = False

        # Baca file baris demi baris
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            line_no = 0
            for line in f:
                line_no += 1
                clean_line = line.strip("\r\n")
                if not clean_line:
                    continue

                # Coba cocokkan bracket format
                m_bracket = self.PATTERN_BRACKET.match(clean_line)
                m_dash = self.PATTERN_DASH.match(clean_line) if not m_bracket else None

                if m_bracket or m_dash:
                    m = m_bracket or m_dash
                    raw_time = m.group(1).strip()
                    sender = m.group(2).strip()
                    # Periksa jika pengirim adalah pesan sistem yang mengandung titik dua palsu
                    if "Messages and calls are end-to-end encrypted" in sender or "Pesan dan panggilan dienkripsi" in sender:
                        continue

                    raw_messages.append({
                        "line": line_no,
                        "time": raw_time,
                        "sender": sender,
                    })
                else:
                    # Cek apakah baris adalah pesan sistem grup (misal "added", "created group", "mengubah subjek")
                    m_sys = self.PATTERN_SYSTEM.match(clean_line)
                    if m_sys:
                        sys_text = m_sys.group(2).lower()
                        if any(kw in sys_text for kw in ("created group", "membuat grup", "added", "menambahkan", "left", "keluar", "changed group", "mengubah setelan")):
                            is_group_detected = True

        if forced_group is not None:
            is_group = bool(forced_group)
        else:
            # Jika terdeteksi kata kunci grup atau terdapat lebih dari 2 pengirim berbeda
            unique_senders = {msg["sender"] for msg in raw_messages}
            is_group = is_group_detected or (len(unique_senders) > 2)

        thread_name = os.path.splitext(os.path.basename(file_path))[0]
        actual_group_id = group_id_opt or (thread_name if is_group else None)

        for msg in raw_messages:
            result.total_rows += 1
            line_no = msg["line"]
            sender_raw = msg["sender"]
            time_raw = msg["time"]

            # Cek apakah sender adalah nomor telepon
            canon_sender, err_phone = try_normalize_phone(sender_raw)
            # Jika bukan nomor telepon (misal hanya nama kontak teks tanpa nomor),
            # per aturan: "Nama tampilan tanpa nomor yang bisa dicocokkan tetap belum terpetakan."
            contact_label = sender_raw if not canon_sender else None

            orig_time, time_utc, tz_known = parse_datetime_to_utc(time_raw)

            if is_group:
                event = NormalizedEvent(
                    event_type="GROUP_PARTICIPATION",
                    source_name=source_name,
                    record_ref=f"line:{line_no}",
                    timestamp_orig=orig_time,
                    timestamp_utc=time_utc,
                    tz_known=tz_known,
                    party_a=canon_sender,
                    party_a_raw=sender_raw,
                    group_id=actual_group_id,
                    contact_name=contact_label,
                )
            else:
                event = NormalizedEvent(
                    event_type="MESSAGE",
                    source_name=source_name,
                    record_ref=f"line:{line_no}",
                    timestamp_orig=orig_time,
                    timestamp_utc=time_utc,
                    tz_known=tz_known,
                    party_a=canon_sender,
                    party_a_raw=sender_raw,
                    party_b=None,  # Dalam chat 1-on-1 lawan bicaranya terasosiasi dari peserta thread
                    thread_id=thread_name,
                    contact_name=contact_label,
                )

            result.events.append(event)

        return result

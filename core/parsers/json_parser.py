"""
Parser untuk berkas JSON (ekspor log percakapan, Telegram, CDR JSON, backup kontak/pesan).
"""
from __future__ import annotations
import json
import os
from typing import Any, Dict, List, Optional
from .base import BaseParser, ParseResult
from ..models import NormalizedEvent
from ..normalizer import try_normalize_phone
from ..time_utils import parse_datetime_to_utc


class JSONParser(BaseParser):
    parser_name = "JSONParser"
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
        default_event_type = profile.get("event_type")
        source_name = (options or {}).get("source_name") or os.path.basename(file_path)

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            try:
                data = json.load(f)
            except Exception as e:
                result.rejected_records.append({
                    "ref": "root",
                    "reason": f"Gagal membaca format JSON: {str(e)}"
                })
                return result

        # Deteksi otomatis struktur Telegram Desktop Export
        if isinstance(data, dict) and "messages" in data and ("name" in data or "type" in data):
            return self._parse_telegram_export(data, source_name, result)

        # Navigasi root_path jika ditentukan
        root_path = profile.get("root_path")
        items = data
        if root_path and isinstance(data, dict):
            parts = root_path.split(".")
            for part in parts:
                if isinstance(items, dict) and part in items:
                    items = items[part]
                else:
                    items = []
                    break

        if not isinstance(items, list):
            if isinstance(items, dict):
                items = [items]
            else:
                result.rejected_records.append({
                    "ref": "root",
                    "reason": "Format JSON bukan daftar entri (list) yang valid"
                })
                return result

        col_party_a = mapping.get("party_a") or mapping.get("from") or "from"
        col_party_b = mapping.get("party_b") or mapping.get("to") or "to"
        col_time = mapping.get("timestamp") or mapping.get("date") or "timestamp"
        col_type = mapping.get("event_type") or mapping.get("type")
        col_dir = mapping.get("direction") or "direction"
        col_status = mapping.get("status") or "status"
        col_eid = mapping.get("event_id") or mapping.get("id") or "id"
        col_thread = mapping.get("thread_id") or "thread_id"
        col_group = mapping.get("group_id") or "group_id"
        col_contact_name = mapping.get("contact_name") or mapping.get("name") or "name"
        col_owner = mapping.get("contact_owner") or mapping.get("owner")
        target_basis = profile.get("target_account_basis") or (options or {}).get("target_account_basis")

        idx = 0
        for item in items:
            idx += 1
            result.total_rows += 1
            if not isinstance(item, dict):
                result.rejected_records.append({
                    "ref": f"item:{idx}",
                    "reason": "Entri bukan merupakan objek JSON"
                })
                continue

            raw_a = str(item.get(col_party_a, "")).strip() or None
            raw_b = str(item.get(col_party_b, "")).strip() or None
            raw_time = item.get(col_time)
            raw_eid = str(item.get(col_eid, "")).strip() or None
            raw_thread = str(item.get(col_thread, "")).strip() or None
            raw_group = str(item.get(col_group, "")).strip() or None
            raw_contact_name = str(item.get(col_contact_name, "")).strip() or None
            raw_owner = str(item.get(col_owner, "")).strip() or None if col_owner else None

            # Menentukan tipe event
            ev_type = default_event_type
            if not ev_type and col_type and col_type in item:
                ev_type = str(item[col_type]).upper()
            if not ev_type:
                if raw_group:
                    ev_type = "GROUP_PARTICIPATION"
                elif raw_contact_name and (raw_a or raw_b):
                    ev_type = "CONTACT"
                else:
                    ev_type = "MESSAGE"

            # Parse nomor
            canon_a, err_a = try_normalize_phone(raw_a) if raw_a else (None, "Kosong")
            canon_b, err_b = try_normalize_phone(raw_b) if raw_b else (None, "Kosong")
            canon_owner, _ = try_normalize_phone(raw_owner) if raw_owner else (None, None)

            if ev_type == "CONTACT":
                # Kontak biasanya party_b adalah nomor kontak
                target_phone = canon_b or canon_a
                target_raw = raw_b or raw_a
                if not target_phone:
                    result.rejected_records.append({
                        "ref": f"item:{idx}",
                        "reason": f"Nomor kontak tidak valid: '{target_raw}'"
                    })
                    continue

                orig_time, time_utc, tz_known = parse_datetime_to_utc(raw_time)
                event = NormalizedEvent(
                    event_type="CONTACT",
                    source_name=source_name,
                    record_ref=f"item:{idx}",
                    event_id=raw_eid,
                    timestamp_orig=orig_time,
                    timestamp_utc=time_utc,
                    tz_known=tz_known,
                    party_a=canon_owner,
                    party_a_raw=raw_owner,
                    party_b=target_phone,
                    party_b_raw=target_raw,
                    contact_name=raw_contact_name,
                    contact_owner=canon_owner,
                    target_account_basis=target_basis,
                )
                result.events.append(event)
                continue

            # Untuk Call / Message / Group
            if not canon_a and not canon_b:
                result.rejected_records.append({
                    "ref": f"item:{idx}",
                    "reason": f"Nomor telepon tidak valid pada event: party_a='{raw_a}', party_b='{raw_b}'"
                })
                continue

            orig_time, time_utc, tz_known = parse_datetime_to_utc(raw_time)
            raw_dir = str(item.get(col_dir, "UNKNOWN")).upper()
            raw_status = str(item.get(col_status, "UNKNOWN")).upper()

            event = NormalizedEvent(
                event_type=ev_type,
                source_name=source_name,
                record_ref=f"item:{idx}",
                event_id=raw_eid,
                timestamp_orig=orig_time,
                timestamp_utc=time_utc,
                tz_known=tz_known,
                party_a=canon_a,
                party_a_raw=raw_a,
                party_b=canon_b,
                party_b_raw=raw_b,
                direction=raw_dir,
                status=raw_status,
                thread_id=raw_thread,
                group_id=raw_group,
                contact_name=raw_contact_name,
                target_account_basis=target_basis,
            )
            result.events.append(event)

        return result

    def _parse_telegram_export(self, data: dict, source_name: str, result: ParseResult) -> ParseResult:
        chat_type = data.get("type", "personal_chat")
        chat_name = data.get("name", "Telegram Chat")
        chat_id = str(data.get("id", ""))
        messages = data.get("messages", [])

        is_group = chat_type in ("public_group", "private_group", "group", "supergroup")
        ev_type = "GROUP_PARTICIPATION" if is_group else "MESSAGE"

        msg_idx = 0
        for msg in messages:
            msg_idx += 1
            result.total_rows += 1
            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type")
            if msg_type != "message":
                # Pesan sistem (service message) diabaikan
                continue

            from_id = str(msg.get("from_id", ""))
            from_name = msg.get("from", "")
            raw_time = msg.get("date")
            msg_eid = str(msg.get("id", f"msg_{msg_idx}"))

            # Cari nomor telepon dari from_id jika format user id / phone
            canon_sender, _ = try_normalize_phone(from_id)

            orig_time, time_utc, tz_known = parse_datetime_to_utc(raw_time)

            if is_group:
                # Partisipasi grup
                event = NormalizedEvent(
                    event_type="GROUP_PARTICIPATION",
                    source_name=source_name,
                    record_ref=f"msg_id:{msg_eid}",
                    event_id=f"tg_{chat_id}_{msg_eid}",
                    timestamp_orig=orig_time,
                    timestamp_utc=time_utc,
                    tz_known=tz_known,
                    party_a=canon_sender,
                    party_a_raw=from_id or from_name,
                    group_id=chat_id or chat_name,
                    contact_name=from_name,
                )
                result.events.append(event)
            else:
                # Private chat
                event = NormalizedEvent(
                    event_type="MESSAGE",
                    source_name=source_name,
                    record_ref=f"msg_id:{msg_eid}",
                    event_id=f"tg_{chat_id}_{msg_eid}",
                    timestamp_orig=orig_time,
                    timestamp_utc=time_utc,
                    tz_known=tz_known,
                    party_a=canon_sender,
                    party_a_raw=from_id or from_name,
                    party_b=None,  # Lawan bicara terpetakan dari metadata thread jika ada
                    thread_id=chat_id or chat_name,
                    contact_name=from_name,
                )
                result.events.append(event)

        return result

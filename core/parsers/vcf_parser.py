"""
Parser untuk berkas vCard (.vcf) mendukung vCard 2.1, 3.0, dan 4.0.
"""
from __future__ import annotations
import os
import re
from typing import Any, Dict, List, Optional
from .base import BaseParser, ParseResult
from ..models import NormalizedEvent
from ..normalizer import try_normalize_phone


class VCFParser(BaseParser):
    parser_name = "VCFParser"
    parser_version = "1.0.0"

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

        owner_phone_raw = options.get("contact_owner") or profile.get("contact_owner")
        target_basis = options.get("target_account_basis") or profile.get("target_account_basis")

        canon_owner = None
        if owner_phone_raw:
            canon_owner, _ = try_normalize_phone(owner_phone_raw)

        # 1. Baca baris dan tangani line unfolding (RFC 2425/6350)
        lines: list[str] = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                line = raw_line.rstrip("\r\n")
                if line.startswith(" ") or line.startswith("\t"):
                    if lines:
                        lines[-1] += line[1:]
                else:
                    lines.append(line)

        # 2. Parse setiap vCard block
        in_vcard = False
        current_name = None
        current_tels: list[tuple[str, str]] = []  # list of (raw_tel, tel_type)
        current_uid = None
        card_start_line = 0

        for line_no, line in enumerate(lines, 1):
            upper_line = line.strip().upper()
            if upper_line == "BEGIN:VCARD":
                in_vcard = True
                current_name = None
                current_tels = []
                current_uid = None
                card_start_line = line_no
                continue

            if upper_line == "END:VCARD":
                if in_vcard:
                    result.total_rows += 1
                    if not current_tels:
                        result.rejected_records.append({
                            "ref": f"vcard_line:{card_start_line}",
                            "reason": f"Kontak '{current_name or 'Tanpa Nama'}' tidak memiliki nomor telepon (TEL)"
                        })
                    else:
                        for raw_tel, tel_type in current_tels:
                            canon_phone, err_phone = try_normalize_phone(raw_tel)
                            if not canon_phone:
                                result.rejected_records.append({
                                    "ref": f"vcard_line:{card_start_line}",
                                    "reason": f"Nomor telepon tidak valid '{raw_tel}': {err_phone}"
                                })
                                continue

                            event = NormalizedEvent(
                                event_type="CONTACT",
                                source_name=source_name,
                                record_ref=f"vcard:{card_start_line}",
                                event_id=current_uid or f"vcf_{card_start_line}_{canon_phone}",
                                party_a=canon_owner,
                                party_a_raw=owner_phone_raw,
                                party_b=canon_phone,
                                party_b_raw=raw_tel,
                                contact_name=current_name,
                                contact_owner=canon_owner,
                                target_account_basis=target_basis,
                            )
                            result.events.append(event)
                in_vcard = False
                continue

            if in_vcard:
                if ":" not in line:
                    continue
                key_part, val_part = line.split(":", 1)
                key_clean = key_part.split(";")[0].strip().upper()
                val_clean = val_part.strip()

                if key_clean == "FN":
                    current_name = val_clean
                elif key_clean == "N" and not current_name:
                    parts = [p.strip() for p in val_clean.split(";") if p.strip()]
                    if parts:
                        current_name = " ".join(reversed(parts))
                elif key_clean == "TEL":
                    current_tels.append((val_clean, key_part))
                elif key_clean == "UID":
                    current_uid = val_clean

        return result

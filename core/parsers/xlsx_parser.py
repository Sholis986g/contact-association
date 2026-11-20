"""
Parser untuk berkas spreadsheet XLSX menggunakan standard library (zipfile + xml.etree)
dengan dukungan fallback ke openpyxl jika tersedia, dan pemilihan sheet (--sheet).
"""
from __future__ import annotations
import os
import re
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET
import zipfile

from .base import BaseParser, ParseResult
from ..models import NormalizedEvent
from ..normalizer import try_normalize_phone
from ..time_utils import parse_datetime_to_utc


class XLSXParser(BaseParser):
    parser_name = "XLSXParser"
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
        sheet_option = (options or {}).get("sheet") or profile.get("sheet")

        # Coba buka zipfile XLSX
        try:
            with zipfile.ZipFile(file_path, 'r') as z:
                rows_data, sheet_used = self._read_xlsx_via_zip(z, sheet_option)
        except Exception as e:
            result.rejected_records.append({
                "ref": "file",
                "reason": f"Gagal membaca format berkas XLSX: {str(e)}"
            })
            return result

        if not rows_data:
            result.rejected_records.append({
                "ref": "sheet",
                "reason": f"Sheet '{sheet_used}' kosong atau tidak memiliki data"
            })
            return result

        header = rows_data[0]
        field_map = {str(col).strip().lower(): i for i, col in enumerate(header) if col is not None}

        col_party_a = mapping.get("party_a") or mapping.get("caller") or mapping.get("from") or "party_a"
        col_party_b = mapping.get("party_b") or mapping.get("callee") or mapping.get("to") or "party_b"
        col_time = mapping.get("timestamp") or mapping.get("date") or mapping.get("call_time") or "timestamp"
        col_type = mapping.get("event_type") or mapping.get("activity_type") or mapping.get("type")
        col_dir = mapping.get("direction") or "direction"
        col_status = mapping.get("status") or "status"
        col_dur = mapping.get("duration") or "duration"
        col_eid = mapping.get("event_id") or mapping.get("call_id") or mapping.get("id") or "event_id"
        col_thread = mapping.get("thread_id") or "thread_id"

        def get_val(row_vals: list, col_name: Optional[str]) -> Optional[str]:
            if not col_name:
                return None
            c_low = col_name.strip().lower()
            idx = field_map.get(c_low)
            if idx is not None and idx < len(row_vals):
                val = row_vals[idx]
                return str(val).strip() if val is not None else None
            return None

        row_no = 1
        for row in rows_data[1:]:
            row_no += 1
            result.total_rows += 1

            raw_a = get_val(row, col_party_a)
            raw_b = get_val(row, col_party_b)
            raw_time = get_val(row, col_time)
            raw_status = get_val(row, col_status) or "UNKNOWN"
            raw_dir = (get_val(row, col_dir) or "UNKNOWN").upper()
            raw_dur_str = get_val(row, col_dur)
            raw_eid = get_val(row, col_eid)
            raw_thread = get_val(row, col_thread)

            # Lewati baris kosong sepenuhnya
            if not any(row):
                continue

            ev_type = default_event_type
            if col_type:
                val_type = get_val(row, col_type)
                if val_type:
                    val_up = val_type.upper()
                    if "SMS" in val_up or "MSG" in val_up or "CHAT" in val_up:
                        ev_type = "MESSAGE"
                    elif "CALL" in val_up or "VOICE" in val_up:
                        ev_type = "CALL"

            duration = None
            if raw_dur_str:
                try:
                    duration = int(float(raw_dur_str))
                except ValueError:
                    pass

            canon_a, err_a = try_normalize_phone(raw_a) if raw_a else (None, "Nomor party A kosong")
            canon_b, err_b = try_normalize_phone(raw_b) if raw_b else (None, "Nomor party B kosong")

            if not canon_a and not canon_b:
                result.rejected_records.append({
                    "ref": f"sheet:{sheet_used},row:{row_no}",
                    "reason": f"Nomor telepon tidak valid: A='{raw_a}', B='{raw_b}'"
                })
                continue

            orig_time, time_utc, tz_known = parse_datetime_to_utc(raw_time)

            norm_status = raw_status.upper()
            if "ANSWER" in norm_status or "TERJAWAB" in norm_status or (duration and duration > 0):
                norm_status = "ANSWERED"
            elif "MISS" in norm_status or "TIDAK TERJAWAB" in norm_status:
                norm_status = "MISSED"
            elif "ATTEMPT" in norm_status or "DIAL" in norm_status or "COBA" in norm_status:
                norm_status = "ATTEMPTED"

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
                record_ref=f"sheet:{sheet_used},row:{row_no}",
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

    def _read_xlsx_via_zip(self, z: zipfile.ZipFile, requested_sheet: Optional[str]) -> tuple[list[list[Any]], str]:
        # 1. Baca shared strings
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
            ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            for si in tree.findall(f"{ns}si"):
                # Bisa berisi direct <t> atau beberapa <r><t>
                texts = [t.text or "" for t in si.iter(f"{ns}t")]
                shared_strings.append("".join(texts))

        # 2. Baca daftar sheet dari xl/workbook.xml
        wb_tree = ET.fromstring(z.read("xl/workbook.xml"))
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        r_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

        sheets_elem = wb_tree.find(f"{ns}sheets")
        sheet_info = []  # list of (name, rId, sheetId)
        if sheets_elem is not None:
            for s in sheets_elem.findall(f"{ns}sheet"):
                s_name = s.attrib.get("name")
                r_id = s.attrib.get(f"{r_ns}id") or s.attrib.get("r:id")
                sheet_id = s.attrib.get("sheetId")
                sheet_info.append((s_name, r_id, sheet_id))

        # Baca relasi workbook untuk memetakan rId ke path berkas XML
        rels_tree = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rel_map = {}
        for rel in rels_tree.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"):
            rid = rel.attrib.get("Id")
            target = rel.attrib.get("Target")
            if target.startswith("/"):
                target = target[1:]
            elif not target.startswith("xl/"):
                target = "xl/" + target
            rel_map[rid] = target

        # Tentukan sheet yang akan dibaca
        selected_target = None
        selected_name = None

        if requested_sheet:
            req_str = str(requested_sheet).strip()
            # Cek jika requested_sheet berupa angka indeks (0, 1, ...)
            if req_str.isdigit():
                idx = int(req_str)
                if 0 <= idx < len(sheet_info):
                    selected_name, rid, _ = sheet_info[idx]
                    selected_target = rel_map.get(rid)
            else:
                for s_name, rid, _ in sheet_info:
                    if s_name.lower() == req_str.lower():
                        selected_name = s_name
                        selected_target = rel_map.get(rid)
                        break

        if not selected_target:
            # Default ke sheet pertama
            if sheet_info:
                selected_name, rid, _ = sheet_info[0]
                selected_target = rel_map.get(rid)
            else:
                selected_name = "Sheet1"
                selected_target = "xl/worksheets/sheet1.xml"

        if not selected_target or selected_target not in z.namelist():
            raise FileNotFoundError(f"Lembar kerja (sheet) '{requested_sheet or selected_name}' tidak ditemukan di XLSX.")

        # 3. Baca XML worksheet
        ws_tree = ET.fromstring(z.read(selected_target))
        sheet_data = ws_tree.find(f"{ns}sheetData")
        if sheet_data is None:
            return [], selected_name

        def col_letters_to_index(col_str: str) -> int:
            num = 0
            for char in col_str:
                num = num * 26 + (ord(char.upper()) - ord('A') + 1)
            return num - 1

        rows_list: list[list[Any]] = []
        for row_elem in sheet_data.findall(f"{ns}row"):
            row_cells: dict[int, Any] = {}
            max_col = -1

            for c in row_elem.findall(f"{ns}c"):
                r_ref = c.attrib.get("r", "")
                # Pisahkan huruf kolom dan angka baris, misal 'B12' -> col='B', row=12
                match = re.match(r'([A-Za-z]+)(\d+)', r_ref)
                if match:
                    col_idx = col_letters_to_index(match.group(1))
                else:
                    col_idx = max_col + 1

                max_col = max(max_col, col_idx)
                cell_type = c.attrib.get("t")
                v_elem = c.find(f"{ns}v")
                val_text = v_elem.text if v_elem is not None else None

                if cell_type == "s" and val_text is not None and val_text.isdigit():
                    s_idx = int(val_text)
                    if s_idx < len(shared_strings):
                        row_cells[col_idx] = shared_strings[s_idx]
                    else:
                        row_cells[col_idx] = ""
                elif cell_type == "inlineStr":
                    is_elem = c.find(f"{ns}is")
                    if is_elem is not None:
                        texts = [t.text or "" for t in is_elem.iter(f"{ns}t")]
                        row_cells[col_idx] = "".join(texts)
                    else:
                        row_cells[col_idx] = ""
                else:
                    row_cells[col_idx] = val_text

            if max_col >= 0:
                row_arr = [row_cells.get(c_idx, None) for c_idx in range(max_col + 1)]
                rows_list.append(row_arr)

        return rows_list, selected_name

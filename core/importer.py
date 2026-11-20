"""
Modul orkestrasi impor bukti forensik, perhitungan manifest SHA-256,
pencegahan duplikasi file/event, dan batch insert berparameter ke SQLite.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .db import Database
from .models import AuditEntry, ManifestRecord, NormalizedEvent
from .parsers import SUPPORTED_EXTENSIONS, get_parser_for_file
from .time_utils import parse_datetime_to_utc


def compute_sha256(file_path: str) -> str:
    """Menghitung hash SHA-256 dari berkas secara read-only dengan streaming buffer."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


class ImportReport:
    def __init__(self):
        self.processed_files: List[Dict[str, Any]] = []
        self.skipped_files: List[Dict[str, Any]] = []
        self.total_accepted: int = 0
        self.total_rejected: int = 0
        self.duplicates_skipped: int = 0
        self.deduplicated_events: int = 0


class Importer:
    """Orkestrator proses impor berkas dan folder artefak bukti."""

    def __init__(self, db: Database, case_id: str, operator_id: str):
        self.db = db
        self.case_id = case_id
        self.operator_id = operator_id

    def import_path(
        self,
        input_path: str,
        profile_path: Optional[str] = None,
        sheet_name: Optional[str] = None,
        source_device: Optional[str] = None,
        contact_owner: Optional[str] = None,
        target_account_basis: Optional[str] = None,
    ) -> ImportReport:
        report = ImportReport()
        profile: Dict[str, Any] = {}

        if profile_path:
            if not os.path.exists(profile_path):
                raise FileNotFoundError(f"Berkas profil pemetaan tidak ditemukan: '{profile_path}'")
            with open(profile_path, "r", encoding="utf-8") as pf:
                profile = json.load(pf)

        options: Dict[str, Any] = {
            "sheet": sheet_name or profile.get("sheet"),
            "source_device": source_device or profile.get("source_device"),
            "contact_owner": contact_owner or profile.get("contact_owner"),
            "target_account_basis": target_account_basis or profile.get("target_account_basis"),
        }

        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Jalur input tidak ditemukan: '{input_path}'")

        files_to_process: List[str] = []
        if os.path.isdir(input_path):
            for root, _, files in os.walk(input_path):
                for f in sorted(files):
                    full_p = os.path.join(root, f)
                    ext = os.path.splitext(f)[1].lower()
                    if ext in SUPPORTED_EXTENSIONS:
                        files_to_process.append(full_p)
                    else:
                        report.skipped_files.append({
                            "file": full_p,
                            "reason": f"Format ekstensi '{ext}' tidak didukung atau bukan artefak yang diizinkan"
                        })
        else:
            files_to_process.append(input_path)

        for fpath in files_to_process:
            self._import_single_file(fpath, profile, options, report)

        # Catat audit log impor
        audit_entry = AuditEntry(
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            operator_id=self.operator_id,
            case_id=self.case_id,
            action_type="IMPORT",
            masked_target=None,
            filters_applied={"input": input_path, "profile": profile_path},
            result_count=report.total_accepted,
            execution_status="SUCCESS" if report.total_accepted > 0 or not report.skipped_files else "PARTIAL",
            error_message=None if not report.skipped_files else f"{len(report.skipped_files)} berkas dilewati",
        )
        self.db.log_audit(audit_entry)

        return report

    def _import_single_file(
        self,
        file_path: str,
        profile: Dict[str, Any],
        options: Dict[str, Any],
        report: ImportReport,
    ) -> None:
        file_name = os.path.basename(file_path)
        sha256_hash = compute_sha256(file_path)
        file_size = os.path.getsize(file_path)
        import_time_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Cek apakah berkas dengan sha256 yang sama sudah pernah diimpor untuk case_id ini
        existing_manifest_id = self.db.check_manifest_exists(sha256_hash, self.case_id)
        if existing_manifest_id is not None:
            report.duplicates_skipped += 1
            report.skipped_files.append({
                "file": file_path,
                "sha256": sha256_hash,
                "reason": f"Berkas identik (SHA-256: {sha256_hash[:12]}...) sudah diimpor sebelumnya pada kasus ini (Manifest ID: {existing_manifest_id}). Dilewati untuk mencegah penggandaan peristiwa."
            })
            return

        parser = get_parser_for_file(file_path, profile.get("parser_name"))
        if not parser:
            report.skipped_files.append({
                "file": file_path,
                "reason": f"Tidak ditemukan parser yang cocok untuk berkas '{file_name}'"
            })
            return

        # 2. Parse berkas secara read-only
        opts = dict(options)
        opts["source_name"] = opts.get("source_device") or file_name
        parse_res = parser.parse(file_path, profile, opts)

        # 3. Buat manifest entri
        status = "SUCCESS" if not parse_res.rejected_records else "PARTIAL"
        if not parse_res.events and parse_res.rejected_records:
            status = "FAILED"

        manifest = ManifestRecord(
            case_id=self.case_id,
            operator_id=self.operator_id,
            file_path=file_path,
            file_name=file_name,
            sha256=sha256_hash,
            file_size=file_size,
            source_device=opts.get("source_device"),
            parser_name=parser.parser_name,
            parser_version=parser.parser_version,
            import_time_utc=import_time_utc,
            status=status,
            records_accepted=len(parse_res.events),
            records_rejected=len(parse_res.rejected_records),
            manifest_metadata={
                "rejected_samples": parse_res.rejected_records[:10],
                "total_rows": parse_res.total_rows,
            },
        )

        manifest_id = self.db.insert_manifest(manifest)

        # 4. Simpan evidence_records dan events dalam transaksi SQLite eksplisit
        accepted_count = 0
        dedup_count = 0

        conn = self.db._raw_connection()
        try:
            conn.execute("BEGIN TRANSACTION;")
            for event in parse_res.events:
                # Buat evidence record untuk rujukan forensik
                cur_ev = conn.execute(
                    """
                    INSERT INTO evidence_records (
                        manifest_id, case_id, record_ref, event_id,
                        raw_metadata, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest_id,
                        self.case_id,
                        event.record_ref,
                        event.event_id,
                        json.dumps({
                            "file": file_name,
                            "source": event.source_name,
                            "ref": event.record_ref,
                            "party_a_raw": event.party_a_raw,
                            "party_b_raw": event.party_b_raw,
                            "status": event.status,
                        }, ensure_ascii=False),
                        import_time_utc,
                    ),
                )
                evidence_id = cur_ev.lastrowid

                # Cek deduplikasi peristiwa jika event_id tersedia dalam lingkup sumber yang sama
                existing_ev_id = None
                if event.event_id:
                    row_exist = conn.execute(
                        """
                        SELECT id, party_a, party_b, timestamp_utc FROM events
                        WHERE case_id = ? AND source_name = ? AND event_id = ?
                        """,
                        (self.case_id, event.source_name, event.event_id),
                    ).fetchone()
                    if row_exist:
                        existing_ev_id = row_exist["id"]
                        # Periksa apakah data identik atau terdapat konflik yang perlu ditinjau
                        if (row_exist["party_a"] != event.party_a or
                            row_exist["party_b"] != event.party_b or
                            row_exist["timestamp_utc"] != event.timestamp_utc):
                            conn.execute(
                                """
                                UPDATE events
                                SET flagged_for_review = 1,
                                    review_reason = 'Konflik metadata pada event_id yang sama dari ekspor berbeda'
                                WHERE id = ?
                                """,
                                (existing_ev_id,),
                            )

                if existing_ev_id is not None:
                    # Jangan gandakan peristiwa, tapi pertahankan rujukan sumber baru di event_sources!
                    conn.execute(
                        """
                        INSERT INTO event_sources (
                            event_id_fk, evidence_id_fk, source_file, record_ref, imported_at_utc
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (existing_ev_id, evidence_id, file_name, event.record_ref, import_time_utc),
                    )
                    dedup_count += 1
                    continue

                # Insert event baru
                cur_ins = conn.execute(
                    """
                    INSERT INTO events (
                        evidence_id, case_id, event_type, source_name, event_id,
                        timestamp_orig, timestamp_utc, tz_known,
                        party_a, party_a_raw, party_b, party_b_raw,
                        direction, status, duration, thread_id, group_id,
                        contact_name, contact_owner, target_account_basis,
                        flagged_for_review, review_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id,
                        self.case_id,
                        event.event_type,
                        event.source_name,
                        event.event_id,
                        event.timestamp_orig,
                        event.timestamp_utc,
                        1 if event.tz_known else 0,
                        event.party_a,
                        event.party_a_raw,
                        event.party_b,
                        event.party_b_raw,
                        event.direction,
                        event.status,
                        event.duration,
                        event.thread_id,
                        event.group_id,
                        event.contact_name,
                        event.contact_owner,
                        event.target_account_basis,
                        1 if event.flagged_for_review else 0,
                        event.review_reason,
                    ),
                )
                new_event_pk = cur_ins.lastrowid
                conn.execute(
                    """
                    INSERT INTO event_sources (
                        event_id_fk, evidence_id_fk, source_file, record_ref, imported_at_utc
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (new_event_pk, evidence_id, file_name, event.record_ref, import_time_utc),
                )
                accepted_count += 1

            conn.execute("COMMIT;")
        except Exception as e:
            conn.execute("ROLLBACK;")
            conn.close()
            raise e
        finally:
            conn.close()

        report.total_accepted += accepted_count
        report.total_rejected += len(parse_res.rejected_records)
        report.deduplicated_events += dedup_count
        report.processed_files.append({
            "manifest_id": manifest_id,
            "file": file_name,
            "sha256": sha256_hash,
            "accepted": accepted_count,
            "rejected": len(parse_res.rejected_records),
            "deduplicated": dedup_count,
            "status": status,
        })

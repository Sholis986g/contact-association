"""
Manajemen database SQLite, skema tabel forensik, indeks performa,
transaksi ACID, dan fungsi audit logging.
"""
from __future__ import annotations
from contextlib import contextmanager
import json
import sqlite3
from typing import Any, Dict, Generator, List, Optional, Tuple
from .models import AuditEntry, ManifestRecord


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS manifests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    file_size INTEGER,
    source_device TEXT,
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    import_time_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    records_accepted INTEGER DEFAULT 0,
    records_rejected INTEGER DEFAULT 0,
    manifest_metadata TEXT,
    UNIQUE(sha256, case_id)
);

CREATE TABLE IF NOT EXISTS evidence_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manifest_id INTEGER NOT NULL REFERENCES manifests(id),
    case_id TEXT NOT NULL,
    record_ref TEXT NOT NULL,
    event_id TEXT,
    raw_metadata TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id INTEGER NOT NULL REFERENCES evidence_records(id),
    case_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_name TEXT,
    event_id TEXT,
    timestamp_orig TEXT,
    timestamp_utc TEXT,
    tz_known INTEGER DEFAULT 0,
    party_a TEXT,
    party_a_raw TEXT,
    party_b TEXT,
    party_b_raw TEXT,
    direction TEXT DEFAULT 'UNKNOWN',
    status TEXT DEFAULT 'UNKNOWN',
    duration INTEGER,
    thread_id TEXT,
    group_id TEXT,
    contact_name TEXT,
    contact_owner TEXT,
    target_account_basis TEXT,
    flagged_for_review INTEGER DEFAULT 0,
    review_reason TEXT
);

CREATE TABLE IF NOT EXISTS event_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id_fk INTEGER NOT NULL REFERENCES events(id),
    evidence_id_fk INTEGER NOT NULL REFERENCES evidence_records(id),
    source_file TEXT,
    record_ref TEXT,
    imported_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    masked_target TEXT,
    filters_applied TEXT,
    result_count INTEGER DEFAULT 0,
    execution_status TEXT NOT NULL,
    error_message TEXT
);

-- Indeks performa untuk kueri cepat nomor kanonik, pihak panggilan/pesan, timestamp, dan pencarian suffix
CREATE INDEX IF NOT EXISTS idx_events_party_a ON events(case_id, party_a);
CREATE INDEX IF NOT EXISTS idx_events_party_b ON events(case_id, party_b);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(case_id, timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_events_dedup ON events(case_id, source_name, event_id);
CREATE INDEX IF NOT EXISTS idx_events_group ON events(case_id, group_id);
CREATE INDEX IF NOT EXISTS idx_events_contact_owner ON events(case_id, contact_owner);
CREATE INDEX IF NOT EXISTS idx_events_suffix_a ON events(substr(party_a, -8));
CREATE INDEX IF NOT EXISTS idx_events_suffix_b ON events(substr(party_b, -8));
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_logs(case_id, timestamp_utc);
"""


class Database:
    """Wrapper untuk database SQLite dengan dukungan transaksi dan audit."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _raw_connection(self) -> sqlite3.Connection:
        """
        Mengembalikan koneksi SQLite mentah (tanpa context manager / auto-commit).
        Pemanggil bertanggung jawab untuk memanggil conn.close() sendiri.
        Gunakan ini hanya di dalam importer untuk transaksi batch eksplisit.
        """
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager yang menghasilkan koneksi SQLite dengan auto-commit/rollback.
        Semua operasi READ dan operasi WRITE tunggal menggunakan ini.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        try:
            with conn:  # auto-commit on success, auto-rollback on exception
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        # executescript memerlukan mode isolation_level=None (autocommit)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(SCHEMA_SQL)
        finally:
            conn.close()

    def log_audit(self, entry: AuditEntry) -> int:
        """Mencatat aktivitas ke audit_logs (selalu dicatat secara terpisah)."""
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_logs (
                    timestamp_utc, operator_id, case_id, action_type,
                    masked_target, filters_applied, result_count,
                    execution_status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.timestamp_utc,
                    entry.operator_id,
                    entry.case_id,
                    entry.action_type,
                    entry.masked_target,
                    json.dumps(entry.filters_applied, ensure_ascii=False),
                    entry.result_count,
                    entry.execution_status,
                    entry.error_message,
                ),
            )
            return cur.lastrowid

    def check_manifest_exists(self, sha256: str, case_id: str) -> Optional[int]:
        """Cek apakah berkas dengan sha256 yang sama sudah pernah diimpor pada case_id yang sama."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM manifests WHERE sha256 = ? AND case_id = ?",
                (sha256, case_id),
            ).fetchone()
            if row:
                return row["id"]
            return None

    def insert_manifest(self, m: ManifestRecord) -> int:
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO manifests (
                    case_id, operator_id, file_path, file_name, sha256,
                    file_size, source_device, parser_name, parser_version,
                    import_time_utc, status, records_accepted, records_rejected,
                    manifest_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m.case_id,
                    m.operator_id,
                    m.file_path,
                    m.file_name,
                    m.sha256,
                    m.file_size,
                    m.source_device,
                    m.parser_name,
                    m.parser_version,
                    m.import_time_utc,
                    m.status,
                    m.records_accepted,
                    m.records_rejected,
                    json.dumps(m.manifest_metadata, ensure_ascii=False),
                ),
            )
            return cur.lastrowid

    def update_manifest_counts(
        self, manifest_id: int, accepted: int, rejected: int, status: str
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE manifests
                SET records_accepted = ?, records_rejected = ?, status = ?
                WHERE id = ?
                """,
                (accepted, rejected, status, manifest_id),
            )

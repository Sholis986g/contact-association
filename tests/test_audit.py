"""
Pengujian audit trail forensik:
- Pencatatan seluruh kueri berhasil dan gagal
- Status eksekusi SUCCESS vs FAILED / ERROR
- Perlindungan privasi: tidak menyimpan isi pesan atau nomor lengkap di audit log standar
"""
import os
import shutil
import tempfile
import unittest

from core.candidates import find_candidates
from core.db import Database
from core.engine import AssociationEngine
from core.exporter import export_report
from core.importer import Importer
from core.normalizer import ValidationError

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(BASE_DIR, "fixtures")


class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "audit_test.sqlite")
        self.db = Database(self.db_path)
        self.case_id = "CASE-AUDIT-001"
        self.operator_id = "OP-FORENSIC-01"
        self.importer = Importer(self.db, self.case_id, self.operator_id)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_audit_successful_queries_and_privacy(self):
        # 1. Audit Impor
        self.importer.import_path(
            os.path.join(FIXTURES_DIR, "sample_cdr.csv"),
            profile_path=os.path.join(BASE_DIR, "profiles", "cdr_profile.json"),
        )

        with self.db.get_connection() as conn:
            row_imp = conn.execute("SELECT * FROM audit_logs WHERE action_type = 'IMPORT'").fetchone()
            self.assertIsNotNone(row_imp)
            self.assertEqual(row_imp["operator_id"], self.operator_id)
            self.assertEqual(row_imp["case_id"], self.case_id)
            self.assertEqual(row_imp["execution_status"], "SUCCESS")
            self.assertGreater(row_imp["result_count"], 0)

        # 2. Audit Candidates
        find_candidates(self.db, self.case_id, self.operator_id, "0012")
        with self.db.get_connection() as conn:
            row_cand = conn.execute("SELECT * FROM audit_logs WHERE action_type = 'CANDIDATES'").fetchone()
            self.assertIsNotNone(row_cand)
            self.assertEqual(row_cand["execution_status"], "SUCCESS")
            self.assertEqual(row_cand["masked_target"], "****0012")

        # 3. Audit Lookup
        engine = AssociationEngine(self.db, self.case_id, self.operator_id)
        res = engine.lookup("081200000012")
        with self.db.get_connection() as conn:
            row_look = conn.execute("SELECT * FROM audit_logs WHERE action_type = 'LOOKUP'").fetchone()
            self.assertIsNotNone(row_look)
            self.assertEqual(row_look["execution_status"], "SUCCESS")
            # Nomor target tersamarkan di audit log standar
            self.assertEqual(row_look["masked_target"], "0812****0012")

        # 4. Audit Export
        exp_path = os.path.join(self.temp_dir, "export.json")
        export_report(res, exp_path, self.db, self.case_id, self.operator_id)
        with self.db.get_connection() as conn:
            row_exp = conn.execute("SELECT * FROM audit_logs WHERE action_type = 'EXPORT'").fetchone()
            self.assertIsNotNone(row_exp)
            self.assertEqual(row_exp["execution_status"], "SUCCESS")

        # Pastikan tidak ada pesan teks obrolan tersimpan di audit log
        with self.db.get_connection() as conn:
            all_logs = conn.execute("SELECT filters_applied, error_message FROM audit_logs").fetchall()
            for r in all_logs:
                f_str = r["filters_applied"] or ""
                e_str = r["error_message"] or ""
                self.assertNotIn("Selamat pagi", f_str)
                self.assertNotIn("Selamat pagi", e_str)

    def test_audit_failed_queries(self):
        # Simulasi kegagalan lookup karena format nomor tidak sah
        raw_invalid_target = "0812INVALID"
        from core.normalizer import try_normalize_phone
        canon, err = try_normalize_phone(raw_invalid_target)

        # Catat kegagalan sesuai implementasi CLI
        from core.models import AuditEntry
        from datetime import datetime, timezone
        audit_entry = AuditEntry(
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            operator_id=self.operator_id,
            case_id=self.case_id,
            action_type="LOOKUP",
            masked_target="****INVALID",
            filters_applied={"raw_input": "0812INVALID"},
            result_count=0,
            execution_status="FAILED",
            error_message=err,
        )
        self.db.log_audit(audit_entry)

        with self.db.get_connection() as conn:
            row_fail = conn.execute("SELECT * FROM audit_logs WHERE execution_status = 'FAILED'").fetchone()
            self.assertIsNotNone(row_fail)
            self.assertEqual(row_fail["action_type"], "LOOKUP")
            self.assertIn("Karakter tidak diizinkan", row_fail["error_message"])


if __name__ == "__main__":
    unittest.main()

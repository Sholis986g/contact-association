"""
Pengujian integritas bukti dan pencegahan duplikasi:
- Re-impor berkas identik (SHA-256 sama) tidak menggandakan peristiwa
- Deduplikasi peristiwa berbasis event_id dalam lingkup sumber sama
- Flagging review jika terdapat metadata bertentangan
"""
import os
import shutil
import tempfile
import unittest

from core.db import Database
from core.importer import Importer

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(BASE_DIR, "fixtures")


class TestDeduplication(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "dedup_test.sqlite")
        self.db = Database(self.db_path)
        self.case_id = "CASE-DEDUP-001"
        self.operator_id = "OP-DEDUP"
        self.importer = Importer(self.db, self.case_id, self.operator_id)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reimport_identical_file_does_not_duplicate(self):
        csv_file = os.path.join(FIXTURES_DIR, "sample_cdr.csv")
        profile = os.path.join(BASE_DIR, "profiles", "cdr_profile.json")

        # Impor pertama
        rep1 = self.importer.import_path(csv_file, profile_path=profile)
        self.assertGreater(rep1.total_accepted, 0)
        initial_accepted = rep1.total_accepted

        with self.db.get_connection() as conn:
            cnt1 = conn.execute("SELECT COUNT(*) FROM events WHERE case_id = ?", (self.case_id,)).fetchone()[0]
            self.assertEqual(cnt1, initial_accepted)

        # Impor kedua dengan berkas yang persis sama
        rep2 = self.importer.import_path(csv_file, profile_path=profile)
        self.assertEqual(rep2.total_accepted, 0)
        self.assertEqual(rep2.duplicates_skipped, 1)

        # Pastikan jumlah baris peristiwa di database tidak bertambah
        with self.db.get_connection() as conn:
            cnt2 = conn.execute("SELECT COUNT(*) FROM events WHERE case_id = ?", (self.case_id,)).fetchone()[0]
            self.assertEqual(cnt2, initial_accepted)

    def test_same_event_id_cross_export_deduplication(self):
        # Buat dua file CSV berbeda dengan nama berbeda tapi memuat event_id yang sama
        f1_path = os.path.join(self.temp_dir, "export_part1.csv")
        f2_path = os.path.join(self.temp_dir, "export_part2.csv")

        # Baris dengan event_id CDR-9999 ada di kedua file
        with open(f1_path, "w", encoding="utf-8") as f:
            f.write("caller_msisdn,callee_msisdn,call_datetime,duration_sec,call_direction,call_status,cdr_record_id\n")
            f.write("081200000012,081300009999,2024-01-15 10:00:00,100,OUTGOING,ANSWERED,CDR-9999\n")

        # Berkas kedua berbeda (SHA-256 berbeda), namun memiliki satu baris dengan event_id yang sama (CDR-9999)
        with open(f2_path, "w", encoding="utf-8") as f:
            f.write("caller_msisdn,callee_msisdn,call_datetime,duration_sec,call_direction,call_status,cdr_record_id\n")
            f.write("081200000012,081300009999,2024-01-15 10:00:00,100,OUTGOING,ANSWERED,CDR-9999\n")
            f.write("081200000012,081400008888,2024-01-16 12:00:00,50,OUTGOING,ANSWERED,CDR-8888\n")

        profile = os.path.join(BASE_DIR, "profiles", "cdr_profile.json")

        rep1 = self.importer.import_path(f1_path, profile_path=profile, source_device="Telkomsel-Gateway")
        self.assertEqual(rep1.total_accepted, 1)

        rep2 = self.importer.import_path(f2_path, profile_path=profile, source_device="Telkomsel-Gateway")
        self.assertEqual(rep2.deduplicated_events, 1)

        with self.db.get_connection() as conn:
            # Peristiwa hanya dihitung sekali di tabel events
            ev_cnt = conn.execute("SELECT COUNT(*) FROM events WHERE case_id = ? AND event_id = 'CDR-9999'", (self.case_id,)).fetchone()[0]
            self.assertEqual(ev_cnt, 1)

            # Rujukan sumber tersimpan untuk KEDUA ekspor pada event CDR-9999 di event_sources
            src_cnt = conn.execute(
                "SELECT COUNT(*) FROM event_sources WHERE event_id_fk = (SELECT id FROM events WHERE event_id = 'CDR-9999')"
            ).fetchone()[0]
            self.assertEqual(src_cnt, 2)

    def test_conflicting_event_metadata_flags_review(self):
        f1_path = os.path.join(self.temp_dir, "orig.csv")
        f2_path = os.path.join(self.temp_dir, "conflict.csv")

        with open(f1_path, "w", encoding="utf-8") as f:
            f.write("caller_msisdn,callee_msisdn,call_datetime,duration_sec,call_direction,call_status,cdr_record_id\n")
            f.write("081200000012,081300001111,2024-01-15 10:00:00,100,OUTGOING,ANSWERED,CDR-CONFLICT\n")

        # Berkas kedua punya event_id sama tapi nomor callee bertentangan
        with open(f2_path, "w", encoding="utf-8") as f:
            f.write("caller_msisdn,callee_msisdn,call_datetime,duration_sec,call_direction,call_status,cdr_record_id\n")
            f.write("081200000012,081300002222,2024-01-15 10:00:00,100,OUTGOING,ANSWERED,CDR-CONFLICT\n")

        profile = os.path.join(BASE_DIR, "profiles", "cdr_profile.json")

        self.importer.import_path(f1_path, profile_path=profile, source_device="Gateway-A")
        self.importer.import_path(f2_path, profile_path=profile, source_device="Gateway-A")

        with self.db.get_connection() as conn:
            row = conn.execute("SELECT flagged_for_review, review_reason FROM events WHERE event_id = 'CDR-CONFLICT'").fetchone()
            self.assertEqual(row["flagged_for_review"], 1)
            self.assertIn("Konflik metadata", row["review_reason"])


if __name__ == "__main__":
    unittest.main()

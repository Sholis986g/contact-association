"""
Pengujian filter tanggal dan penanganan zona waktu:
- Batas kalender inklusif --to (23:59:59)
- Normalisasi zona waktu ke UTC
- Pemisahan data tanpa tanggal saat filter tanggal aktif
"""
import os
import shutil
import tempfile
import unittest

from core.db import Database
from core.importer import Importer
from core.engine import AssociationEngine
from core.time_utils import parse_date_boundary, parse_datetime_to_utc

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(BASE_DIR, "fixtures")


class TestDateFilters(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "date_test.sqlite")
        self.db = Database(self.db_path)
        self.case_id = "CASE-DATE-001"
        self.operator_id = "OP-DATE"
        self.importer = Importer(self.db, self.case_id, self.operator_id)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_calendar_date_boundary_inclusive(self):
        # 1. Test parse_date_boundary
        from_str, from_dt = parse_date_boundary("2024-01-01", is_end_date=False)
        self.assertEqual(from_str, "2024-01-01T00:00:00")
        self.assertEqual(from_dt.hour, 0)
        self.assertEqual(from_dt.minute, 0)

        # --to kalender harus akhir inklusif pada hari yang diminta (23:59:59)
        to_str, to_dt = parse_date_boundary("2024-01-31", is_end_date=True)
        self.assertEqual(to_str, "2024-01-31T23:59:59")
        self.assertEqual(to_dt.hour, 23)
        self.assertEqual(to_dt.minute, 59)
        self.assertEqual(to_dt.second, 59)

    def test_timezone_parsing(self):
        # WIB (+07:00) ke UTC
        orig, utc, tz_known = parse_datetime_to_utc("2024-01-15 17:00:00 WIB")
        self.assertTrue(tz_known)
        self.assertEqual(utc, "2024-01-15T10:00:00Z")

        # ISO dengan offset +07:00
        orig, utc, tz_known = parse_datetime_to_utc("2024-01-15T17:00:00+07:00")
        self.assertTrue(tz_known)
        self.assertEqual(utc, "2024-01-15T10:00:00Z")

        # Tanpa penanda zona waktu (tz_known=False)
        orig, local_iso, tz_known = parse_datetime_to_utc("2024-01-15 17:00:00")
        self.assertFalse(tz_known)
        self.assertEqual(local_iso, "2024-01-15T17:00:00")

    def test_lookup_date_filtering_and_undated_separation(self):
        # sample_cdr.csv memiliki peristiwa di:
        # 2024-01-10, 2024-01-12, 2024-01-15, 2024-01-20, 2024-01-25,
        # 2024-02-05 (di luar Januari), dan satu peristiwa tanpa timestamp (CDR-1008)
        self.importer.import_path(
            os.path.join(FIXTURES_DIR, "sample_cdr.csv"),
            profile_path=os.path.join(BASE_DIR, "profiles", "cdr_profile.json"),
        )

        engine = AssociationEngine(self.db, self.case_id, self.operator_id)
        target = "081200000012"

        # Kueri tanpa filter tanggal: semua terhitung
        res_all = engine.lookup(target)
        self.assertEqual(res_all["undated_records_separated"], 0)
        c_all = {item["canonical_number"]: item for item in res_all["details"]["CALL_INTERACTION"]}
        # CDR-1007 (Februari) dan CDR-1008 (undated) terhitung pada 081311112222 dan 089912345678
        self.assertEqual(c_all["081311112222"]["event_count"], 4)
        self.assertIn("089912345678", c_all)

        # Kueri dengan filter Januari 2024: --from 2024-01-01 --to 2024-01-31
        res_jan = engine.lookup(target, from_date_str="2024-01-01", to_date_str="2024-01-31")
        # Bukti tanpa tanggal dipisahkan
        self.assertGreater(res_jan["undated_records_separated"], 0)

        c_jan = {item["canonical_number"]: item for item in res_jan["details"]["CALL_INTERACTION"]}
        # Event Februari (2024-02-05) dikecualikan, sehingga 081311112222 hanya memiliki 3 event
        self.assertEqual(c_jan["081311112222"]["event_count"], 3)
        # Event tanpa tanggal pada 089912345678 dikecualikan dari filter rentang tanggal
        self.assertNotIn("089912345678", c_jan)


if __name__ == "__main__":
    unittest.main()

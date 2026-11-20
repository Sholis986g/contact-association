"""
Pengujian fitur masking default dan opsi eksplisit --show-full pada laporan forensik.
"""
import os
import shutil
import tempfile
import unittest

from core.db import Database
from core.importer import Importer
from core.engine import AssociationEngine
from core.exporter import export_report

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(BASE_DIR, "fixtures")


class TestMaskingAndFull(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "mask_test.sqlite")
        self.db = Database(self.db_path)
        self.case_id = "CASE-MASK-001"
        self.operator_id = "OP-MASK"
        self.importer = Importer(self.db, self.case_id, self.operator_id)

        # Impor sample CDR
        self.importer.import_path(
            os.path.join(FIXTURES_DIR, "sample_cdr.csv"),
            profile_path=os.path.join(BASE_DIR, "profiles", "cdr_profile.json"),
        )
        self.engine = AssociationEngine(self.db, self.case_id, self.operator_id)
        self.target = "081200000012"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_masking(self):
        res = self.engine.lookup(self.target, show_full=False)
        summary = res["summary"]
        details = res["details"]

        # Target harus tersamarkan
        self.assertEqual(summary["target"], "0812****0012")
        self.assertEqual(summary["masked_target"], "0812****0012")

        # Nomor terkait di details harus tersamarkan
        call_items = details["CALL_INTERACTION"]
        self.assertGreater(len(call_items), 0)
        for item in call_items:
            # canonical_number tetap tersimpan secara internal, tapi masked_number memuat bintang
            self.assertIn("****", item["masked_number"])

        # Ekspor ke JSON
        json_out = os.path.join(self.temp_dir, "report_masked.json")
        export_report(res, json_out, self.db, self.case_id, self.operator_id, show_full=False)
        with open(json_out, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("0812****0012", content)
            self.assertIn("Hasil berasal dari artefak yang diimpor dalam lingkup kasus ini", content)

    def test_explicit_show_full(self):
        res = self.engine.lookup(self.target, show_full=True)
        summary = res["summary"]

        # Target ditampilkan nomor lengkap kanonik
        self.assertEqual(summary["target"], "081200000012")

        # Ekspor ke CSV
        csv_out = os.path.join(self.temp_dir, "report_full.csv")
        export_report(res, csv_out, self.db, self.case_id, self.operator_id, show_full=True)
        with open(csv_out, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("081200000012", content)
            self.assertIn("081311112222", content)
            self.assertIn("Hasil berasal dari artefak yang diimpor dalam lingkup kasus ini", content)


if __name__ == "__main__":
    unittest.main()

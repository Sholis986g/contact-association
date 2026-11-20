"""
Pengujian pencarian kandidat nomor telepon berdasarkan suffix (4-8 digit).
"""
import os
import shutil
import tempfile
import unittest

from core.candidates import find_candidates
from core.db import Database
from core.importer import Importer

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(BASE_DIR, "fixtures")


class TestCandidates(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "cand_test.sqlite")
        self.db = Database(self.db_path)
        self.case_id = "CASE-CAND-001"
        self.operator_id = "OP-CAND"
        self.importer = Importer(self.db, self.case_id, self.operator_id)

        # Impor fixtures
        self.importer.import_path(
            os.path.join(FIXTURES_DIR, "sample_cdr.csv"),
            profile_path=os.path.join(BASE_DIR, "profiles", "cdr_profile.json"),
        )
        self.importer.import_path(
            os.path.join(FIXTURES_DIR, "sample_contacts.vcf"),
            contact_owner="081200000012",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_candidates_leading_zero(self):
        # Cari suffix '0012' (Target 081200000012 berakhir dengan 0012)
        res = find_candidates(self.db, self.case_id, self.operator_id, "0012")
        candidates = res["candidates"]
        self.assertEqual(len(candidates), 1)

        c = candidates[0]
        # Masking default
        self.assertEqual(c["masked_phone"], "0812****0012")
        self.assertGreater(c["source_count"], 0)
        self.assertGreater(c["total_events"], 0)
        self.assertIn("Operator tetap harus memilih nomor lengkap", res["note"])

    def test_candidates_multiple_matches(self):
        # Cari suffix '2222' (081311112222 di CDR dan 088800002222 di VCF)
        res = find_candidates(self.db, self.case_id, self.operator_id, "2222")
        candidates = res["candidates"]
        self.assertEqual(len(candidates), 2)
        phones = {c["masked_phone"] for c in candidates}
        self.assertIn("0813****2222", phones)
        self.assertIn("0888****2222", phones)


if __name__ == "__main__":
    unittest.main()

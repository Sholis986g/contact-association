"""
Pengujian integrasi antarmuka baris perintah (CLI) contact-association secara end-to-end.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_SCRIPT = os.path.join(BASE_DIR, "contact_association.py")
FIXTURES_DIR = os.path.join(BASE_DIR, "fixtures")


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "case_cli.sqlite")
        self.case_id = "CASE-CLI-001"
        self.operator_id = "OP-CLI-01"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_cli(self, args: list[str]) -> subprocess.CompletedProcess:
        cmd = [sys.executable, CLI_SCRIPT] + args
        return subprocess.run(cmd, capture_output=True, text=True, cwd=BASE_DIR)

    def test_cli_help(self):
        proc = self.run_cli(["--help"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("contact_association.py", proc.stdout)
        self.assertIn("import", proc.stdout)
        self.assertIn("candidates", proc.stdout)
        self.assertIn("lookup", proc.stdout)

    def test_cli_full_flow(self):
        # 1. CLI Import berkas CDR
        res_imp = self.run_cli([
            "import",
            "--db", self.db_path,
            "--case-id", self.case_id,
            "--operator-id", self.operator_id,
            "--input", os.path.join(FIXTURES_DIR, "sample_cdr.csv"),
            "--profile", os.path.join(BASE_DIR, "profiles", "cdr_profile.json"),
        ])
        self.assertEqual(res_imp.returncode, 0)
        self.assertIn("Total Peristiwa Diterima", res_imp.stdout)

        # 2. CLI Candidates dengan suffix 0012
        res_cand = self.run_cli([
            "candidates",
            "--db", self.db_path,
            "--case-id", self.case_id,
            "--operator-id", self.operator_id,
            "--suffix", "0012",
        ])
        self.assertEqual(res_cand.returncode, 0)
        self.assertIn("0812****0012", res_cand.stdout)

        # 3. CLI Lookup dengan rentang tanggal dan ekspor JSON
        export_file = os.path.join(self.temp_dir, "cli_report.json")
        res_look = self.run_cli([
            "lookup",
            "--db", self.db_path,
            "--case-id", self.case_id,
            "--operator-id", self.operator_id,
            "--target", "081200000012",
            "--from", "2024-01-01",
            "--to", "2024-01-31",
            "--export", export_file,
        ])
        self.assertEqual(res_look.returncode, 0)
        self.assertIn("LAPORAN ASOSIASI KONTAK FORENSIK DIGITAL", res_look.stdout)
        self.assertIn("0812****0012", res_look.stdout)
        self.assertTrue(os.path.exists(export_file))

        # 4. CLI Audit
        res_aud = self.run_cli([
            "audit",
            "--db", self.db_path,
            "--case-id", self.case_id,
        ])
        self.assertEqual(res_aud.returncode, 0)
        self.assertIn("IMPORT", res_aud.stdout)
        self.assertIn("CANDIDATES", res_aud.stdout)
        self.assertIn("LOOKUP", res_aud.stdout)

    def test_cli_invalid_target_and_failed_audit(self):
        # Jalankan lookup dengan nomor tidak valid
        res = self.run_cli([
            "lookup",
            "--db", self.db_path,
            "--case-id", self.case_id,
            "--operator-id", self.operator_id,
            "--target", "0812INVALID",
        ])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Target nomor telepon tidak valid", res.stderr)

        # Cek apakah kegagalan tercatat di audit log
        res_aud = self.run_cli([
            "audit",
            "--db", self.db_path,
            "--case-id", self.case_id,
        ])
        self.assertEqual(res_aud.returncode, 0)
        self.assertIn("FAILED", res_aud.stdout)


if __name__ == "__main__":
    unittest.main()

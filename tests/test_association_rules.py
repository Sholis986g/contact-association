"""
Pengujian 5 kategori aturan asosiasi forensik:
CALL_INTERACTION, DIRECT_MESSAGE, SAVED_CONTACT, SAME_ADDRESS_BOOK, SHARED_GROUP.
"""
import os
import shutil
import tempfile
import unittest

from core.db import Database
from core.importer import Importer
from core.engine import AssociationEngine
from core.models import AssociationCategory

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(BASE_DIR, "fixtures")


class TestAssociationRules(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_case.sqlite")
        self.db = Database(self.db_path)
        self.case_id = "CASE-TEST-001"
        self.operator_id = "OP-TEST"
        self.importer = Importer(self.db, self.case_id, self.operator_id)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_five_categories(self):
        # 1. Impor CDR (CALL_INTERACTION)
        self.importer.import_path(
            os.path.join(FIXTURES_DIR, "sample_cdr.csv"),
            profile_path=os.path.join(BASE_DIR, "profiles", "cdr_profile.json"),
        )

        # 2. Impor WhatsApp Private (DIRECT_MESSAGE)
        self.importer.import_path(
            os.path.join(FIXTURES_DIR, "sample_whatsapp_private.txt"),
        )

        # 3. Impor VCF Target (SAVED_CONTACT)
        self.importer.import_path(
            os.path.join(FIXTURES_DIR, "sample_contacts.vcf"),
            contact_owner="081200000012",
            target_account_basis="Perangkat disita dari saksi A atas nama target",
        )

        # 4. Impor VCF Shared Book (SAME_ADDRESS_BOOK)
        self.importer.import_path(
            os.path.join(FIXTURES_DIR, "sample_shared_contacts.vcf"),
        )

        # 5. Impor Telegram Group (SHARED_GROUP)
        self.importer.import_path(
            os.path.join(FIXTURES_DIR, "sample_telegram.json"),
        )

        engine = AssociationEngine(self.db, self.case_id, self.operator_id)
        target = "081200000012"
        res = engine.lookup(target)

        summary = res["summary"]
        details = res["details"]

        # Validasi Kategori 1: CALL_INTERACTION
        call_items = details[AssociationCategory.CALL_INTERACTION.value]
        self.assertGreater(len(call_items), 0)
        call_map = {item["canonical_number"]: item for item in call_items}

        # 081311112222 memiliki interaksi panggilan dengan target
        self.assertIn("081311112222", call_map)
        c_0813 = call_map["081311112222"]
        self.assertEqual(c_0813["event_count"], 4)
        self.assertEqual(c_0813["breakdown"]["incoming_calls"], 1)
        self.assertEqual(c_0813["breakdown"]["outgoing_calls"], 3)
        self.assertEqual(c_0813["breakdown"]["answered_calls"], 4)

        # 082199998888 memiliki panggilan tidak terjawab / attempted
        self.assertIn("082199998888", call_map)
        c_0821 = call_map["082199998888"]
        self.assertEqual(c_0821["breakdown"]["attempted_or_missed_calls"], 2)

        # Validasi Kategori 2: DIRECT_MESSAGE
        msg_items = details[AssociationCategory.DIRECT_MESSAGE.value]
        self.assertGreater(len(msg_items), 0)
        msg_map = {item["canonical_number"]: item for item in msg_items}

        # 081912345678 bertukar 4 pesan dalam 1 thread chat pribadi
        self.assertIn("081912345678", msg_map)
        m_0819 = msg_map["081912345678"]
        self.assertEqual(m_0819["event_count"], 4)
        self.assertEqual(m_0819["breakdown"]["message_count"], 4)
        self.assertEqual(m_0819["breakdown"]["thread_count"], 1)

        # Validasi Kategori 3: SAVED_CONTACT
        saved_items = details[AssociationCategory.SAVED_CONTACT.value]
        saved_map = {item["canonical_number"]: item for item in saved_items}

        # 087700001111 dan 088800002222 ada di buku kontak target
        self.assertIn("087700001111", saved_map)
        self.assertIn("088800002222", saved_map)
        s_0877 = saved_map["087700001111"]
        self.assertIn("Budi Mitra Bisnis", s_0877["breakdown"]["labels"])
        self.assertIn("Perangkat disita", s_0877["breakdown"]["target_account_basis"])

        # Validasi Kategori 4: SAME_ADDRESS_BOOK
        same_book_items = details[AssociationCategory.SAME_ADDRESS_BOOK.value]
        same_book_map = {item["canonical_number"]: item for item in same_book_items}

        # 085600003333 muncul di sample_shared_contacts.vcf bersama target tanpa bukti kepemilikan
        self.assertIn("085600003333", same_book_map)
        sb_0856 = same_book_map["085600003333"]
        self.assertEqual(sb_0856["breakdown"]["co_occurrence_type"], "SAME_ADDRESS_BOOK")
        # Pastikan tidak masuk ke CALL_INTERACTION atau DIRECT_MESSAGE
        self.assertNotIn("085600003333", call_map)
        self.assertNotIn("085600003333", msg_map)

        # Validasi Kategori 5: SHARED_GROUP
        group_items = details[AssociationCategory.SHARED_GROUP.value]
        group_map = {item["canonical_number"]: item for item in group_items}

        # 083811119999 dan 085277778888 di grup telegram yang sama
        self.assertIn("083811119999", group_map)
        self.assertIn("085277778888", group_map)
        g_0838 = group_map["083811119999"]
        self.assertIn("Konteks partisipasi grup bersama", g_0838["breakdown"]["forensic_note"])

        # Validasi Total Unik Global: Setiap nomor unik dihitung tepat satu kali
        all_unique = set()
        for c_items in details.values():
            for item in c_items:
                all_unique.add(item["canonical_number"])

        self.assertEqual(summary["total_unique_associated_numbers"], len(all_unique))


if __name__ == "__main__":
    unittest.main()

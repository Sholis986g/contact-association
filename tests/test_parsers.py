"""
Pengujian seluruh parser forensik: CSV, JSON, XLSX, VCF, dan WhatsApp Text.
"""
import os
import unittest
from core.parsers.csv_parser import CSVParser
from core.parsers.json_parser import JSONParser
from core.parsers.xlsx_parser import XLSXParser
from core.parsers.vcf_parser import VCFParser
from core.parsers.whatsapp_parser import WhatsAppParser

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(BASE_DIR, "fixtures")


class TestParsers(unittest.TestCase):
    def test_csv_parser(self):
        parser = CSVParser()
        csv_file = os.path.join(FIXTURES_DIR, "sample_cdr.csv")
        profile = {
            "event_type": "CALL",
            "mapping": {
                "party_a": "caller_msisdn",
                "party_b": "callee_msisdn",
                "timestamp": "call_datetime",
                "duration": "duration_sec",
                "direction": "call_direction",
                "status": "call_status",
                "event_id": "cdr_record_id"
            }
        }
        res = parser.parse(csv_file, profile=profile)
        self.assertGreater(len(res.events), 0)
        first_ev = res.events[0]
        self.assertEqual(first_ev.party_a, "081200000012")
        self.assertEqual(first_ev.party_b, "081311112222")
        self.assertEqual(first_ev.status, "ANSWERED")
        self.assertEqual(first_ev.event_id, "CDR-1001")

    def test_json_telegram_parser(self):
        parser = JSONParser()
        json_file = os.path.join(FIXTURES_DIR, "sample_telegram.json")
        res = parser.parse(json_file)
        self.assertEqual(len(res.events), 3)
        self.assertEqual(res.events[0].event_type, "GROUP_PARTICIPATION")
        self.assertEqual(res.events[0].party_a, "081200000012")
        self.assertEqual(res.events[1].party_a, "083811119999")
        self.assertEqual(res.events[2].party_a, "085277778888")

    def test_xlsx_parser_sheet_selection(self):
        parser = XLSXParser()
        xlsx_file = os.path.join(FIXTURES_DIR, "sample_workbook.xlsx")

        # 1. Baca sheet default (CDR_Log)
        res_cdr = parser.parse(xlsx_file, options={"sheet": "CDR_Log"})
        self.assertGreater(len(res_cdr.events), 0)
        self.assertEqual(res_cdr.events[0].party_a, "081200000012")
        self.assertEqual(res_cdr.events[0].party_b, "081122334455")

        # 2. Baca sheet SMS_Archive
        profile_sms = {
            "event_type": "MESSAGE",
            "mapping": {
                "party_a": "sender",
                "party_b": "recipient",
                "timestamp": "sent_date",
                "direction": "direction",
                "status": "status",
                "event_id": "sms_id"
            }
        }
        res_sms = parser.parse(xlsx_file, profile=profile_sms, options={"sheet": "SMS_Archive"})
        self.assertGreater(len(res_sms.events), 0)
        self.assertEqual(res_sms.events[0].party_a, "081200000012")
        self.assertEqual(res_sms.events[0].party_b, "081299991111")
        self.assertEqual(res_sms.events[0].event_type, "MESSAGE")

    def test_vcf_parser(self):
        parser = VCFParser()
        vcf_file = os.path.join(FIXTURES_DIR, "sample_contacts.vcf")
        res = parser.parse(vcf_file, options={"contact_owner": "081200000012", "target_account_basis": "HP Sitaan Target"})
        self.assertEqual(len(res.events), 2)
        phones = {e.party_b for e in res.events}
        self.assertIn("087700001111", phones)
        self.assertIn("088800002222", phones)
        self.assertEqual(res.events[0].contact_owner, "081200000012")
        self.assertEqual(res.events[0].target_account_basis, "HP Sitaan Target")

    def test_whatsapp_private_chat(self):
        parser = WhatsAppParser()
        txt_file = os.path.join(FIXTURES_DIR, "sample_whatsapp_private.txt")
        res = parser.parse(txt_file, options={"is_group": False})
        self.assertEqual(len(res.events), 4)
        senders = {e.party_a for e in res.events}
        self.assertIn("081200000012", senders)
        self.assertIn("081912345678", senders)
        for ev in res.events:
            self.assertEqual(ev.event_type, "MESSAGE")

    def test_whatsapp_group_chat_and_system_messages(self):
        parser = WhatsAppParser()
        txt_file = os.path.join(FIXTURES_DIR, "sample_whatsapp_group.txt")
        res = parser.parse(txt_file)
        # Baris pesan sistem enkripsi dan pembuatan grup dilewati dari event percakapan
        self.assertEqual(len(res.events), 3)
        for ev in res.events:
            self.assertEqual(ev.event_type, "GROUP_PARTICIPATION")
            self.assertIn(ev.party_a, ("081200000012", "081700004444", "081800005555"))


if __name__ == "__main__":
    unittest.main()

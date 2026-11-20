"""
Script pembuatan fixture sintetis XLSX untuk contact-association.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core.xlsx_creator import create_simple_xlsx


def generate_fixtures() -> None:
    xlsx_path = os.path.join(BASE_DIR, "fixtures", "sample_workbook.xlsx")

    sheets_data = {
        "CDR_Log": [
            ["party_a", "party_b", "timestamp", "duration", "direction", "status", "event_id"],
            ["081200000012", "081122334455", "2024-01-18 10:30:00", 150, "OUTGOING", "ANSWERED", "XLSX-001"],
            ["081122334455", "081200000012", "2024-01-19 15:45:00", 210, "INCOMING", "ANSWERED", "XLSX-002"],
            ["081200000012", "081122334455", "2024-01-22 09:12:00", 0, "OUTGOING", "MISSED", "XLSX-003"],
        ],
        "SMS_Archive": [
            ["sender", "recipient", "sent_date", "direction", "status", "sms_id"],
            ["081200000012", "081299991111", "2024-01-21 11:20:00", "OUTGOING", "DELIVERED", "SMS-001"],
            ["081299991111", "081200000012", "2024-01-21 11:22:00", "INCOMING", "DELIVERED", "SMS-002"],
        ]
    }

    create_simple_xlsx(xlsx_path, sheets_data)
    print(f"[✓] Created sample workbook at: {xlsx_path}")


if __name__ == "__main__":
    generate_fixtures()

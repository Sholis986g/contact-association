"""
Modul ekspor hasil analisis asosiasi ke format JSON dan CSV.
Menerapkan masking nomor telepon secara default (kecuali --show-full diberikan).
Selalu menyertakan disclaimer forensik resmi.
"""
from __future__ import annotations
import csv
from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List
from .db import Database
from .models import AuditEntry, DISCLAIMER_TEXT


def export_report(
    lookup_result: Dict[str, Any],
    export_path: str,
    db: Database,
    case_id: str,
    operator_id: str,
    show_full: bool = False,
) -> str:
    """
    Mengekspor laporan asosiasi ke berkas JSON atau CSV berdasarkan ekstensi file.
    """
    ext = os.path.splitext(export_path)[1].lower()

    if ext == ".csv":
        _export_to_csv(lookup_result, export_path, show_full)
    else:
        _export_to_json(lookup_result, export_path)

    # Catat audit log ekspor
    audit_entry = AuditEntry(
        timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        operator_id=operator_id,
        case_id=case_id,
        action_type="EXPORT",
        masked_target=lookup_result.get("summary", {}).get("masked_target"),
        filters_applied={"export_path": export_path, "format": ext, "show_full": show_full},
        result_count=lookup_result.get("summary", {}).get("total_unique_associated_numbers", 0),
        execution_status="SUCCESS",
        error_message=None,
    )
    db.log_audit(audit_entry)

    return export_path


def _export_to_json(data: Dict[str, Any], export_path: str) -> None:
    export_payload = {
        "_metadata": {
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool": "contact-association",
            "version": "1.0.0",
            "legal_disclaimer": DISCLAIMER_TEXT,
        },
        "summary": data.get("summary"),
        "details": data.get("details"),
        "undated_records_separated": data.get("undated_records_separated", 0),
    }

    os.makedirs(os.path.dirname(os.path.abspath(export_path)), exist_ok=True)
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(export_payload, f, indent=2, ensure_ascii=False)


def _export_to_csv(data: Dict[str, Any], export_path: str, show_full: bool) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(export_path)), exist_ok=True)

    summary = data.get("summary", {})
    details = data.get("details", {})

    fieldnames = [
        "case_id",
        "target_number",
        "category",
        "associated_number",
        "operator",
        "operator_source",
        "event_count",
        "first_activity_utc",
        "last_activity_utc",
        "breakdown_summary",
        "evidence_references",
        "disclaimer",
    ]

    target_num = summary.get("target") if show_full else summary.get("masked_target")

    with open(export_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for category_name, items in details.items():
            for item in items:
                num = item.get("canonical_number") if show_full else item.get("masked_number")
                bdown = item.get("breakdown", {})
                bdown_str = "; ".join(f"{k}={v}" for k, v in bdown.items() if not isinstance(v, (list, dict)))
                refs_str = " | ".join(item.get("evidence_refs", []))

                writer.writerow({
                    "case_id": summary.get("case_id"),
                    "target_number": target_num,
                    "category": category_name,
                    "associated_number": num,
                    "operator": item.get("operator_name"),
                    "operator_source": item.get("operator_source"),
                    "event_count": item.get("event_count"),
                    "first_activity_utc": item.get("first_activity_utc") or "-",
                    "last_activity_utc": item.get("last_activity_utc") or "-",
                    "breakdown_summary": bdown_str,
                    "evidence_references": refs_str,
                    "disclaimer": DISCLAIMER_TEXT,
                })

"""
Modul pencarian kandidat nomor telepon berdasarkan suffix digit belakang (4-8 digit).
Mempertahankan leading zeros dan menyamarkan nomor secara default.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from .db import Database
from .models import AuditEntry
from .normalizer import infer_operator, mask_phone, validate_suffix


def find_candidates(
    db: Database,
    case_id: str,
    operator_id: str,
    suffix_input: str,
    show_full: bool = False,
) -> Dict[str, Any]:
    """
    Mencari daftar nomor telepon yang berakhiran dengan suffix tertentu.
    Operator tetap harus memilih nomor lengkap sebelum melakukan lookup forensik.
    """
    clean_suffix = validate_suffix(suffix_input)

    query = """
    WITH matched_numbers AS (
        SELECT party_a AS phone, source_name, id FROM events
        WHERE case_id = ? AND party_a IS NOT NULL AND party_a LIKE ?
        UNION ALL
        SELECT party_b AS phone, source_name, id FROM events
        WHERE case_id = ? AND party_b IS NOT NULL AND party_b LIKE ?
    )
    SELECT
        phone,
        COUNT(DISTINCT source_name) as source_count,
        COUNT(id) as total_events
    FROM matched_numbers
    WHERE phone LIKE ?
    GROUP BY phone
    ORDER BY total_events DESC, phone ASC
    """

    pattern = f"%{clean_suffix}"
    candidates_list: List[Dict[str, Any]] = []

    with db.get_connection() as conn:
        rows = conn.execute(query, (case_id, pattern, case_id, pattern, pattern)).fetchall()
        for r in rows:
            canonical = r["phone"]
            op_name, op_src = infer_operator(canonical)
            candidates_list.append({
                "canonical_phone": canonical if show_full else mask_phone(canonical, show_full=False),
                "masked_phone": mask_phone(canonical, show_full=False),
                "full_phone_available": show_full,
                "source_count": r["source_count"],
                "total_events": r["total_events"],
                "operator": op_name,
                "operator_source": op_src,
            })

    # Catat audit log
    audit_entry = AuditEntry(
        timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        operator_id=operator_id,
        case_id=case_id,
        action_type="CANDIDATES",
        masked_target=f"****{clean_suffix}",
        filters_applied={"suffix": clean_suffix, "show_full": show_full},
        result_count=len(candidates_list),
        execution_status="SUCCESS",
        error_message=None,
    )
    db.log_audit(audit_entry)

    return {
        "case_id": case_id,
        "operator_id": operator_id,
        "suffix_queried": clean_suffix,
        "total_candidates": len(candidates_list),
        "candidates": candidates_list,
        "note": "Operator tetap harus memilih nomor lengkap sebelum menjalankan 'lookup'."
    }

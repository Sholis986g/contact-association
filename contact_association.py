#!/usr/bin/env python3
"""
contact-association: Alat Forensik Digital Asosiasi Kontak Nomor Telepon Indonesia.
Menerima target nomor telepon seluler Indonesia dan menghasilkan daftar nomor terkait
berdasarkan artefak bukti lokal yang diperoleh secara sah dan berada dalam lingkup kasus.

Sesuai standar forensik digital:
- Operasi 100% offline tanpa scraping / lookup operator eksternal.
- ACID transaksi SQLite dengan query berparameter.
- Masking nomor secara default (opsi eksplisit --show-full untuk penanganan bukti internal).
- Audit trail lengkap mencatat seluruh kueri dan kegagalan.
- Penegakan integritas bukti berbasis SHA-256 dan pencegahan duplikasi.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
import sqlite3
import sys

# Tambahkan direktori root modul ke sys.path jika belum ada
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from core.db import Database
from core.models import AuditEntry, DISCLAIMER_TEXT
from core.normalizer import (
    ValidationError,
    mask_phone,
    normalize_and_validate_phone,
    validate_suffix,
)
from core.importer import Importer
from core.candidates import find_candidates
from core.engine import AssociationEngine
from core.exporter import export_report
from core.terminal import clean, fields, heading, table


def cmd_import(args: argparse.Namespace) -> int:
    """Subperintah import: Mengimpor berkas atau folder bukti artefak forensik."""
    db = Database(args.db)
    importer = Importer(db, case_id=args.case_id, operator_id=args.operator_id)

    heading("IMPOR BUKTI")
    context = [("Kasus", args.case_id), ("Operator", args.operator_id), ("Input", args.input)]
    if args.profile:
        context.append(("Profil", args.profile))
    if args.sheet:
        context.append(("Sheet", args.sheet))
    fields(*context)

    try:
        report = importer.import_path(
            input_path=args.input,
            profile_path=args.profile,
            sheet_name=args.sheet,
            source_device=args.source_device,
            contact_owner=args.contact_owner,
            target_account_basis=args.target_basis,
        )

        heading("HASIL IMPOR", level=2)
        fields(
            ("Total Peristiwa Diterima", report.total_accepted),
            ("Baris ditolak", report.total_rejected),
            ("Peristiwa duplikat", report.deduplicated_events),
            ("Berkas duplikat", report.duplicates_skipped),
        )
        table(
            ("Berkas", "Diterima", "Ditolak", "Status", "Awal SHA-256"),
            ((p["file"], p["accepted"], p["rejected"], p["status"], p["sha256"][:16])
             for p in report.processed_files),
        )
        if report.skipped_files:
            heading("BERKAS DILEWATI", level=2)
            table(("Berkas", "Alasan"), ((s["file"], s["reason"]) for s in report.skipped_files))
        return 0
    except Exception as e:
        print(f"\n[!] Kesalahan fatal saat mengimpor bukti: {clean(e)}", file=sys.stderr)
        return 1


def cmd_candidates(args: argparse.Namespace) -> int:
    """Subperintah candidates: Pencarian nomor kandidat berdasarkan suffix digit belakang."""
    db = Database(args.db)

    try:
        clean_suffix = validate_suffix(args.suffix)
    except ValidationError as ve:
        # Catat kegagalan ke audit log
        audit_entry = AuditEntry(
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            operator_id=args.operator_id,
            case_id=args.case_id,
            action_type="CANDIDATES",
            masked_target=f"****{args.suffix}",
            filters_applied={"suffix": str(args.suffix)},
            result_count=0,
            execution_status="FAILED",
            error_message=str(ve),
        )
        db.log_audit(audit_entry)
        print(f"\n[!] Parameter suffix tidak valid: {clean(ve)}", file=sys.stderr)
        return 1

    try:
        res = find_candidates(
            db=db,
            case_id=args.case_id,
            operator_id=args.operator_id,
            suffix_input=clean_suffix,
            show_full=args.show_full,
        )

        candidates = res["candidates"]
        heading("KANDIDAT NOMOR")
        fields(
            ("Kasus", args.case_id),
            ("Akhiran", clean_suffix),
            ("Total kandidat", len(candidates)),
            ("Mode", "Nomor lengkap" if args.show_full else "Tersamarkan"),
        )
        if candidates:
            print()
            table(
                ("No.", "Nomor", "Sumber", "Peristiwa", "Operator dari prefix"),
                ((index, c["canonical_phone"] if args.show_full else c["masked_phone"],
                  c["source_count"], c["total_events"], c["operator"])
                 for index, c in enumerate(candidates, 1)),
            )
            print(f"\n  {clean(res['note'])}")
        else:
            print("\n  Tidak ada kandidat pada kasus ini.")

        if args.export:
            export_payload = {
                "summary": {
                    "case_id": args.case_id,
                    "operator_id": args.operator_id,
                    "suffix_queried": clean_suffix,
                    "total_candidates": len(candidates),
                },
                "candidates": candidates,
                "disclaimer": DISCLAIMER_TEXT,
            }
            with open(args.export, "w", encoding="utf-8") as f:
                json.dump(export_payload, f, indent=2, ensure_ascii=False)
            print(f"[OK] Hasil kandidat diekspor ke: {clean(args.export)}\n")

        return 0
    except Exception as e:
        audit_entry = AuditEntry(
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            operator_id=args.operator_id,
            case_id=args.case_id,
            action_type="CANDIDATES",
            masked_target=f"****{clean_suffix}",
            filters_applied={"suffix": clean_suffix},
            result_count=0,
            execution_status="ERROR",
            error_message=str(e),
        )
        db.log_audit(audit_entry)
        print(f"\n[!] Terjadi kesalahan saat mencari kandidat: {clean(e)}", file=sys.stderr)
        return 1


def cmd_lookup(args: argparse.Namespace) -> int:
    """Subperintah lookup: Analisis asosiasi nomor kontak 1-langkah terhadap nomor target."""
    db = Database(args.db)

    # Validasi dan normalisasi target
    try:
        canonical_target = normalize_and_validate_phone(args.target)
    except ValidationError as ve:
        audit_entry = AuditEntry(
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            operator_id=args.operator_id,
            case_id=args.case_id,
            action_type="LOOKUP",
            masked_target=mask_phone(str(args.target)[:16], show_full=False),
            filters_applied={"from": getattr(args, 'from_date', None), "to": args.to},
            result_count=0,
            execution_status="FAILED",
            error_message=str(ve),
        )
        db.log_audit(audit_entry)
        print(f"\n[!] Target nomor telepon tidak valid: {clean(ve)}", file=sys.stderr)
        return 1

    engine = AssociationEngine(db, case_id=args.case_id, operator_id=args.operator_id)

    try:
        from_val = getattr(args, "from_date", None)
        res = engine.lookup(
            target_raw=canonical_target,
            from_date_str=from_val,
            to_date_str=args.to,
            show_full=args.show_full,
        )

        summary = res["summary"]
        details = res["details"]
        undated_sep = res["undated_records_separated"]

        heading("LAPORAN ASOSIASI KONTAK FORENSIK DIGITAL")
        fields(
            ("Kasus", summary["case_id"]),
            ("Operator", summary["operator_id"]),
            ("Target", summary["target"]),
            ("Periode", f"{from_val or 'Awal'} sampai {args.to or 'Akhir'}"),
            ("Sumber bukti", ", ".join(summary["sources_analyzed"]) or "Tidak ada berkas"),
            ("Total nomor unik terkait", summary["total_unique_associated_numbers"]),
        )
        if undated_sep:
            print(f"  Bukti tanpa tanggal di luar filter periode: {undated_sep}")
        if not summary["total_unique_associated_numbers"]:
            print("\n  Tidak ada asosiasi pada bukti dan periode yang dipilih.")

        for category, items in details.items():
            if not items:
                continue
            heading(f"{category} ({len(items)})", level=2)
            table(
                ("No.", "Nomor terkait", "Peristiwa", "Aktivitas terakhir", "Operator dari prefix"),
                ((index, item["canonical_number"] if args.show_full else item["masked_number"],
                  item["event_count"], item["last_activity_utc"] or "-", item["operator_name"])
                 for index, item in enumerate(items, 1)),
            )
            for index, item in enumerate(items, 1):
                breakdown = item["breakdown"]
                metadata = []
                for key, value in breakdown.items():
                    if key in ("thread_ids", "group_ids", "sources") or not value and value != 0:
                        continue
                    if key == "labels":
                        value = ", ".join(value)
                    metadata.append(f"{key.replace('_', ' ')}: {value}")
                refs = item["evidence_refs"]
                evidence = ", ".join(refs[:3])
                if len(refs) > 3:
                    evidence += f" (+{len(refs) - 3} rujukan)"
                print(f"    [{index}] Bukti: {clean(evidence or '-')}")
                if metadata:
                    print(f"        {clean(' | '.join(metadata))}")

        print(f"\n  {clean(summary['disclaimer'])}\n")

        if args.export:
            export_path = export_report(
                lookup_result=res,
                export_path=args.export,
                db=db,
                case_id=args.case_id,
                operator_id=args.operator_id,
                show_full=args.show_full,
            )
            print(f"[OK] Laporan forensik berhasil diekspor ke: {clean(export_path)}\n")

        return 0
    except Exception as e:
        audit_entry = AuditEntry(
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            operator_id=args.operator_id,
            case_id=args.case_id,
            action_type="LOOKUP",
            masked_target=mask_phone(canonical_target, show_full=False),
            filters_applied={"from": from_val, "to": args.to, "show_full": args.show_full},
            result_count=0,
            execution_status="ERROR",
            error_message=str(e),
        )
        db.log_audit(audit_entry)
        print(f"\n[!] Terjadi kesalahan saat lookup: {clean(e)}", file=sys.stderr)
        return 1


def cmd_audit(args: argparse.Namespace) -> int:
    """Subperintah audit: Menampilkan jejak audit log forensik."""
    db = Database(args.db)
    heading("JEJAK AUDIT FORENSIK")
    fields(("Database", args.db), ("Kasus", args.case_id or "Semua"))
    print()
    query = "SELECT * FROM audit_logs"
    params = []
    if args.case_id:
        query += " WHERE case_id = ?"
        params.append(args.case_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(args.limit)

    with db.get_connection() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

    if not rows:
        print("    [!] Belum ada catatan audit log.\n")
        return 0

    table(
        ("ID", "Waktu (UTC)", "Operator", "Kasus", "Aksi", "Status", "Target", "Hasil"),
        ((r["id"], r["timestamp_utc"], r["operator_id"], r["case_id"],
          r["action_type"], r["execution_status"], r["masked_target"] or "-", r["result_count"])
         for r in rows),
    )
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contact_association.py",
        description="Alat forensik digital analisis asosiasi kontak telepon seluler Indonesia berbasis SQLite offline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Alur penggunaan: import -> candidates (jika perlu) -> lookup -> audit\n"
            "Gunakan --help pada setiap subperintah untuk melihat parameternya.\n"
            "Pemrosesan offline. Operator ID dinyatakan oleh pengguna CLI."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", help="Subperintah yang tersedia")

    # Subcommand: import
    p_import = subparsers.add_parser("import", help="Impor berkas atau folder bukti artefak forensik")
    p_import.add_argument("--db", required=True, help="Jalur berkas database SQLite kasus")
    p_import.add_argument("--case-id", required=True, help="ID kasus forensik")
    p_import.add_argument("--operator-id", required=True, help="Identitas operator yang dinyatakan pengguna CLI")
    p_import.add_argument("--input", required=True, help="Jalur berkas atau folder artefak bukti")
    p_import.add_argument("--profile", help="Jalur berkas JSON pemetaan profil kolom/ekstrak")
    p_import.add_argument("--sheet", help="Nama atau indeks sheet Excel untuk berkas XLSX")
    p_import.add_argument("--source-device", help="Label perangkat sumber (contoh: 'HP Target', 'Eksport-CDR')")
    p_import.add_argument("--contact-owner", help="Nomor pemilik buku kontak jika kepemilikan terbukti")
    p_import.add_argument("--target-basis", help="Dasar hukum/metadata pengaitan perangkat/akun dengan target")

    # Subcommand: candidates
    p_cand = subparsers.add_parser("candidates", help="Pencarian kandidat nomor telepon berdasarkan suffix digit belakang (4-8 digit)")
    p_cand.add_argument("--db", required=True, help="Jalur berkas database SQLite kasus")
    p_cand.add_argument("--case-id", required=True, help="ID kasus forensik")
    p_cand.add_argument("--operator-id", required=True, help="Identitas operator yang dinyatakan pengguna CLI")
    p_cand.add_argument("--suffix", required=True, help="Suffix nomor 4-8 digit (mempertahankan leading zero, misal: 0012)")
    p_cand.add_argument("--show-full", action="store_true", help="Tampilkan nomor lengkap untuk penanganan bukti internal")
    p_cand.add_argument("--export", help="Jalur berkas ekspor hasil (JSON)")

    # Subcommand: lookup
    p_look = subparsers.add_parser("lookup", help="Analisis asosiasi kontak satu langkah untuk nomor target kanonik")
    p_look.add_argument("--db", required=True, help="Jalur berkas database SQLite kasus")
    p_look.add_argument("--case-id", required=True, help="ID kasus forensik")
    p_look.add_argument("--operator-id", required=True, help="Identitas operator yang dinyatakan pengguna CLI")
    p_look.add_argument("--target", required=True, help="Nomor telepon seluler target (08..., +628..., atau 628...)")
    p_look.add_argument("--from", dest="from_date", help="Batas awal periode tanggal (YYYY-MM-DD atau ISO)")
    p_look.add_argument("--to", help="Batas akhir periode tanggal inklusif (YYYY-MM-DD atau ISO)")
    p_look.add_argument("--show-full", action="store_true", help="Tampilkan nomor lengkap untuk penanganan bukti internal")
    p_look.add_argument("--export", help="Jalur berkas ekspor hasil laporan (JSON atau CSV)")

    # Subcommand: audit
    p_audit = subparsers.add_parser("audit", help="Tampilkan log audit forensik")
    p_audit.add_argument("--db", required=True, help="Jalur berkas database SQLite kasus")
    p_audit.add_argument("--case-id", help="Filter berdasarkan ID kasus")
    p_audit.add_argument("--limit", type=int, default=50, help="Jumlah entri audit maksimum (default: 50)")

    return parser


def main() -> None:
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    commands = {
        "import": cmd_import,
        "candidates": cmd_candidates,
        "lookup": cmd_lookup,
        "audit": cmd_audit,
    }
    try:
        exit_code = commands[args.command](args)
    except (OSError, sqlite3.Error) as error:
        print(f"\n[!] Gagal mengakses berkas atau database: {clean(error)}", file=sys.stderr)
        exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

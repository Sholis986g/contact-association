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
import sys
from typing import Any, Dict, List, Optional

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


def print_banner() -> None:
    banner = """
========================================================================
 CONTACT-ASSOCIATION: Digital Forensics Phone Number Association Tool
 Offline Evidence Analysis & Association Engine (Python 3.10+ / SQLite)
========================================================================
"""
    print(banner.strip())


def cmd_import(args: argparse.Namespace) -> int:
    """Subperintah import: Mengimpor berkas atau folder bukti artefak forensik."""
    db = Database(args.db)
    importer = Importer(db, case_id=args.case_id, operator_id=args.operator_id)

    print(f"\n[+] Memulai impor bukti forensik...")
    print(f"    Kasus ID     : {args.case_id}")
    print(f"    Operator ID  : {args.operator_id} (identitas yang dinyatakan pengguna CLI)")
    print(f"    Jalur Input  : {args.input}")
    if args.profile:
        print(f"    Profil Impor : {args.profile}")
    if args.sheet:
        print(f"    Lembar Sheet : {args.sheet}")

    try:
        report = importer.import_path(
            input_path=args.input,
            profile_path=args.profile,
            sheet_name=args.sheet,
            source_device=args.source_device,
            contact_owner=args.contact_owner,
            target_account_basis=args.target_basis,
        )

        print(f"\n[+] Hasil Impor:")
        print(f"    Total Peristiwa Diterima : {report.total_accepted}")
        print(f"    Total Baris Ditolak      : {report.total_rejected}")
        print(f"    Peristiwa Terdeduplikasi : {report.deduplicated_events}")
        print(f"    Berkas Duplikat Dilewati : {report.duplicates_skipped}")

        if report.processed_files:
            print(f"\n    Daftar Berkas Diproses:")
            for p in report.processed_files:
                print(f"      - {p['file']} (SHA-256: {p['sha256'][:16]}...): {p['accepted']} diterima, {p['rejected']} ditolak [Status: {p['status']}]")

        if report.skipped_files:
            print(f"\n    [!] Berkas Dilewati:")
            for s in report.skipped_files:
                print(f"      - {s['file']}: {s['reason']}")

        print(f"\n[✓] Impor selesai dengan integritas data terverifikasi.\n")
        return 0
    except Exception as e:
        print(f"\n[!] Kesalahan fatal saat mengimpor bukti: {str(e)}", file=sys.stderr)
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
        print(f"\n[!] Parameter suffix tidak valid: {str(ve)}", file=sys.stderr)
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
        print(f"\n[+] Hasil Pencarian Kandidat Suffix '{clean_suffix}':")
        print(f"    Kasus ID         : {args.case_id}")
        print(f"    Operator ID      : {args.operator_id} (identitas yang dinyatakan pengguna CLI)")
        print(f"    Total Ditemukan  : {len(candidates)}")
        print(f"    Mode Penampilan  : {'Nomor Lengkap (--show-full)' if args.show_full else 'Tersamarkan (Default)'}\n")

        if not candidates:
            print("    [!] Tidak ditemukan nomor telepon dengan suffix tersebut pada kasus ini.\n")
        else:
            print(f"    {'No.':<4} {'Nomor Telepon':<22} {'Jml Sumber':<12} {'Jml Event':<12} {'Operator (Inferensi)':<20}")
            print("    " + "-" * 72)
            for idx, c in enumerate(candidates, 1):
                phone_disp = c["canonical_phone"] if args.show_full else c["masked_phone"]
                op_label = f"{c['operator']} ({c['operator_source']})" if c['operator'] != "Tidak diketahui" else "Tidak diketahui"
                print(f"    {idx:<4} {phone_disp:<22} {c['source_count']:<12} {c['total_events']:<12} {op_label:<20}")

            print(f"\n    [CATATAN FORENSIK]: {res['note']}\n")

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
            print(f"[✓] Hasil kandidat diekspor ke: {args.export}\n")

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
        print(f"\n[!] Terjadi kesalahan saat mencari kandidat: {str(e)}", file=sys.stderr)
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
        print(f"\n[!] Target nomor telepon tidak valid: {str(ve)}", file=sys.stderr)
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

        print(f"\n========================================================================")
        print(f" LAPORAN ASOSIASI KONTAK FORENSIK DIGITAL")
        print(f"========================================================================")
        print(f"  ID Kasus                 : {summary['case_id']}")
        print(f"  Operator ID              : {summary['operator_id']} (identitas yang dinyatakan pengguna CLI)")
        print(f"  Nomor Target             : {summary['target']}")
        print(f"  Filter Periode           : Dari={from_val or 'Semua'} s/d Sampai={args.to or 'Semua'}")
        print(f"  Sumber yang Dianalisis   : {', '.join(summary['sources_analyzed']) or 'Tidak ada berkas'}")
        print(f"  Total Nomor Unik Terkait : {summary['total_unique_associated_numbers']}")
        print(f"------------------------------------------------------------------------")
        print(f"  Distribusi Kategori Asosiasi:")
        for cat_name, cnt in summary["category_counts"].items():
            print(f"    - {cat_name:<22}: {cnt} nomor")
        if undated_sep > 0:
            print(f"  [Catatan Periode]: Terdapat {undated_sep} rekaman tanpa timestamp yang dipisahkan saat filter tanggal aktif.")
        print(f"------------------------------------------------------------------------\n")

        # Tampilkan rincian per kategori
        for cat_name, items in details.items():
            if not items:
                continue
            print(f"[*] KATEGORI: {cat_name} ({len(items)} entri)")
            print(f"    {'No.':<4} {'Nomor Terkait':<22} {'Jml Event':<12} {'Aktivitas Terakhir':<22} {'Operator':<18}")
            print("    " + "-" * 76)
            for idx, item in enumerate(items, 1):
                phone_disp = item["canonical_number"] if args.show_full else item["masked_number"]
                last_act = item["last_activity_utc"] or "-"
                op_label = f"{item['operator_name']}" if item['operator_name'] != "Tidak diketahui" else "Tidak diketahui"
                print(f"    {idx:<4} {phone_disp:<22} {item['event_count']:<12} {last_act:<22} {op_label:<18}")

                # Tampilkan rincian breakdown forensik
                bdown = item["breakdown"]
                bdown_parts = []
                for k, v in bdown.items():
                    if k in ("forensic_note", "thread_ids", "group_ids", "sources", "labels"):
                        continue
                    bdown_parts.append(f"{k}: {v}")
                if bdown_parts:
                    print(f"         Detail: {', '.join(bdown_parts)}")

                if "labels" in bdown and bdown["labels"]:
                    print(f"         Label Kontak: {', '.join(bdown['labels'])}")
                if "target_account_basis" in bdown and bdown["target_account_basis"]:
                    print(f"         Dasar Pengaitan: {bdown['target_account_basis']}")
                if "forensic_note" in bdown:
                    print(f"         Catatan Forensik: {bdown['forensic_note']}")
                print(f"         Rujukan Bukti: {', '.join(item['evidence_refs'][:3])}{' ...' if len(item['evidence_refs']) > 3 else ''}")
            print()

        print(f"[!] PERNYATAAN FORENSIK:")
        print(f"    \"{summary['disclaimer']}\"\n")

        if args.export:
            export_path = export_report(
                lookup_result=res,
                export_path=args.export,
                db=db,
                case_id=args.case_id,
                operator_id=args.operator_id,
                show_full=args.show_full,
            )
            print(f"[✓] Laporan forensik berhasil diekspor ke: {export_path}\n")

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
        print(f"\n[!] Terjadi kesalahan saat lookup: {str(e)}", file=sys.stderr)
        return 1


def cmd_audit(args: argparse.Namespace) -> int:
    """Subperintah audit: Menampilkan jejak audit log forensik."""
    db = Database(args.db)
    print(f"\n[+] Jejak Audit Forensik (Database: {args.db}):")
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

    print(f"    {'ID':<5} {'Timestamp (UTC)':<22} {'Operator':<12} {'Case ID':<12} {'Aksi':<12} {'Status':<10} {'Target':<16} {'Hasil':<6}")
    print("    " + "-" * 95)
    for r in rows:
        tgt = r["masked_target"] or "-"
        print(f"    {r['id']:<5} {r['timestamp_utc']:<22} {r['operator_id']:<12} {r['case_id']:<12} {r['action_type']:<12} {r['execution_status']:<10} {tgt:<16} {r['result_count']:<6}")
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contact_association.py",
        description="Alat forensik digital analisis asosiasi kontak telepon seluler Indonesia berbasis SQLite offline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Catatan Forensik: Operator ID adalah identitas yang dinyatakan pengguna CLI. Seluruh pemrosesan dilakukan offline."
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

    if args.command == "import":
        sys.exit(cmd_import(args))
    elif args.command == "candidates":
        sys.exit(cmd_candidates(args))
    elif args.command == "lookup":
        sys.exit(cmd_lookup(args))
    elif args.command == "audit":
        sys.exit(cmd_audit(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

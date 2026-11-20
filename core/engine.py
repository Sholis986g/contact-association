"""
Mesin analisis asosiasi kontak forensik (Association Engine).
Menerapkan 5 aturan klasifikasi:
- CALL_INTERACTION
- DIRECT_MESSAGE
- SAVED_CONTACT
- SAME_ADDRESS_BOOK
- SHARED_GROUP
Dengan dukungan filter tanggal inklusif, pemisahan data tanpa tanggal,
ranking deterministik, dan agregasi rujukan bukti forensik.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Set, Tuple

from .db import Database
from .models import (
    AssociationCategory,
    AssociationDetail,
    CaseSummary,
    DISCLAIMER_TEXT,
    AuditEntry,
)
from .normalizer import (
    infer_operator,
    mask_phone,
    normalize_and_validate_phone,
)
from .time_utils import parse_date_boundary


class AssociationEngine:
    def __init__(self, db: Database, case_id: str, operator_id: str):
        self.db = db
        self.case_id = case_id
        self.operator_id = operator_id

    def lookup(
        self,
        target_raw: str,
        from_date_str: Optional[str] = None,
        to_date_str: Optional[str] = None,
        show_full: bool = False,
    ) -> Dict[str, Any]:
        """
        Menjalankan analisis asosiasi 1-langkah untuk nomor target kanonik.
        """
        canonical_target = normalize_and_validate_phone(target_raw)

        # Parse filter tanggal
        from_iso, from_dt = parse_date_boundary(from_date_str, is_end_date=False)
        to_iso, to_dt = parse_date_boundary(to_date_str, is_end_date=True)

        date_filter_active = bool(from_iso or to_iso)

        # Kumpulkan seluruh sumber berkas/device yang dianalisis pada kasus ini
        sources_analyzed: List[str] = []
        with self.db.get_connection() as conn:
            rows_src = conn.execute(
                "SELECT DISTINCT file_name FROM manifests WHERE case_id = ?",
                (self.case_id,),
            ).fetchall()
            sources_analyzed = [r["file_name"] for r in rows_src]

        # Ambil seluruh event yang relevan dari database
        # 1. CALL_INTERACTION
        call_details, call_undated = self._analyze_calls(canonical_target, from_iso, to_iso, show_full)

        # 2. DIRECT_MESSAGE
        msg_details, msg_undated = self._analyze_direct_messages(canonical_target, from_iso, to_iso, show_full)

        # 3. SAVED_CONTACT & 4. SAME_ADDRESS_BOOK
        saved_details, same_book_details, contact_undated = self._analyze_contacts(
            canonical_target, from_iso, to_iso, show_full
        )

        # 5. SHARED_GROUP
        group_details, group_undated = self._analyze_shared_groups(canonical_target, from_iso, to_iso, show_full)

        # Gabungkan semua detail
        all_details = {
            AssociationCategory.CALL_INTERACTION.value: call_details,
            AssociationCategory.DIRECT_MESSAGE.value: msg_details,
            AssociationCategory.SAVED_CONTACT.value: saved_details,
            AssociationCategory.SAME_ADDRESS_BOOK.value: same_book_details,
            AssociationCategory.SHARED_GROUP.value: group_details,
        }

        # Hitung nomor unik secara global (satu nomor dihitung sekali pada total global)
        unique_associated_numbers: Set[str] = set()
        for cat_name, details_list in all_details.items():
            for d in details_list:
                unique_associated_numbers.add(d["canonical_number"])

        total_undated = call_undated + msg_undated + contact_undated + group_undated

        category_counts = {
            cat_name: len(details_list)
            for cat_name, details_list in all_details.items()
        }

        masked_target = mask_phone(canonical_target, show_full=show_full)

        summary = {
            "case_id": self.case_id,
            "operator_id": self.operator_id,
            "target": canonical_target if show_full else masked_target,
            "masked_target": mask_phone(canonical_target, show_full=False),
            "date_filter": {
                "active": date_filter_active,
                "from": from_iso,
                "to": to_iso,
            },
            "sources_analyzed": sources_analyzed,
            "total_unique_associated_numbers": len(unique_associated_numbers),
            "category_counts": category_counts,
            "undated_records_count": total_undated,
            "disclaimer": DISCLAIMER_TEXT,
        }

        # Catat audit log kueri
        audit_entry = AuditEntry(
            timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            operator_id=self.operator_id,
            case_id=self.case_id,
            action_type="LOOKUP",
            masked_target=mask_phone(canonical_target, show_full=False),
            filters_applied={
                "from": from_iso,
                "to": to_iso,
                "show_full": show_full,
            },
            result_count=len(unique_associated_numbers),
            execution_status="SUCCESS",
            error_message=None,
        )
        self.db.log_audit(audit_entry)

        return {
            "summary": summary,
            "details": all_details,
            "undated_records_separated": total_undated if date_filter_active else 0,
        }

    def _sort_category_details(self, details_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Urutkan hasil dalam kategori berdasarkan:
        1. Jumlah peristiwa unik (descending)
        2. Tanggal aktivitas terakhir (descending, tanpa tanggal diletakkan di akhir)
        3. Nomor kanonik (ascending, penentu urutan deterministik)
        """
        def get_sort_key(item: Dict[str, Any]) -> Tuple[int, float, str]:
            cnt = item.get("event_count", 0)
            last_ts = item.get("last_activity_utc")
            if last_ts:
                try:
                    epoch = datetime.fromisoformat(last_ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    epoch = 0.0
            else:
                epoch = -1.0  # Tanpa tanggal diletakkan di akhir ketika diinversi

            # -cnt untuk descending
            # -epoch untuk descending (yang punya epoch positif akan lebih kecil dari -(-1) = 1.0)
            return (-cnt, -epoch, item.get("canonical_number", ""))

        return sorted(details_list, key=get_sort_key)

    def _analyze_calls(
        self,
        target: str,
        from_iso: Optional[str],
        to_iso: Optional[str],
        show_full: bool,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        CALL_INTERACTION:
        Target dan nomor terkait tercatat sebagai kedua pihak pada log yang sama.
        Hitung panggilan masuk/keluar atau tidak diketahui, serta waktu terakhir.
        Pisahkan panggilan terjawab dari percobaan jika sumber menyediakan status.
        """
        undated_count = 0
        with self.db.get_connection() as conn:
            # Cari panggilan di mana salah satu pihak adalah target dan pihak lainnya nomor valid
            query = """
            SELECT
                e.id, e.evidence_id, e.party_a, e.party_b, e.direction, e.status,
                e.duration, e.timestamp_utc, e.timestamp_orig, e.tz_known,
                ev.record_ref, m.file_name
            FROM events e
            JOIN evidence_records ev ON e.evidence_id = ev.id
            JOIN manifests m ON ev.manifest_id = m.id
            WHERE e.case_id = ?
              AND e.event_type = 'CALL'
              AND (
                  (e.party_a = ? AND e.party_b IS NOT NULL AND e.party_b != ?)
                  OR
                  (e.party_b = ? AND e.party_a IS NOT NULL AND e.party_a != ?)
              )
            """
            rows = conn.execute(query, (self.case_id, target, target, target, target)).fetchall()

        # Agregasi per nomor lawan bicara
        by_partner: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "incoming": 0,
            "outgoing": 0,
            "unknown_dir": 0,
            "answered": 0,
            "attempted": 0,
            "unknown_status": 0,
            "timestamps": [],
            "evidence_ids": set(),
            "evidence_refs": set(),
        })

        for r in rows:
            ts = r["timestamp_utc"]
            # Filter tanggal
            if from_iso or to_iso:
                if not ts:
                    undated_count += 1
                    continue
                if from_iso and ts < from_iso:
                    continue
                if to_iso and ts > to_iso:
                    continue

            # Tentukan siapa lawan bicaranya
            if r["party_a"] == target:
                partner = r["party_b"]
                # Jika target adalah party_a (pemanggil), arah panggilan dilihat dari perspektif target
                direction = r["direction"]
                if direction == "UNKNOWN":
                    direction = "OUTGOING"
            else:
                partner = r["party_a"]
                direction = r["direction"]
                if direction == "UNKNOWN":
                    direction = "INCOMING"

            p_data = by_partner[partner]
            p_data["evidence_ids"].add(r["evidence_id"])
            p_data["evidence_refs"].add(f"{r['file_name']}#{r['record_ref']}")

            if ts:
                p_data["timestamps"].append(ts)

            # Hitung arah
            if direction == "INCOMING":
                p_data["incoming"] += 1
            elif direction == "OUTGOING":
                p_data["outgoing"] += 1
            else:
                p_data["unknown_dir"] += 1

            # Hitung status panggilan
            st = (r["status"] or "UNKNOWN").upper()
            if st in ("ANSWERED", "TERJAWAB"):
                p_data["answered"] += 1
            elif st in ("ATTEMPTED", "MISSED", "TIDAK TERJAWAB", "COBA", "BUSY"):
                p_data["attempted"] += 1
            else:
                p_data["unknown_status"] += 1

        details_list: List[Dict[str, Any]] = []
        for partner, data in by_partner.items():
            total_events = data["incoming"] + data["outgoing"] + data["unknown_dir"]
            ts_sorted = sorted(data["timestamps"])
            first_act = ts_sorted[0] if ts_sorted else None
            last_act = ts_sorted[-1] if ts_sorted else None

            op_name, op_src = infer_operator(partner)

            item = {
                "category": AssociationCategory.CALL_INTERACTION.value,
                "canonical_number": partner,
                "masked_number": mask_phone(partner, show_full=show_full),
                "operator_name": op_name,
                "operator_source": op_src,
                "event_count": total_events,
                "first_activity_utc": first_act,
                "last_activity_utc": last_act,
                "breakdown": {
                    "incoming_calls": data["incoming"],
                    "outgoing_calls": data["outgoing"],
                    "unknown_direction_calls": data["unknown_dir"],
                    "answered_calls": data["answered"],
                    "attempted_or_missed_calls": data["attempted"],
                    "unknown_status_calls": data["unknown_status"],
                },
                "evidence_ids": sorted(list(data["evidence_ids"])),
                "evidence_refs": sorted(list(data["evidence_refs"])),
            }
            details_list.append(item)

        # Urutkan secara deterministik
        details_sorted = self._sort_category_details(details_list)

        return details_sorted, undated_count

    def _analyze_direct_messages(
        self,
        target: str,
        from_iso: Optional[str],
        to_iso: Optional[str],
        show_full: bool,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        DIRECT_MESSAGE:
        Target dan nomor terkait dapat diidentifikasi sebagai kedua pihak dalam percakapan pribadi.
        Pisahkan jumlah pesan dari jumlah thread.
        Nama tampilan tanpa nomor yang bisa dicocokkan tetap belum terpetakan.
        """
        undated_count = 0
        with self.db.get_connection() as conn:
            # 1. Pesan dengan party_a dan party_b eksplisit
            query_direct = """
            SELECT
                e.id, e.evidence_id, e.party_a, e.party_b, e.thread_id,
                e.timestamp_utc, ev.record_ref, m.file_name
            FROM events e
            JOIN evidence_records ev ON e.evidence_id = ev.id
            JOIN manifests m ON ev.manifest_id = m.id
            WHERE e.case_id = ?
              AND e.event_type = 'MESSAGE'
              AND (
                  (e.party_a = ? AND e.party_b IS NOT NULL AND e.party_b != ?)
                  OR
                  (e.party_b = ? AND e.party_a IS NOT NULL AND e.party_a != ?)
              )
            """
            rows_direct = conn.execute(query_direct, (self.case_id, target, target, target, target)).fetchall()

            # 2. Pesan dalam thread chat 1-on-1 (misal dari chat export di mana thread_id mengikat kedua pihak)
            query_threads = """
            SELECT
                e.id, e.evidence_id, e.party_a, e.thread_id,
                e.timestamp_utc, ev.record_ref, m.file_name
            FROM events e
            JOIN evidence_records ev ON e.evidence_id = ev.id
            JOIN manifests m ON ev.manifest_id = m.id
            WHERE e.case_id = ?
              AND e.event_type = 'MESSAGE'
              AND e.thread_id IS NOT NULL
              AND e.party_a IS NOT NULL
              AND e.party_b IS NULL
            """
            rows_thread = conn.execute(query_threads, (self.case_id,)).fetchall()

        by_partner: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "messages": 0,
            "threads": set(),
            "timestamps": [],
            "evidence_ids": set(),
            "evidence_refs": set(),
        })

        # Proses baris direct eksplisit
        for r in rows_direct:
            ts = r["timestamp_utc"]
            if from_iso or to_iso:
                if not ts:
                    undated_count += 1
                    continue
                if from_iso and ts < from_iso:
                    continue
                if to_iso and ts > to_iso:
                    continue

            partner = r["party_b"] if r["party_a"] == target else r["party_a"]
            p_data = by_partner[partner]
            p_data["messages"] += 1
            if r["thread_id"]:
                p_data["threads"].add(r["thread_id"])
            else:
                p_data["threads"].add(f"direct_{partner}")
            p_data["evidence_ids"].add(r["evidence_id"])
            p_data["evidence_refs"].add(f"{r['file_name']}#{r['record_ref']}")
            if ts:
                p_data["timestamps"].append(ts)

        # Proses baris thread 1-on-1 (kelompokkan per thread_id)
        thread_participants: Dict[str, Set[str]] = defaultdict(set)
        thread_rows: Dict[str, List[Any]] = defaultdict(list)
        for r in rows_thread:
            th_id = r["thread_id"]
            thread_participants[th_id].add(r["party_a"])
            thread_rows[th_id].append(r)

        for th_id, parts in thread_participants.items():
            # Percakapan pribadi harus memiliki tepat 2 pihak dan salah satunya adalah target
            if len(parts) == 2 and target in parts:
                partner = [p for p in parts if p != target][0]
                for r in thread_rows[th_id]:
                    ts = r["timestamp_utc"]
                    if from_iso or to_iso:
                        if not ts:
                            undated_count += 1
                            continue
                        if from_iso and ts < from_iso:
                            continue
                        if to_iso and ts > to_iso:
                            continue

                    p_data = by_partner[partner]
                    p_data["messages"] += 1
                    p_data["threads"].add(th_id)
                    p_data["evidence_ids"].add(r["evidence_id"])
                    p_data["evidence_refs"].add(f"{r['file_name']}#{r['record_ref']}")
                    if ts:
                        p_data["timestamps"].append(ts)

        details_list: List[Dict[str, Any]] = []
        for partner, data in by_partner.items():
            ts_sorted = sorted(data["timestamps"])
            first_act = ts_sorted[0] if ts_sorted else None
            last_act = ts_sorted[-1] if ts_sorted else None
            op_name, op_src = infer_operator(partner)

            item = {
                "category": AssociationCategory.DIRECT_MESSAGE.value,
                "canonical_number": partner,
                "masked_number": mask_phone(partner, show_full=show_full),
                "operator_name": op_name,
                "operator_source": op_src,
                "event_count": data["messages"],
                "first_activity_utc": first_act,
                "last_activity_utc": last_act,
                "breakdown": {
                    "message_count": data["messages"],
                    "thread_count": len(data["threads"]),
                    "thread_ids": sorted(list(data["threads"])),
                },
                "evidence_ids": sorted(list(data["evidence_ids"])),
                "evidence_refs": sorted(list(data["evidence_refs"])),
            }
            details_list.append(item)

        details_sorted = self._sort_category_details(details_list)

        return details_sorted, undated_count

    def _analyze_contacts(
        self,
        target: str,
        from_iso: Optional[str],
        to_iso: Optional[str],
        show_full: bool,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
        """
        SAVED_CONTACT:
        Nomor terkait tersimpan di buku kontak akun/perangkat yang hubungan dengan targetnya
        sudah dicatat dalam metadata bukti (contact_owner == target atau target_account_basis tercatat).
        Tampilkan label, sumber, dan dasar pengaitan akun/perangkat dengan target.

        SAME_ADDRESS_BOOK:
        Target dan nomor lain muncul di buku kontak yang sama, tetapi kepemilikan buku kontak
        oleh target belum terbukti. Tampilkan sebagai kemunculan bersama, terpisah dari interaksi langsung.
        """
        undated_count = 0
        with self.db.get_connection() as conn:
            query = """
            SELECT
                e.id, e.evidence_id, e.party_a, e.party_b, e.contact_name,
                e.contact_owner, e.target_account_basis, e.source_name,
                e.timestamp_utc, ev.record_ref, m.file_name
            FROM events e
            JOIN evidence_records ev ON e.evidence_id = ev.id
            JOIN manifests m ON ev.manifest_id = m.id
            WHERE e.case_id = ? AND e.event_type = 'CONTACT'
            """
            rows = conn.execute(query, (self.case_id,)).fetchall()

        saved_by_number: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "events": 0,
            "labels": set(),
            "sources": set(),
            "bases": set(),
            "timestamps": [],
            "evidence_ids": set(),
            "evidence_refs": set(),
        })

        # Kelompokkan kontak berdasarkan sumber/file untuk mendeteksi SAME_ADDRESS_BOOK
        contacts_by_source: Dict[str, List[Any]] = defaultdict(list)
        for r in rows:
            contacts_by_source[r["file_name"]].append(r)

        same_book_by_number: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "events": 0,
            "labels": set(),
            "sources": set(),
            "timestamps": [],
            "evidence_ids": set(),
            "evidence_refs": set(),
        })

        for fname, c_rows in contacts_by_source.items():
            # Cek apakah target ada di buku kontak ini (sebagai kontak atau pemilik)
            has_target = False
            for r in c_rows:
                if r["party_b"] == target or r["contact_owner"] == target:
                    has_target = True
                    break

            for r in c_rows:
                ts = r["timestamp_utc"]
                if from_iso or to_iso:
                    if not ts:
                        undated_count += 1
                        continue
                    if from_iso and ts < from_iso:
                        continue
                    if to_iso and ts > to_iso:
                        continue

                contact_phone = r["party_b"]
                if not contact_phone or contact_phone == target:
                    continue

                owner = r["contact_owner"]
                basis = r["target_account_basis"]

                # 1. Cek SAVED_CONTACT: kepemilikan oleh target dicatat atau owner == target
                if owner == target or (basis and "target" in basis.lower()):
                    s_data = saved_by_number[contact_phone]
                    s_data["events"] += 1
                    if r["contact_name"]:
                        s_data["labels"].add(r["contact_name"])
                    s_data["sources"].add(r["source_name"] or r["file_name"])
                    if basis:
                        s_data["bases"].add(basis)
                    elif owner == target:
                        s_data["bases"].add("Kepemilikan akun/perangkat terverifikasi nomor target")
                    s_data["evidence_ids"].add(r["evidence_id"])
                    s_data["evidence_refs"].add(f"{r['file_name']}#{r['record_ref']}")
                    if ts:
                        s_data["timestamps"].append(ts)
                # 2. Cek SAME_ADDRESS_BOOK: target muncul di buku kontak yang sama tapi kepemilikan belum terbukti
                elif has_target and owner != target:
                    sb_data = same_book_by_number[contact_phone]
                    sb_data["events"] += 1
                    if r["contact_name"]:
                        sb_data["labels"].add(r["contact_name"])
                    sb_data["sources"].add(r["source_name"] or r["file_name"])
                    sb_data["evidence_ids"].add(r["evidence_id"])
                    sb_data["evidence_refs"].add(f"{r['file_name']}#{r['record_ref']}")
                    if ts:
                        sb_data["timestamps"].append(ts)

        # Bangun daftar SAVED_CONTACT
        saved_list: List[Dict[str, Any]] = []
        for phone, data in saved_by_number.items():
            ts_sorted = sorted(data["timestamps"])
            first_act = ts_sorted[0] if ts_sorted else None
            last_act = ts_sorted[-1] if ts_sorted else None
            op_name, op_src = infer_operator(phone)

            item = {
                "category": AssociationCategory.SAVED_CONTACT.value,
                "canonical_number": phone,
                "masked_number": mask_phone(phone, show_full=show_full),
                "operator_name": op_name,
                "operator_source": op_src,
                "event_count": data["events"],
                "first_activity_utc": first_act,
                "last_activity_utc": last_act,
                "breakdown": {
                    "labels": sorted(list(data["labels"])),
                    "sources": sorted(list(data["sources"])),
                    "target_account_basis": "; ".join(sorted(list(data["bases"]))) if data["bases"] else "Catatan metadata bukti",
                    "forensic_note": "Label kontak tidak menyiratkan identitas atau jenis hubungan secara definitif.",
                },
                "evidence_ids": sorted(list(data["evidence_ids"])),
                "evidence_refs": sorted(list(data["evidence_refs"])),
            }
            saved_list.append(item)

        # Bangun daftar SAME_ADDRESS_BOOK
        same_book_list: List[Dict[str, Any]] = []
        for phone, data in same_book_by_number.items():
            ts_sorted = sorted(data["timestamps"])
            first_act = ts_sorted[0] if ts_sorted else None
            last_act = ts_sorted[-1] if ts_sorted else None
            op_name, op_src = infer_operator(phone)

            item = {
                "category": AssociationCategory.SAME_ADDRESS_BOOK.value,
                "canonical_number": phone,
                "masked_number": mask_phone(phone, show_full=show_full),
                "operator_name": op_name,
                "operator_source": op_src,
                "event_count": data["events"],
                "first_activity_utc": first_act,
                "last_activity_utc": last_act,
                "breakdown": {
                    "co_occurrence_type": "SAME_ADDRESS_BOOK",
                    "labels": sorted(list(data["labels"])),
                    "sources": sorted(list(data["sources"])),
                    "forensic_note": "Kemunculan bersama (co-occurrence); kepemilikan buku kontak oleh target belum terbukti. Jangan dijumlahkan ke skor interaksi langsung.",
                },
                "evidence_ids": sorted(list(data["evidence_ids"])),
                "evidence_refs": sorted(list(data["evidence_refs"])),
            }
            same_book_list.append(item)

        saved_sorted = self._sort_category_details(saved_list)
        same_book_sorted = self._sort_category_details(same_book_list)

        return saved_sorted, same_book_sorted, undated_count

    def _analyze_shared_groups(
        self,
        target: str,
        from_iso: Optional[str],
        to_iso: Optional[str],
        show_full: bool,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        SHARED_GROUP:
        Target dan nomor lain tercatat sebagai peserta di grup yang sama.
        Tampilkan sebagai konteks grup, tanpa menambah hitungan pesan langsung.
        """
        undated_count = 0
        with self.db.get_connection() as conn:
            # Ambil semua partisipasi grup
            query = """
            SELECT
                e.id, e.evidence_id, e.party_a, e.group_id, e.timestamp_utc,
                ev.record_ref, m.file_name
            FROM events e
            JOIN evidence_records ev ON e.evidence_id = ev.id
            JOIN manifests m ON ev.manifest_id = m.id
            WHERE e.case_id = ?
              AND (e.event_type = 'GROUP_PARTICIPATION' OR e.group_id IS NOT NULL)
              AND e.group_id != ''
            """
            rows = conn.execute(query, (self.case_id,)).fetchall()

        # Kelompokkan partisipan per group_id
        groups: Dict[str, Set[str]] = defaultdict(set)
        group_rows: Dict[str, List[Any]] = defaultdict(list)

        for r in rows:
            gid = r["group_id"]
            phone = r["party_a"]
            if phone:
                groups[gid].add(phone)
            group_rows[gid].append(r)

        # Cari grup di mana target berada
        target_groups = {gid for gid, participants in groups.items() if target in participants}

        by_partner: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "group_events": 0,
            "groups": set(),
            "timestamps": [],
            "evidence_ids": set(),
            "evidence_refs": set(),
        })

        for gid in target_groups:
            for r in group_rows[gid]:
                ts = r["timestamp_utc"]
                if from_iso or to_iso:
                    if not ts:
                        undated_count += 1
                        continue
                    if from_iso and ts < from_iso:
                        continue
                    if to_iso and ts > to_iso:
                        continue

                member = r["party_a"]
                if not member or member == target:
                    continue

                p_data = by_partner[member]
                p_data["group_events"] += 1
                p_data["groups"].add(gid)
                p_data["evidence_ids"].add(r["evidence_id"])
                p_data["evidence_refs"].add(f"{r['file_name']}#{r['record_ref']}")
                if ts:
                    p_data["timestamps"].append(ts)

        details_list: List[Dict[str, Any]] = []
        for partner, data in by_partner.items():
            ts_sorted = sorted(data["timestamps"])
            first_act = ts_sorted[0] if ts_sorted else None
            last_act = ts_sorted[-1] if ts_sorted else None
            op_name, op_src = infer_operator(partner)

            item = {
                "category": AssociationCategory.SHARED_GROUP.value,
                "canonical_number": partner,
                "masked_number": mask_phone(partner, show_full=show_full),
                "operator_name": op_name,
                "operator_source": op_src,
                "event_count": data["group_events"],
                "first_activity_utc": first_act,
                "last_activity_utc": last_act,
                "breakdown": {
                    "shared_groups_count": len(data["groups"]),
                    "group_ids": sorted(list(data["groups"])),
                    "forensic_note": "Konteks partisipasi grup bersama; tidak menambah hitungan pesan pribadi langsung.",
                },
                "evidence_ids": sorted(list(data["evidence_ids"])),
                "evidence_refs": sorted(list(data["evidence_refs"])),
            }
            details_list.append(item)

        details_sorted = self._sort_category_details(details_list)

        return details_sorted, undated_count

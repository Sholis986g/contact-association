"""
Benchmark dan pengujian performa contact-association pada 100.000 peristiwa sintetis.
Mengukur waktu impor, kueri lookup, dan kueri candidates secara terpisah.
"""
from __future__ import annotations
import os
import random
import shutil
import sys
import tempfile
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from core.db import Database
from core.importer import Importer
from core.engine import AssociationEngine
from core.candidates import find_candidates

# ── Konfigurasi Benchmark ─────────────────────────────────────────────────────
TARGET_NUMBER = "081200000012"
CASE_ID = "CASE-BENCHMARK"
OPERATOR_ID = "OP-BENCH"
N_EVENTS = 100_000

# Pool nomor telepon Indonesia yang valid
def _rand_phone(prefix: str, min_len: int = 10, max_len: int = 12) -> str:
    suffix_len = random.randint(min_len, max_len) - len(prefix)
    suffix = "".join([str(random.randint(0, 9)) for _ in range(suffix_len)])
    return prefix + suffix

PREFIXES = [
    "0812", "0813", "0821", "0852", "0853",   # Telkomsel
    "0815", "0856", "0857",                    # Indosat
    "0896", "0897", "0898",                    # Tri
    "0817", "0831", "0877",                    # XL
    "0881", "0882", "0888",                    # Smartfren
]

POOL_SIZE = 500   # 500 nomor partner unik untuk variasi realistis
PARTNER_POOL = [_rand_phone(random.choice(PREFIXES)) for _ in range(POOL_SIZE)]

STATUSES = ["ANSWERED", "MISSED", "ATTEMPTED"]
DIRECTIONS = ["OUTGOING", "INCOMING"]
EVENT_TYPES = ["CALL", "CALL", "CALL", "MESSAGE"]   # Lebih banyak call

TIMESTAMPS = [
    "2024-01-{:02d} {:02d}:{:02d}:{:02d}".format(
        random.randint(1, 28),
        random.randint(0, 23),
        random.randint(0, 59),
        random.randint(0, 59),
    )
    for _ in range(N_EVENTS)
]


def generate_benchmark_csv(path: str, n: int) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("caller_msisdn,callee_msisdn,call_datetime,duration_sec,call_direction,call_status,cdr_record_id\n")
        for i in range(n):
            # 70% melibatkan target sebagai salah satu pihak
            if random.random() < 0.70:
                if random.random() < 0.5:
                    party_a = TARGET_NUMBER
                    party_b = random.choice(PARTNER_POOL)
                else:
                    party_a = random.choice(PARTNER_POOL)
                    party_b = TARGET_NUMBER
            else:
                party_a = random.choice(PARTNER_POOL)
                party_b = random.choice(PARTNER_POOL)
                while party_b == party_a:
                    party_b = random.choice(PARTNER_POOL)

            ts = TIMESTAMPS[i % len(TIMESTAMPS)]
            dur = random.randint(0, 600)
            direction = random.choice(DIRECTIONS)
            status = random.choice(STATUSES)
            ev_id = f"BM-{i:07d}"
            f.write(f"{party_a},{party_b},{ts},{dur},{direction},{status},{ev_id}\n")


def run_benchmark() -> dict:
    temp_dir = tempfile.mkdtemp(prefix="ca_bench_")
    db_path = os.path.join(temp_dir, "bench.sqlite")
    csv_path = os.path.join(temp_dir, "bench_events.csv")
    profile_path = os.path.join(BASE_DIR, "profiles", "cdr_profile.json")

    results = {
        "n_events": N_EVENTS,
        "partner_pool_size": POOL_SIZE,
        "target": TARGET_NUMBER,
        "timings": {},
        "counts": {},
        "unverified_formats": [],
    }

    try:
        print(f"[*] Menyiapkan {N_EVENTS:,} peristiwa sintetis...")
        t0 = time.perf_counter()
        generate_benchmark_csv(csv_path, N_EVENTS)
        t_gen = time.perf_counter() - t0
        results["timings"]["csv_generation_sec"] = round(t_gen, 3)
        print(f"    CSV dibuat dalam {t_gen:.2f}s ({os.path.getsize(csv_path) / 1_048_576:.1f} MB)")

        db = Database(db_path)
        importer = Importer(db, CASE_ID, OPERATOR_ID)

        # ── 1. Benchmark Impor ─────────────────────────────────────────────
        print(f"\n[*] Menjalankan impor {N_EVENTS:,} peristiwa...")
        t1 = time.perf_counter()
        report = importer.import_path(csv_path, profile_path=profile_path)
        t_import = time.perf_counter() - t1

        results["timings"]["import_sec"] = round(t_import, 3)
        results["counts"]["events_accepted"] = report.total_accepted
        results["counts"]["events_rejected"] = report.total_rejected
        results["timings"]["import_rate_per_sec"] = round(report.total_accepted / t_import, 0) if t_import > 0 else 0
        print(f"    Impor selesai: {report.total_accepted:,} diterima, {report.total_rejected:,} ditolak")
        print(f"    Waktu impor  : {t_import:.3f}s ({results['timings']['import_rate_per_sec']:,.0f} peristiwa/detik)")

        engine = AssociationEngine(db, CASE_ID, OPERATOR_ID)

        # ── 2. Benchmark Lookup tanpa filter tanggal ───────────────────────
        print(f"\n[*] Menjalankan lookup (tanpa filter tanggal)...")
        t2 = time.perf_counter()
        res_all = engine.lookup(TARGET_NUMBER, show_full=False)
        t_lookup_all = time.perf_counter() - t2

        results["timings"]["lookup_no_filter_sec"] = round(t_lookup_all, 3)
        results["counts"]["unique_associated_no_filter"] = res_all["summary"]["total_unique_associated_numbers"]
        print(f"    Lookup selesai: {results['counts']['unique_associated_no_filter']:,} nomor unik terkait")
        print(f"    Waktu lookup  : {t_lookup_all:.3f}s")

        # ── 3. Benchmark Lookup dengan filter tanggal ─────────────────────
        print(f"\n[*] Menjalankan lookup (filter: 2024-01-01 s/d 2024-01-15)...")
        t3 = time.perf_counter()
        res_filt = engine.lookup(TARGET_NUMBER, from_date_str="2024-01-01", to_date_str="2024-01-15")
        t_lookup_filt = time.perf_counter() - t3

        results["timings"]["lookup_with_filter_sec"] = round(t_lookup_filt, 3)
        results["counts"]["unique_associated_filtered"] = res_filt["summary"]["total_unique_associated_numbers"]
        print(f"    Lookup (filter) selesai: {results['counts']['unique_associated_filtered']:,} nomor unik terkait")
        print(f"    Waktu lookup (filter)  : {t_lookup_filt:.3f}s")

        # ── 4. Benchmark Candidates suffix ────────────────────────────────
        print(f"\n[*] Menjalankan pencarian candidates (suffix='0012')...")
        t4 = time.perf_counter()
        cand_res = find_candidates(db, CASE_ID, OPERATOR_ID, "0012")
        t_cand = time.perf_counter() - t4

        results["timings"]["candidates_sec"] = round(t_cand, 3)
        results["counts"]["candidates_found"] = cand_res["total_candidates"]
        print(f"    Candidates selesai: {cand_res['total_candidates']} kandidat ditemukan")
        print(f"    Waktu candidates  : {t_cand:.3f}s")

        # ── 5. Format yang belum berhasil diverifikasi ─────────────────────
        # XLSX: diuji di test suite dengan XLSX buatan sendiri (pure Python zip+xml, tanpa openpyxl)
        # Telegram: diuji di test suite dengan format ekspor desktop
        # CSV: TERVERIFIKASI (benchmark ini)
        # VCF: TERVERIFIKASI (test_parsers.py)
        # WhatsApp TXT: TERVERIFIKASI (test_parsers.py)
        results["unverified_formats"] = [
            "XLSX (openpyxl tidak terinstal; parser menggunakan zipfile+xml Python stdlib — fungsional tapi belum diuji pada semua varian XLSX kompleks)",
        ]

        return results

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def print_results(r: dict) -> None:
    print("\n" + "=" * 68)
    print(" HASIL BENCHMARK contact-association")
    print("=" * 68)
    print(f"  Jumlah peristiwa sintetis  : {r['n_events']:,}")
    print(f"  Ukuran pool partner        : {r['partner_pool_size']}")
    print(f"  Target nomor               : {r['target']}")
    print()
    print("  WAKTU (detik):")
    t = r["timings"]
    print(f"    Pembuatan CSV            : {t.get('csv_generation_sec', '-')}")
    print(f"    Impor ke SQLite          : {t.get('import_sec', '-')}s  ({t.get('import_rate_per_sec', 0):,.0f} ev/s)")
    print(f"    Lookup (tanpa filter)    : {t.get('lookup_no_filter_sec', '-')}s")
    print(f"    Lookup (dengan filter)   : {t.get('lookup_with_filter_sec', '-')}s")
    print(f"    Candidates suffix search : {t.get('candidates_sec', '-')}s")
    print()
    print("  HASIL KUERI:")
    c = r["counts"]
    print(f"    Peristiwa diterima       : {c.get('events_accepted', 0):,}")
    print(f"    Peristiwa ditolak        : {c.get('events_rejected', 0):,}")
    print(f"    Nomor unik terkait (all) : {c.get('unique_associated_no_filter', 0):,}")
    print(f"    Nomor unik terkait (filt): {c.get('unique_associated_filtered', 0):,}")
    print(f"    Kandidat ditemukan       : {c.get('candidates_found', 0)}")
    print()
    print("  FORMAT BELUM TERVERIFIKASI SEPENUHNYA:")
    for f in r.get("unverified_formats", []):
        print(f"    ! {f}")
    print("=" * 68 + "\n")


if __name__ == "__main__":
    random.seed(42)
    res = run_benchmark()
    print_results(res)

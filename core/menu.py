"""Menu terminal untuk perintah yang sama dengan CLI."""
from pathlib import Path
from tempfile import TemporaryDirectory
from shutil import get_terminal_size

from .terminal import accent, clean, heading


ACTIONS = {
    "1": ("import", "Impor bukti", "Tambahkan file atau folder bukti lokal ke database kasus."),
    "2": ("candidates", "Cari kandidat", "Cari nomor dalam kasus dari 4-8 digit terakhir."),
    "3": ("lookup", "Analisis nomor", "Lihat interaksi, periode, dan rujukan bukti untuk satu nomor."),
    "4": ("audit", "Riwayat audit", "Tinjau aktivitas yang tercatat pada kasus."),
}


def ask(label, default="", required=False):
    while True:
        hint = f" [{clean(default)}]" if default else ""
        value = input(f"  {label}{hint} > ").strip().strip('"')
        if value == ":menu":
            raise InterruptedError
        if value or default or not required:
            return value or default
        print("  Kolom ini perlu diisi.")


def dashboard():
    rule = "=" * max(12, min(70, get_terminal_size().columns - 2))
    print("\n" + accent(rule, "36"))
    print(accent("  CONTACT ASSOCIATION", "1;36") + "   /   FORENSIK DIGITAL")
    print(accent(rule, "36"))
    print("  Telusuri hubungan antar nomor dari kontak, log panggilan,")
    print("  dan pesan yang sudah diimpor ke kasus. Analisis berjalan offline.\n")
    for key, (_, title, purpose) in ACTIONS.items():
        print(f"  {accent('[' + key + ']', '1;33')}  {title}\n       {purpose}")
    print(f"  {accent('[5]', '1;33')}  Demo terpandu dengan data contoh")
    print("  [0]  Keluar\n")


def demo(run):
    root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory() as tmp:
        common = ["--db", str(Path(tmp) / "demo.sqlite"), "--case-id", "DEMO", "--operator-id", "DEMO"]
        print("  Demo memakai CDR contoh dan database sementara.")
        if run(["import", *common, "--input", str(root / "fixtures/sample_cdr.csv"),
                "--profile", str(root / "profiles/cdr_profile.json")]) == 0:
            run(["lookup", *common, "--target", "081200000012"])


def run_menu(run):
    db, case, operator = "latihan.sqlite", "DEMO", "OP01"
    while True:
        try:
            dashboard()
            choice = input("  Pilih tindakan [0-5] > ").strip()
            if choice == "0":
                return
            if choice == "5":
                demo(run)
            elif choice in ACTIONS:
                command, title, purpose = ACTIONS[choice]
                heading(title.upper())
                print("  " + purpose)
                print("  Enter memakai nilai dalam kurung. Ketik :menu untuk kembali.")
                db = ask("Database SQLite", db)
                if command != "import" and not Path(db).is_file():
                    print("  Database belum ditemukan. Pilih Impor bukti atau masukkan jalur yang benar.")
                    continue
                case = ask("ID kasus", case)
                argv = [command, "--db", db, "--case-id", case]
                if command != "audit":
                    operator = ask("ID operator", operator)
                    argv.extend(["--operator-id", operator])
                if command == "import":
                    argv.extend(["--input", ask("File atau folder bukti", required=True)])
                    profile = ask("Profil pemetaan JSON (opsional)")
                    if profile:
                        argv.extend(["--profile", profile])
                elif command == "candidates":
                    argv.extend(["--suffix", ask("Akhiran nomor (4-8 digit)", required=True)])
                elif command == "lookup":
                    argv.extend(["--target", ask("Nomor lengkap", required=True)])
                    for flag, label in [("--from", "Tanggal awal"), ("--to", "Tanggal akhir"),
                                        ("--export", "File ekspor JSON/CSV")]:
                        value = ask(label + " (opsional)")
                        if value:
                            argv.extend([flag, value])
                run(argv)
            else:
                print("  Masukkan angka 0 sampai 5.")
                continue
            input("\n  Tekan Enter untuk kembali ke menu...")
        except (InterruptedError, KeyboardInterrupt):
            print("\n  Kembali ke menu.")
        except EOFError:
            return

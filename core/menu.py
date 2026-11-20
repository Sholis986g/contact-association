"""Menu terminal untuk perintah yang sama dengan CLI."""
from pathlib import Path
from tempfile import TemporaryDirectory
from shutil import get_terminal_size
import sys
from textwrap import wrap

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


def panel(title, body, width, rounded):
    glyphs = "╭╮╰╯─│" if rounded else "┏┓┗┛━┃"
    try:
        glyphs.encode(sys.stdout.encoding or "ascii")
    except UnicodeEncodeError:
        glyphs = "++++-|"
    tl, tr, bl, br, h, v = glyphs
    inner = width - 4
    lines = [tl + h * (width - 2) + tr]
    for paragraph in (title, *body):
        for line in wrap(paragraph, inner) or [""]:
            lines.append(f"{v} {line:<{inner}} {v}")
    return lines + [bl + h * (width - 2) + br]


def dashboard(theme, case, db):
    board = theme == "board"
    width = max(16, min(92, get_terminal_size().columns - 2))
    color = "1;36" if board else "1;33"
    name = "Papan bukti" if board else "Berkas kasus"
    intro = panel("CONTACT ASSOCIATION  /  " + name.upper(), [
        "Telusuri interaksi nomor dari artefak lokal.",
        f"Kasus: {clean(case)}   |   Database: {clean(Path(db).name)}",
    ], width, board)
    print("\n" + "\n".join(accent(line, color) for line in intro))
    items = [(key, title, desc) for key, (_, title, desc) in ACTIONS.items()]
    if board:
        # Dua kartu sejajar bila jendela cukup lebar.
        columns = 2 if width >= 66 else 1
        card_width = (width - 2 * (columns - 1)) // columns
        for start in range(0, len(items), columns):
            cards = [panel(f"[{key}] {title.upper()}", [desc], card_width, True)
                     for key, title, desc in items[start:start + columns]]
            for row in range(max(map(len, cards))):
                print("  ".join(card[row] if row < len(card) else " " * card_width
                                for card in cards))
    else:
        entries = []
        for key, title, desc in items:
            entries.extend([f"[{key}]  {title}", "     " + desc])
        print("\n".join(panel("INDEKS TINDAKAN", entries, width, False)))
    print(accent("[5] Demo   [T] Ganti tema   [0] Keluar", color))


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
    theme = "board"
    while True:
        try:
            dashboard(theme, case, db)
            choice = input("  Pilih [0-5 / T] > ").strip()
            if choice.lower() == "t":
                theme = "dossier" if theme == "board" else "board"
                continue
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
                print("  Masukkan angka 0 sampai 5 atau T.")
                continue
            input("\n  Tekan Enter untuk kembali ke menu...")
        except (InterruptedError, KeyboardInterrupt):
            print("\n  Kembali ke menu.")
        except EOFError:
            return

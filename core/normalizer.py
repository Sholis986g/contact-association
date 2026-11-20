"""
Modul normalisasi dan validasi nomor telepon seluler Indonesia,
pencarian suffix, penyamaran (masking), dan inferensi provider seluler.
Kompatibel dengan spesifikasi dan aturan dari id-phone-match.
"""
from __future__ import annotations
import re
from typing import Optional, Tuple


class ValidationError(Exception):
    """Exception untuk kesalahan validasi nomor telepon atau format input."""
    pass


# Pemetaan prefix resmi provider seluler Indonesia berdasarkan data/providers.json id-phone-match
# Diurutkan berdasarkan prefix terpanjang lebih dahulu (longest-prefix match)
PROVIDER_PREFIXES: list[tuple[str, str]] = [
    # 5-digit prefixes
    ("08515", "Telkomsel"),
    ("08516", "Telkomsel"),
    ("08517", "Telkomsel"),
    ("08518", "Telkomsel"),
    ("08588", "Indosat Ooredoo"),
    # 4-digit prefixes
    # Telkomsel
    ("0811", "Telkomsel"),
    ("0812", "Telkomsel"),
    ("0813", "Telkomsel"),
    ("0821", "Telkomsel"),
    ("0822", "Telkomsel"),
    ("0823", "Telkomsel"),
    ("0851", "Telkomsel"),
    ("0852", "Telkomsel"),
    ("0853", "Telkomsel"),
    # Indosat Ooredoo
    ("0814", "Indosat Ooredoo"),
    ("0815", "Indosat Ooredoo"),
    ("0816", "Indosat Ooredoo"),
    ("0855", "Indosat Ooredoo"),
    ("0856", "Indosat Ooredoo"),
    ("0857", "Indosat Ooredoo"),
    ("0858", "Indosat Ooredoo"),
    # Tri (Hutchison 3 / IOH)
    ("0895", "Tri"),
    ("0896", "Tri"),
    ("0897", "Tri"),
    ("0898", "Tri"),
    ("0899", "Tri"),
    # XL Axiata
    ("0817", "XL Axiata"),
    ("0818", "XL Axiata"),
    ("0819", "XL Axiata"),
    ("0831", "XL Axiata"),
    ("0832", "XL Axiata"),
    ("0833", "XL Axiata"),
    ("0838", "XL Axiata"),
    ("0859", "XL Axiata"),
    ("0877", "XL Axiata"),
    ("0878", "XL Axiata"),
    ("0879", "XL Axiata"),
    # Smartfren
    ("0881", "Smartfren"),
    ("0882", "Smartfren"),
    ("0883", "Smartfren"),
    ("0884", "Smartfren"),
    ("0885", "Smartfren"),
    ("0886", "Smartfren"),
    ("0887", "Smartfren"),
    ("0888", "Smartfren"),
    ("0889", "Smartfren"),
]


def normalize_and_validate_phone(raw_phone: str | None) -> str:
    """
    Menormalisasi nomor telepon ke format kanonik '08xxxxxxxxxx' dan memvalidasinya.
    Menggunakan kembali aturan validasi nomor dari id-phone-match.

    Aturan:
    - Format input yang diterima: 08xx, +628xx, 628xx.
    - Pemisah yang diizinkan: spasi (' '), tanda hubung ('-'), titik ('.'), tanda kurung ('(', ')').
    - Karakter lain yang tidak sah akan ditolak dengan alasan jelas.
    - Mengonversi awalan '+62' atau '62' menjadi '0'.
    - Hasil kanonik harus diawali dengan '08'.
    - Panjang harus 10–13 digit setelah normalisasi.

    Returns:
        str: Nomor telepon kanonik '08xxxxxxxxxx'

    Raises:
        ValidationError: Jika karakter tidak sah, awalan bukan 08, atau panjang di luar 10-13 digit.
    """
    if raw_phone is None:
        raise ValidationError("Nomor telepon tidak boleh kosong (None).")

    raw_str = str(raw_phone).strip()
    if not raw_str:
        raise ValidationError("Nomor telepon tidak boleh kosong.")

    # Cek karakter tidak diizinkan
    allowed_chars_pattern = r'^\+?[\d\s\-\.\(\)]+$'
    if not re.match(allowed_chars_pattern, raw_str):
        if '+' in raw_str and not raw_str.startswith('+'):
            raise ValidationError(
                f"Karakter '+' hanya diizinkan di awal nomor: '{raw_str}'."
            )
        invalid_chars = [ch for ch in raw_str if not (ch.isdigit() or ch in (' ', '-', '.', '(', ')', '+'))]
        raise ValidationError(
            f"Karakter tidak diizinkan pada input nomor '{raw_str}': "
            f"{', '.join(repr(c) for c in sorted(set(invalid_chars)))}."
        )

    # Buang pemisah spasi, strip, titik, kurung
    cleaned = re.sub(r'[\s\-\.\(\)]+', '', raw_str)

    # Normalisasi awalan internasional (+62 / 62) ke awalan lokal (0)
    if cleaned.startswith('+62'):
        canonical = '0' + cleaned[3:]
    elif cleaned.startswith('62'):
        canonical = '0' + cleaned[2:]
    else:
        canonical = cleaned

    # Validasi awalan 08
    if not canonical.startswith('08'):
        raise ValidationError(
            f"Nomor tidak valid: '{raw_str}' (kanonik: '{canonical}'). "
            "Nomor seluler Indonesia harus diawali dengan '08' setelah normalisasi "
            "(format yang diterima: 08xx, +628xx, 628xx)."
        )

    # Validasi seluruh karakter tersisa adalah digit angka
    if not canonical.isdigit():
        raise ValidationError(f"Nomor kanonik mengandung karakter non-digit: '{canonical}'.")

    # Validasi panjang nomor: 10 - 13 digit
    length = len(canonical)
    if length < 10 or length > 13:
        raise ValidationError(
            f"Panjang nomor tidak valid: {length} digit ('{canonical}'). "
            "Panjang nomor seluler Indonesia harus 10–13 digit setelah normalisasi ke format 08xx."
        )

    return canonical


def try_normalize_phone(raw_phone: str | None) -> Tuple[Optional[str], Optional[str]]:
    """
    Mencoba menormalisasi nomor telepon tanpa melempar exception fatal.
    Returns:
        (canonical_phone, None) jika valid.
        (None, error_reason) jika tidak valid.
    """
    try:
        canonical = normalize_and_validate_phone(raw_phone)
        return canonical, None
    except ValidationError as e:
        return None, str(e)


def validate_suffix(raw_suffix: str | None) -> str:
    """
    Validasi parameter --suffix (4-8 digit).
    Mempertahankan angka nol di awal suffix (misal: '0012' tetap '0012').
    """
    if raw_suffix is None:
        raise ValidationError("Parameter --suffix tidak boleh kosong.")

    suffix_str = str(raw_suffix).strip()
    if not suffix_str:
        raise ValidationError("Parameter --suffix tidak boleh kosong.")

    if not suffix_str.isdigit():
        raise ValidationError(f"Suffix harus hanya terdiri dari digit angka: '{raw_suffix}'.")

    if len(suffix_str) < 4 or len(suffix_str) > 8:
        raise ValidationError(
            f"Panjang suffix harus 4–8 digit (diberikan: {len(suffix_str)} digit: '{suffix_str}')."
        )

    return suffix_str


def mask_phone(phone: str | None, show_full: bool = False) -> str:
    """
    Menyamarkan nomor telepon untuk keamanan privasi dan audit forensik.
    Contoh: '081234567890' -> '0812****7890'.
    Jika show_full=True, nomor ditampilkan lengkap.
    """
    if not phone:
        return "-"
    if show_full:
        return str(phone)

    phone_str = str(phone).strip()
    length = len(phone_str)

    if length >= 8:
        return f"{phone_str[:4]}****{phone_str[-4:]}"
    elif length >= 4:
        return f"{phone_str[:2]}**{phone_str[-2:]}"
    else:
        return "****"


def infer_operator(canonical_phone: str | None) -> Tuple[str, str]:
    """
    Melakukan inferensi operator seluler berdasarkan prefix nomor kanonik.
    Sesuai instruksi:
    Jika operator seluler ditampilkan dari prefix, beri label 'inferensi prefix'.
    Gunakan 'Tidak diketahui' jika tidak dapat dipastikan.

    Returns:
        Tuple[operator_name, operator_source]
    """
    if not canonical_phone:
        return "Tidak diketahui", "Tidak diketahui"

    for prefix, provider in PROVIDER_PREFIXES:
        if canonical_phone.startswith(prefix):
            return provider, "inferensi prefix"

    return "Tidak diketahui", "Tidak diketahui"

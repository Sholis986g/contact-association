"""
Pengujian normalisasi nomor telepon Indonesia, suffix, masking, dan inferensi provider.
"""
import unittest
from core.normalizer import (
    ValidationError,
    infer_operator,
    mask_phone,
    normalize_and_validate_phone,
    try_normalize_phone,
    validate_suffix,
)


class TestNormalizer(unittest.TestCase):
    def test_three_formats_normalization(self):
        # 1. Format 08xx
        self.assertEqual(normalize_and_validate_phone("08123456789"), "08123456789")
        self.assertEqual(normalize_and_validate_phone("0812-3456-7890"), "081234567890")
        self.assertEqual(normalize_and_validate_phone("(0812) 345 6789"), "08123456789")

        # 2. Format +628xx
        self.assertEqual(normalize_and_validate_phone("+628123456789"), "08123456789")
        self.assertEqual(normalize_and_validate_phone("+62 812-3456-7890"), "081234567890")
        self.assertEqual(normalize_and_validate_phone("+62.812.3456.7890"), "081234567890")

        # 3. Format 628xx
        self.assertEqual(normalize_and_validate_phone("628123456789"), "08123456789")
        self.assertEqual(normalize_and_validate_phone("6281234567890"), "081234567890")
        self.assertEqual(normalize_and_validate_phone("62-812-3456-789"), "08123456789")

    def test_invalid_and_ambiguous_numbers(self):
        # Karakter tidak sah
        with self.assertRaises(ValidationError):
            normalize_and_validate_phone("0812ABCD1234")

        with self.assertRaises(ValidationError):
            normalize_and_validate_phone("0812#345*678")

        with self.assertRaises(ValidationError):
            normalize_and_validate_phone("+62812+34567")

        # Awalan bukan seluler Indonesia (bukan 08)
        with self.assertRaises(ValidationError):
            normalize_and_validate_phone("0215551234")  # PSTN Jakarta

        with self.assertRaises(ValidationError):
            normalize_and_validate_phone("+12025550199")  # US number

        # Panjang di luar 10-13 digit
        with self.assertRaises(ValidationError):
            normalize_and_validate_phone("08123456")  # 8 digit

        with self.assertRaises(ValidationError):
            normalize_and_validate_phone("0812345678901234")  # 16 digit

        # Kosong
        with self.assertRaises(ValidationError):
            normalize_and_validate_phone("")
        with self.assertRaises(ValidationError):
            normalize_and_validate_phone(None)

    def test_suffix_validation_and_leading_zeros(self):
        # Mempertahankan leading zero
        self.assertEqual(validate_suffix("0012"), "0012")
        self.assertEqual(validate_suffix("000123"), "000123")
        self.assertEqual(validate_suffix("7890"), "7890")
        self.assertEqual(validate_suffix("12345678"), "12345678")

        # Kurang dari 4 digit
        with self.assertRaises(ValidationError):
            validate_suffix("123")

        # Lebih dari 8 digit
        with self.assertRaises(ValidationError):
            validate_suffix("123456789")

        # Karakter non-digit
        with self.assertRaises(ValidationError):
            validate_suffix("001a")

    def test_masking_phone(self):
        # Default masking (show_full=False)
        self.assertEqual(mask_phone("081234567890", show_full=False), "0812****7890")
        self.assertEqual(mask_phone("08123456789", show_full=False), "0812****6789")

        # Explicit show_full=True
        self.assertEqual(mask_phone("081234567890", show_full=True), "081234567890")
        self.assertEqual(mask_phone(None, show_full=False), "-")

    def test_operator_inference(self):
        # Telkomsel
        op, src = infer_operator("081200000012")
        self.assertEqual(op, "Telkomsel")
        self.assertEqual(src, "inferensi prefix")

        # Indosat Ooredoo
        op, src = infer_operator("081500000012")
        self.assertEqual(op, "Indosat Ooredoo")
        self.assertEqual(src, "inferensi prefix")

        # Tri
        op, src = infer_operator("089600000012")
        self.assertEqual(op, "Tri")
        self.assertEqual(src, "inferensi prefix")

        # XL Axiata
        op, src = infer_operator("081800000012")
        self.assertEqual(op, "XL Axiata")
        self.assertEqual(src, "inferensi prefix")

        # Smartfren
        op, src = infer_operator("088200000012")
        self.assertEqual(op, "Smartfren")
        self.assertEqual(src, "inferensi prefix")

        # Prefix tidak terdaftar
        op, src = infer_operator("080000000000")
        self.assertEqual(op, "Tidak diketahui")
        self.assertEqual(src, "Tidak diketahui")


if __name__ == "__main__":
    unittest.main()

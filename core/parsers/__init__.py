"""
Registry dan loader parser forensik untuk format CSV, JSON, XLSX, VCF, dan WhatsApp Text.
"""
from __future__ import annotations
import os
from typing import Dict, Optional, Type
from .base import BaseParser, ParseResult
from .csv_parser import CSVParser
from .json_parser import JSONParser
from .xlsx_parser import XLSXParser
from .vcf_parser import VCFParser
from .whatsapp_parser import WhatsAppParser


PARSER_MAP: Dict[str, Type[BaseParser]] = {
    ".csv": CSVParser,
    ".json": JSONParser,
    ".xlsx": XLSXParser,
    ".vcf": VCFParser,
    ".txt": WhatsAppParser,
}

SUPPORTED_EXTENSIONS = set(PARSER_MAP.keys())


def get_parser_for_file(file_path: str, profile_parser_name: Optional[str] = None) -> Optional[BaseParser]:
    """Mendapatkan instance parser yang sesuai untuk berkas bukti."""
    if profile_parser_name:
        for p_cls in (CSVParser, JSONParser, XLSXParser, VCFParser, WhatsAppParser):
            if p_cls.parser_name.lower() == profile_parser_name.lower():
                return p_cls()

    ext = os.path.splitext(file_path)[1].lower()
    parser_cls = PARSER_MAP.get(ext)
    if parser_cls:
        return parser_cls()
    return None

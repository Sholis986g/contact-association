"""
Antarmuka dasar parser artefak bukti forensik.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List
from ..models import NormalizedEvent


@dataclass
class ParseResult:
    events: List[NormalizedEvent] = field(default_factory=list)
    rejected_records: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    total_rows: int = 0


class BaseParser(ABC):
    parser_name: str = "BaseParser"
    parser_version: str = "1.0.0"

    @abstractmethod
    def parse(
        self,
        file_path: str,
        profile: Dict[str, Any] | None = None,
        options: Dict[str, Any] | None = None,
    ) -> ParseResult:
        """Membaca berkas bukti dalam mode read-only dan menghasilkan ParseResult."""
        pass

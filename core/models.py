"""
Model data, struktur data forensik, dan representasi entitas untuk contact-association.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


DISCLAIMER_TEXT = (
    "Hasil berasal dari artefak yang diimpor dalam lingkup kasus ini. "
    "Kecocokan parsial dan kemunculan bersama memerlukan pemeriksaan lanjutan. "
    "Gunakan hasil sesuai otorisasi kasus dan prosedur penanganan bukti yang berlaku."
)


class AssociationCategory(str, Enum):
    CALL_INTERACTION = "CALL_INTERACTION"
    DIRECT_MESSAGE = "DIRECT_MESSAGE"
    SAVED_CONTACT = "SAVED_CONTACT"
    SAME_ADDRESS_BOOK = "SAME_ADDRESS_BOOK"
    SHARED_GROUP = "SHARED_GROUP"


@dataclass
class ManifestRecord:
    case_id: str
    operator_id: str
    file_path: str
    file_name: str
    sha256: str
    file_size: int
    source_device: Optional[str]
    parser_name: str
    parser_version: str
    import_time_utc: str
    status: str = "SUCCESS"  # SUCCESS, PARTIAL, FAILED
    records_accepted: int = 0
    records_rejected: int = 0
    manifest_metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None


@dataclass
class NormalizedEvent:
    event_type: str  # CALL, MESSAGE, CONTACT, GROUP_PARTICIPATION
    source_name: str
    record_ref: str
    event_id: Optional[str] = None
    timestamp_orig: Optional[str] = None
    timestamp_utc: Optional[str] = None
    tz_known: bool = False
    party_a: Optional[str] = None
    party_a_raw: Optional[str] = None
    party_b: Optional[str] = None
    party_b_raw: Optional[str] = None
    direction: str = "UNKNOWN"  # INCOMING, OUTGOING, UNKNOWN
    status: str = "UNKNOWN"     # ANSWERED, ATTEMPTED, MISSED, SENT, DELIVERED, etc.
    duration: Optional[int] = None
    thread_id: Optional[str] = None
    group_id: Optional[str] = None
    contact_name: Optional[str] = None
    contact_owner: Optional[str] = None
    target_account_basis: Optional[str] = None
    flagged_for_review: bool = False
    review_reason: Optional[str] = None


@dataclass
class AssociationDetail:
    category: AssociationCategory
    canonical_number: str
    masked_number: str
    operator_name: str
    operator_source: str
    event_count: int
    first_activity_utc: Optional[str] = None
    last_activity_utc: Optional[str] = None
    breakdown: Dict[str, Any] = field(default_factory=dict)
    evidence_ids: List[int] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)


@dataclass
class CaseSummary:
    case_id: str
    target: str
    masked_target: str
    date_filter: Dict[str, Optional[str]]
    sources_analyzed: List[str]
    total_unique_associated_numbers: int
    category_counts: Dict[str, int]
    undated_records_count: int
    disclaimer: str = DISCLAIMER_TEXT


@dataclass
class AuditEntry:
    timestamp_utc: str
    operator_id: str
    case_id: str
    action_type: str  # IMPORT, CANDIDATES, LOOKUP, EXPORT
    masked_target: Optional[str] = None
    filters_applied: Dict[str, Any] = field(default_factory=dict)
    result_count: int = 0
    execution_status: str = "SUCCESS"  # SUCCESS, FAILED, ERROR
    error_message: Optional[str] = None
    id: Optional[int] = None

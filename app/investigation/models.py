from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from app.schemas import PageCategory, FormMetadata, FormFieldMetadata


class DiscoveredLink(BaseModel):
    __test__ = False
    raw_url: str
    normalized_url: str
    source_url: str
    link_text: Optional[str] = None
    depth: int = 0
    is_same_origin: bool = True
    status: str = "DISCOVERED"  # DISCOVERED, INSPECTED, SKIPPED, FAILED


class ObservedElement(BaseModel):
    __test__ = False
    element_id: str
    tag: str
    element_type: Optional[str] = None
    name_or_id: Optional[str] = None
    accessible_name: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    is_interactive: bool = False


class ObservedFeature(BaseModel):
    __test__ = False
    feature_id: str
    name: str
    category: str  # auth, registration, search, ecommerce, booking, banking, table_dashboard, navigation, dialog_tabs, validation
    description: str
    detection_method: str = "DETERMINISTIC_HEURISTIC"  # DETERMINISTIC_HEURISTIC or LLM_INTERPRETED
    confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    associated_urls: List[str] = Field(default_factory=list)
    supporting_evidence_ids: List[str] = Field(default_factory=list)
    provenance: str = "OBSERVED"  # OBSERVED, INFERRED, REQUIREMENT
    known_unknowns: List[str] = Field(default_factory=list)


class PageEvidence(BaseModel):
    __test__ = False
    page_id: str
    url: str
    final_url: str
    page_title: str
    meta_description: Optional[str] = None
    depth: int = 0
    http_status: Optional[int] = None
    page_category: PageCategory = PageCategory.UNKNOWN
    headings: List[str] = Field(default_factory=list)
    visible_text_snippet: str = ""
    buttons: List[str] = Field(default_factory=list)
    forms: List[FormMetadata] = Field(default_factory=list)
    observed_elements: List[ObservedElement] = Field(default_factory=list)
    discovered_links: List[str] = Field(default_factory=list)
    has_tables: bool = False
    has_pagination: bool = False
    has_dialogs: bool = False
    has_tabs: bool = False
    screenshot_artifact_id: Optional[str] = None
    console_errors: List[str] = Field(default_factory=list)
    inspection_duration_seconds: float = 0.0


class PageInventory(BaseModel):
    __test__ = False
    total_discovered: int = 0
    total_inspected: int = 0
    pages: List[PageEvidence] = Field(default_factory=list)
    discovered_links: List[DiscoveredLink] = Field(default_factory=list)
    skipped_urls: List[str] = Field(default_factory=list)
    failed_urls: List[str] = Field(default_factory=list)


class InvestigationReport(BaseModel):
    __test__ = False
    investigation_id: str
    starting_url: str
    origin: str
    inventory: PageInventory
    features: List[ObservedFeature] = Field(default_factory=list)
    discovery_completeness: str = "PARTIAL_BOUNDED"  # PARTIAL_BOUNDED or COMPLETE_WITHIN_BUDGET
    unresolved_questions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    duration_seconds: float = 0.0

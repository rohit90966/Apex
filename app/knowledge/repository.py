"""
Domain Knowledge Base Repository.
Loads, validates, and indexes reusable domain patterns from JSON definitions.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional, Set

from app.intelligence.archetypes import WebsiteArchetype, ApplicationCapability
from app.knowledge.schema import DomainKnowledgePattern

logger = logging.getLogger(__name__)

DEFAULT_PATTERNS_DIR = os.path.join(os.path.dirname(__file__), "patterns")


class DomainKnowledgeRepository:
    """
    Central repository for structured domain testing knowledge patterns.
    """

    def __init__(self, patterns_dir: Optional[str] = None):
        self.patterns_dir = patterns_dir or DEFAULT_PATTERNS_DIR
        self._patterns_by_id: Dict[str, DomainKnowledgePattern] = {}
        self._patterns_by_archetype: Dict[WebsiteArchetype, List[DomainKnowledgePattern]] = {
            arch: [] for arch in WebsiteArchetype
        }
        self._patterns_by_capability: Dict[ApplicationCapability, List[DomainKnowledgePattern]] = {
            cap: [] for cap in ApplicationCapability
        }
        self._loaded = False
        self.load_patterns()

    def load_patterns(self, force_reload: bool = False) -> int:
        """Loads and indexes all pattern JSON files from patterns_dir."""
        if self._loaded and not force_reload:
            return len(self._patterns_by_id)

        self._patterns_by_id.clear()
        for arch in WebsiteArchetype:
            self._patterns_by_archetype[arch] = []
        for cap in ApplicationCapability:
            self._patterns_by_capability[cap] = []

        if not os.path.exists(self.patterns_dir):
            logger.warning("Domain patterns directory '%s' does not exist", self.patterns_dir)
            self._loaded = True
            return 0

        total_loaded = 0
        for fname in sorted(os.listdir(self.patterns_dir)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(self.patterns_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)

                items = raw_data if isinstance(raw_data, list) else [raw_data]
                for item in items:
                    pattern = DomainKnowledgePattern(**item)
                    self._patterns_by_id[pattern.pattern_id] = pattern

                    for arch in pattern.archetypes:
                        self._patterns_by_archetype[arch].append(pattern)

                    for cap in pattern.capabilities:
                        self._patterns_by_capability[cap].append(pattern)

                    total_loaded += 1
            except Exception as e:
                logger.error("Failed to load pattern file '%s': %s", fname, e)

        logger.info("Loaded %d domain knowledge patterns from %s", total_loaded, self.patterns_dir)
        self._loaded = True
        return total_loaded

    def get_pattern(self, pattern_id: str) -> Optional[DomainKnowledgePattern]:
        return self._patterns_by_id.get(pattern_id)

    def list_all_patterns(self) -> List[DomainKnowledgePattern]:
        return list(self._patterns_by_id.values())

    def find_by_archetype(self, archetype: WebsiteArchetype) -> List[DomainKnowledgePattern]:
        return list(self._patterns_by_archetype.get(archetype, []))

    def find_by_capability(self, capability: ApplicationCapability) -> List[DomainKnowledgePattern]:
        return list(self._patterns_by_capability.get(capability, []))


# Global singleton instance
_repository_instance: Optional[DomainKnowledgeRepository] = None


def get_knowledge_repository() -> DomainKnowledgeRepository:
    global _repository_instance
    if _repository_instance is None:
        _repository_instance = DomainKnowledgeRepository()
    return _repository_instance

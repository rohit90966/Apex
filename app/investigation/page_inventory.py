from typing import List, Dict, Optional, Set
from app.investigation.models import PageEvidence, DiscoveredLink, PageInventory


class PageInventoryManager:
    """
    Maintains inventory of discovered and inspected pages, managing URL deduplication,
    crawl depth, and link tracking.
    """

    def __init__(self):
        self.inspected_urls: Set[str] = set()
        self.queued_urls: Set[str] = set()
        self.pages: List[PageEvidence] = []
        self.discovered_links: List[DiscoveredLink] = []
        self.skipped_urls: List[str] = []
        self.failed_urls: List[str] = []

    def record_discovered_link(self, link: DiscoveredLink):
        self.discovered_links.append(link)
        self.queued_urls.add(link.normalized_url)

    def record_inspected_page(self, page: PageEvidence):
        self.pages.append(page)
        self.inspected_urls.add(page.url)
        if page.final_url:
            self.inspected_urls.add(page.final_url)

    def record_skipped(self, url: str, reason: str):
        self.skipped_urls.append(f"{url} ({reason})")

    def record_failed(self, url: str, error: str):
        self.failed_urls.append(f"{url} ({error})")

    def is_already_seen(self, url: str) -> bool:
        return url in self.inspected_urls or url in self.queued_urls

    def build_inventory(self) -> PageInventory:
        return PageInventory(
            total_discovered=len(self.discovered_links),
            total_inspected=len(self.pages),
            pages=self.pages,
            discovered_links=self.discovered_links,
            skipped_urls=self.skipped_urls,
            failed_urls=self.failed_urls,
        )

import pytest
from unittest.mock import patch, MagicMock
from app.investigation.crawler import WebsiteCrawler
from app.investigation.models import InvestigationReport


def test_crawler_mocked_inspection():
    crawler = WebsiteCrawler()

    # Verify crawler bounds & error handling when host is not allowed
    with pytest.raises(Exception):
        crawler._sync_crawl("https://unapproved-external-domain.com")


def test_crawler_respects_depth_and_limits():
    crawler = WebsiteCrawler()

    # Mock Playwright to test discovery logic safely without starting network
    with patch("app.investigation.crawler.sync_playwright") as mock_playwright:
        mock_p = MagicMock()
        mock_playwright.return_value.__enter__.return_value = mock_p
        mock_browser = MagicMock()
        mock_p.chromium.launch.return_value = mock_browser
        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page

        mock_page.title.return_value = "Demo Target Home"
        mock_page.url = "http://127.0.0.1:8001/"
        mock_page.evaluate.side_effect = [
            ["Welcome to Demo Portal"],  # headings
            "Body snippet with navigation links",  # body
            ["Login", "Register"],  # buttons
            {"has_tables": False, "has_pagination": False, "has_dialogs": False, "has_tabs": False},  # struct flags
            [],  # forms
            [
                {"href": "/login", "text": "Login"},
                {"href": "/registration", "text": "Register"},
                {"href": "https://external.com", "text": "External"},
            ],  # links
            # Second page (/login)
            ["User Sign In"],
            "Login form body",
            ["Log In"],
            {"has_tables": False, "has_pagination": False, "has_dialogs": False, "has_tabs": False},
            [],
            [],
        ]

        report = crawler._sync_crawl("http://127.0.0.1:8001/", max_pages=2, max_depth=1)

        assert report.inventory.total_inspected <= 2
        assert len(report.features) > 0
        assert report.discovery_completeness in ("PARTIAL_BOUNDED", "COMPLETE_WITHIN_BUDGET")

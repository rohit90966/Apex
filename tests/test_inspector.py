import pytest
from unittest.mock import AsyncMock, patch
from app.schemas import InspectionResult
from app.inspector import WebsiteInspector


@pytest.mark.asyncio
async def test_inspector_mock():
    # Test inspector interface and structure
    with patch.object(WebsiteInspector, "inspect", new_callable=AsyncMock) as mock_inspect:
        mock_inspect.return_value = InspectionResult(
            inspection_id="mock-id",
            target_url="http://127.0.0.1:8001/login",
            final_url="http://127.0.0.1:8001/login",
            page_title="Mocked Page",
            http_status=200,
            headings=["Mock Heading"],
            buttons=["Submit"],
            links=["Home"],
            screenshot_artifact_id="screenshot-mock.png",
            inspection_duration_seconds=0.45,
        )

        inspector = WebsiteInspector()
        result = await inspector.inspect("http://127.0.0.1:8001/login")

        assert result.inspection_id == "mock-id"
        assert result.page_title == "Mocked Page"
        assert result.http_status == 200
        assert result.screenshot_artifact_id == "screenshot-mock.png"

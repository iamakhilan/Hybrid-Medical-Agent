from unittest.mock import AsyncMock, patch
import unittest

from fastapi.testclient import TestClient

from hybrid_agent.agent import SourceResult
from hybrid_agent.api import app


class ApiEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("hybrid_agent.api.get_agent")
    def test_list_documents_endpoint(self, mock_get_agent):
        mock_agent = AsyncMock()
        mock_agent.list_cached_documents.return_value = [
            {
                "doc_id": "doc_1",
                "title": "Patient Record",
                "source": "manual",
                "created_at": "2026-10-22T10:00:00",
            }
        ]
        mock_get_agent.return_value = mock_agent

        response = self.client.get("/api/documents")
        self.assertEqual(response.status_code, 200)
        docs = response.json()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["doc_id"], "doc_1")
        self.assertEqual(docs[0]["title"], "Patient Record")

    @patch("hybrid_agent.api.get_agent")
    def test_health_endpoint(self, mock_get_agent):
        mock_agent = AsyncMock()
        mock_agent.health_check.return_value = (
            SourceResult(ok=True, text="Local cache ready"),
            SourceResult(ok=True, text="Cloud MCP server reachable"),
        )
        mock_get_agent.return_value = mock_agent

        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["local_ok"])
        self.assertTrue(data["cloud_ok"])

    @patch("hybrid_agent.api.get_agent")
    def test_ask_endpoint(self, mock_get_agent):
        from hybrid_agent.agent import MergedResponse

        mock_agent = AsyncMock()
        mock_agent.ask.return_value = MergedResponse(
            query="What medications is the patient taking?",
            local=SourceResult(ok=True, text="Amlodipine 5mg once daily", latency_seconds=0.005),
            cloud=SourceResult(ok=False, error="cloud unavailable"),
            used_cloud=False,
            merged_text="### Documented Medications\n\n- Amlodipine 5 mg once daily",
        )
        mock_agent.search_cloud_sources.return_value = []
        mock_agent.search_local_sources.return_value = [
            {
                "doc_id": "doc_1",
                "title": "Patient Record",
                "page_number": 1,
                "chunk_index": 0,
                "text": "CURRENT MEDICATIONS: Amlodipine 5 mg once daily",
                "score": 0.95,
            }
        ]
        mock_get_agent.return_value = mock_agent

        response = self.client.post("/api/ask", json={"question": "What medications is the patient taking?", "top_k": 5})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("Documented Medications", data["answer"])
        self.assertIn("Amlodipine 5 mg once daily", data["answer"])
        self.assertEqual(len(data["citations"]), 1)
        self.assertEqual(data["citations"][0]["doc_id"], "doc_1")


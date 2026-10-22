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

"""Regression tests for hybrid response selection policy."""
import unittest
from hybrid_agent.agent import HybridAgent, SourceResult

class MergePolicyTests(unittest.TestCase):
    def setUp(self):
        self.agent = HybridAgent()

    def test_prefers_cloud_and_includes_local_preview(self):
        local = SourceResult(ok=True, text="Local cached finding: HbA1c 8.4%.", latency_seconds=0.001)
        cloud = SourceResult(ok=True, text="Cloud result: HbA1c 8.4%.", latency_seconds=1.2)
        result = self.agent._merge("What is HbA1c?", local, cloud)
        self.assertTrue(result.used_cloud)
        self.assertIn("Cloud result", result.merged_text)
        self.assertIn("Local fast preview", result.merged_text)

    def test_falls_back_to_local_when_cloud_fails(self):
        local = SourceResult(ok=True, text="Cached result", latency_seconds=0.001)
        cloud = SourceResult(ok=False, error="missing API key", latency_seconds=0.01)
        result = self.agent._merge("question", local, cloud)
        self.assertFalse(result.used_cloud)
        self.assertEqual(result.merged_text, "Cached result")

    def test_falls_back_when_cloud_has_no_document_context(self):
        local = SourceResult(ok=True, text="Cached patient detail", latency_seconds=0.001)
        cloud = SourceResult(ok=True, text="No relevant documents found in the cloud vector store.", latency_seconds=0.01)
        result = self.agent._merge("question", local, cloud)
        self.assertFalse(result.used_cloud)
        self.assertEqual(result.merged_text, "Cached patient detail")

    def test_reports_both_failures_without_raising(self):
        local = SourceResult(ok=False, error="local failed")
        cloud = SourceResult(ok=False, error="cloud failed")
        result = self.agent._merge("question", local, cloud)
        self.assertFalse(result.used_cloud)
        self.assertIn("local failed", result.merged_text)
        self.assertIn("cloud failed", result.merged_text)

if __name__ == "__main__":
    unittest.main()

"""Regression tests for source-linked clinical timeline and safety heuristics."""
import unittest

from hybrid_agent.clinical_records import build_timeline, detect_contradictions


class ClinicalRecordsTests(unittest.TestCase):
    def test_timeline_preserves_source_page_and_date(self):
        sources = [{
            "doc_id": "lab-report",
            "title": "Lab Report",
            "text": "[[PAGE:4]]\n2025-01-02 Potassium: 5.4 mmol/L",
        }]
        events = build_timeline(sources)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["date"], "2025-01-02")
        self.assertEqual(events[0]["category"], "Lab result")
        self.assertEqual(events[0]["page_number"], 4)
        self.assertEqual(events[0]["source_title"], "Lab Report")

    def test_flags_conflicting_medication_doses(self):
        sources = [
            {"doc_id": "a", "title": "Visit A", "text": "[[PAGE:1]]\n2025-01-01 Metformin 500 mg daily"},
            {"doc_id": "b", "title": "Visit B", "text": "[[PAGE:2]]\n2025-02-01 Metformin 1000 mg daily"},
        ]
        findings = detect_contradictions(sources)
        self.assertTrue(any(item["kind"] == "medication_dose_conflict" for item in findings))

    def test_flags_allergy_status_conflict(self):
        sources = [
            {"doc_id": "a", "title": "Intake", "text": "[[PAGE:1]]\n2025-01-01 No known allergies"},
            {"doc_id": "b", "title": "Discharge", "text": "[[PAGE:2]]\n2025-02-01 Allergy to penicillin documented"},
        ]
        findings = detect_contradictions(sources)
        self.assertTrue(any(item["kind"] == "allergy_record_conflict" for item in findings))

    def test_flags_lab_unit_variation(self):
        sources = [
            {"doc_id": "a", "title": "Lab A", "text": "[[PAGE:1]]\n2025-01-01 Potassium 5.4 mmol/L"},
            {"doc_id": "b", "title": "Lab B", "text": "[[PAGE:1]]\n2025-02-01 Potassium 5.4 mEq/L"},
        ]
        findings = detect_contradictions(sources)
        self.assertTrue(any(item["kind"] == "lab_unit_conflict" for item in findings))


if __name__ == "__main__":
    unittest.main()

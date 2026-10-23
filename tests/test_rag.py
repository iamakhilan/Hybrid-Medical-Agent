"""Tests for RAG synthesis and retrieval across clinical queries."""

import unittest
from local_mcp_server.db import SearchResult
from local_mcp_server.local_llm import synthesize_rag_response


class RagSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.sample_chunks = [
            SearchResult(
                doc_id="sample_patient_record",
                title="Sample Patient Record",
                snippet=(
                    "PATIENT MEDICAL RECORD\n"
                    "Patient ID   : PT-2024-00481\n"
                    "Name         : Arjun Mehta\n"
                    "Date of Birth: 14 March 1978  (Age: 46)\n"
                    "Gender       : Male\n"
                    "ATTENDING PHYSICIAN: Dr. Priya Nair, MBBS, MD (Internal Medicine)\n"
                    "Hospital     : Apollo Hospitals, Bengaluru"
                ),
                score=10.0,
            ),
            SearchResult(
                doc_id="sample_patient_record",
                title="Sample Patient Record",
                snippet=(
                    "CURRENT MEDICATIONS\n"
                    "1. Amlodipine 5 mg — once daily (morning) — for hypertension\n"
                    "2. Atorvastatin 10 mg — once daily (night) — for dyslipidemia\n"
                    "3. Multivitamin tablet — once daily"
                ),
                score=9.5,
            ),
            SearchResult(
                doc_id="sample_patient_record",
                title="Sample Patient Record",
                snippet=(
                    "LABORATORY INVESTIGATIONS\n"
                    "Date: 10 August 2026\n"
                    "HbA1c                           : 8.4%         [Ref: <5.7%]    HIGH\n"
                    "Fasting Blood Glucose (FBG)     : 198 mg/dL   [Ref: 70-100]   HIGH\n"
                    "Post-Prandial Glucose (2hr PPG) : 286 mg/dL   [Ref: <140]     HIGH"
                ),
                score=9.0,
            ),
            SearchResult(
                doc_id="sample_patient_record",
                title="Sample Patient Record",
                snippet=(
                    "Vitals:\n"
                    "  Blood Pressure   : 138/88 mmHg\n"
                    "  Heart Rate       : 82 bpm (regular)\n"
                    "  Temperature      : 36.8 degrees C\n"
                    "  SpO2             : 98% on room air\n"
                    "  Weight           : 89 kg\n"
                    "  BMI              : 30.1 kg/m2 (Obese Class I)"
                ),
                score=8.5,
            ),
            SearchResult(
                doc_id="sample_patient_record",
                title="Sample Patient Record",
                snippet=(
                    "PAST MEDICAL HISTORY\n"
                    "- Hypertension (diagnosed 2019)\n"
                    "- Dyslipidemia (diagnosed 2021)\n"
                    "- No prior surgeries\n"
                    "- No known drug allergies"
                ),
                score=8.0,
            ),
        ]

    def test_medication_query_synthesis(self):
        answer = synthesize_rag_response("What medications is the patient taking?", self.sample_chunks)
        self.assertIn("Documented Medications", answer)
        self.assertIn("Amlodipine", answer)
        self.assertIn("5 mg", answer)
        self.assertIn("Atorvastatin", answer)
        self.assertNotIn("Based on 5 cached local document(s):", answer)

    def test_lab_query_synthesis(self):
        answer = synthesize_rag_response("What is the patient HbA1c level?", self.sample_chunks)
        self.assertIn("Laboratory Findings", answer)
        self.assertIn("8.4%", answer)
        self.assertIn("HIGH", answer)

    def test_demographics_query_synthesis(self):
        answer = synthesize_rag_response("What is the patient's name and age?", self.sample_chunks)
        self.assertIn("Demographics", answer)
        self.assertIn("Arjun Mehta", answer)
        self.assertIn("46", answer)

    def test_attending_doctor_query_synthesis(self):
        answer = synthesize_rag_response("Who is the attending doctor?", self.sample_chunks)
        self.assertIn("Dr. Priya Nair", answer)
        self.assertIn("Apollo Hospitals", answer)

    def test_vitals_query_synthesis(self):
        answer = synthesize_rag_response("What are the patient's vital signs?", self.sample_chunks)
        self.assertIn("Vital Signs", answer)
        self.assertIn("138/88 mmHg", answer)
        self.assertIn("82 bpm", answer)

    def test_allergy_query_synthesis(self):
        answer = synthesize_rag_response("Does the patient have any allergies?", self.sample_chunks)
        self.assertIn("Allergy", answer)
        self.assertIn("No known drug allergies", answer)

    def test_greeting_handling(self):
        answer = synthesize_rag_response("Hello!", [])
        self.assertIn("Hello! I am your clinical RAG assistant", answer)

    def test_empty_chunks_returns_helpful_notice(self):
        answer = synthesize_rag_response("What is the patient's sodium level?", [])
        self.assertIn("No relevant clinical records matching", answer)

    def test_never_produces_raw_snippet_dump(self):
        answer = synthesize_rag_response("Summary of the patient", self.sample_chunks)
        self.assertFalse(answer.startswith("Based on 5 cached local document(s):"))


if __name__ == "__main__":
    unittest.main()

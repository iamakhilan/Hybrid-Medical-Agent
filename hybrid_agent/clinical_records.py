"""Heuristic, source-linked clinical record exploration.

These rules surface candidate facts and inconsistencies for human review. They do
not diagnose, recommend treatment, or resolve conflicting source records.
"""
from __future__ import annotations

import re
from datetime import date, datetime

PAGE_MARKER = re.compile(r"\[\[PAGE:(\d+)\]\]")
DATE_PATTERN = re.compile(
    r"\b(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b"
)
CLINICAL_TERMS = re.compile(
    r"\b(lab|potassium|sodium|creatinine|glucose|hba1c|hemoglobin|diagnos\w*|"
    r"medication|prescrib\w*|dose|mg\b|mcg\b|allerg\w*|blood pressure|"
    r"treatment|started|stopped|discontinued|symptom|admitted|discharged|"
    r"procedure|heart rate|temperature|follow[- ]?up)\b", re.I
)
MED_DOSE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9-]{2,})\s+(?:dose\s*(?:is|of)?\s*)?"
    r"(\d+(?:\.\d+)?)\s*(mg|mcg|µg|g|ml|mL|units?|IU)\b", re.I
)
LAB_NAMES = ("potassium", "sodium", "creatinine", "glucose", "hba1c", "hemoglobin", "platelets", "calcium", "chloride", "bicarbonate")
UNIT_PATTERN = re.compile(r"\b(mmol/L|mmol|mEq/L|mg/dL|mmHg|g/dL|10\^9/L|U/L|IU/L|%|mg|mcg|µg|ml|mL)\b", re.I)


def _parse_date(raw: str) -> str | None:
    value = raw.strip().replace(",", "")
    formats = (
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
        "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
        "%d/%m/%y", "%m/%d/%y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _page_sections(text: str) -> list[tuple[int | None, str]]:
    matches = list(PAGE_MARKER.finditer(text or ""))
    if not matches:
        return [(None, text or "")]
    sections = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((int(match.group(1)), text[match.end():end]))
    return sections


def _iter_lines(source: dict):
    for page, page_text in _page_sections(str(source.get("text", ""))):
        for line in page_text.splitlines():
            line = line.strip(" \t-•")
            if line:
                yield page, line


def build_timeline(sources: list[dict]) -> list[dict]:
    """Return dated clinical events with source document and page provenance."""
    events = []
    for source in sources:
        for page, line in _iter_lines(source):
            if not CLINICAL_TERMS.search(line):
                continue
            date_match = DATE_PATTERN.search(line)
            if not date_match:
                continue
            normalized_date = _parse_date(date_match.group(1))
            if not normalized_date:
                continue
            lower = line.lower()
            if re.search(r"\b(potassium|sodium|creatinine|glucose|hba1c|hemoglobin|lab|mmol/l|mg/dl)\b", lower):
                category = "Lab result"
            elif re.search(r"\b(allerg\w*)\b", lower):
                category = "Allergy"
            elif re.search(r"\b(medication|prescrib\w*|dose|mg\b|mcg\b|tablet|capsule)\b", lower):
                category = "Medication"
            elif re.search(r"\b(diagnos\w*|condition|disease)\b", lower):
                category = "Diagnosis"
            else:
                category = "Clinical note"
            events.append({
                "date": normalized_date,
                "category": category,
                "description": line[:600],
                "doc_id": str(source.get("doc_id", "")),
                "source_title": str(source.get("title") or source.get("doc_id") or "Source document"),
                "page_number": page,
            })
    events.sort(key=lambda event: (event["date"], event["source_title"], event["page_number"] or 0))
    return events


def detect_contradictions(sources: list[dict]) -> list[dict]:
    """Surface possible medication, allergy, unit, date, and freshness issues."""
    findings: list[dict] = []
    dose_mentions: dict[str, list[dict]] = {}
    allergy_mentions: list[dict] = []
    lab_units: dict[str, list[dict]] = {}
    today = date.today()

    for source in sources:
        for page, line in _iter_lines(source):
            base = {
                "doc_id": str(source.get("doc_id", "")),
                "source_title": str(source.get("title") or source.get("doc_id") or "Source document"),
                "page_number": page,
                "passage": line[:500],
            }
            low = line.lower()
            date_match = DATE_PATTERN.search(line)
            normalized_date = _parse_date(date_match.group(1)) if date_match else None

            for match in MED_DOSE.finditer(line):
                medicine = match.group(1).strip().lower()
                if medicine in {"dose", "taking", "take", "given", "daily", "tablet", "capsule", "every", "about"}:
                    continue
                mention = {**base, "medicine": medicine, "dose": match.group(2), "unit": match.group(3).lower(), "date": normalized_date}
                dose_mentions.setdefault(medicine, []).append(mention)

            if re.search(r"\ballerg(?:y|ies|ic)\b", low):
                known_none = bool(re.search(r"\b(no known|none known|no documented|denies)\b.{0,30}\ballerg", low))
                allergy_match = re.search(r"\ballerg(?:y|ies|ic)\b.{0,35}", line, re.I)
                allergy_mentions.append({**base, "none_reported": known_none, "allergy_text": allergy_match.group(0) if allergy_match else line, "date": normalized_date})

            for lab in LAB_NAMES:
                if re.search(rf"\b{re.escape(lab)}\b", low):
                    units = [unit.lower() for unit in UNIT_PATTERN.findall(line)]
                    if units:
                        lab_units.setdefault(lab, []).append({**base, "unit": units[-1], "date": normalized_date})
                    elif re.search(r"\b\d+(?:\.\d+)?\b", line):
                        findings.append({
                            "kind": "missing_unit", "severity": "review",
                            "title": f"Unit not explicit for {lab}",
                            "message": "A numeric value appears near this lab name, but a unit was not detected in the same line. Verify the original record.",
                            **base,
                        })

            if CLINICAL_TERMS.search(line) and not normalized_date:
                findings.append({
                    "kind": "missing_date", "severity": "review",
                    "title": "Clinical detail without a clear date",
                    "message": "This clinical detail has no recognizable date on the same line; its timing may be unclear.",
                    **base,
                })
            if normalized_date and re.search(r"\b(medication|prescrib\w*|dose|mg\b|mcg\b|tablet|capsule)\b", low):
                try:
                    age_days = (today - date.fromisoformat(normalized_date)).days
                except ValueError:
                    age_days = 0
                if age_days > 365:
                    findings.append({
                        "kind": "possibly_stale", "severity": "review",
                        "title": "Older medication record",
                        "message": f"This medication-related entry is dated {normalized_date} (over one year ago). Confirm whether it is still current.",
                        **base,
                    })

    for medicine, mentions in dose_mentions.items():
        unique = {(m["dose"], m["unit"]) for m in mentions}
        if len(unique) > 1:
            findings.append({
                "kind": "medication_dose_conflict", "severity": "high_review",
                "title": f"Potential dose discrepancy: {medicine.title()}",
                "message": "Different doses/units appear in the supplied records. This detector cannot determine which entry is current; review both sources with a qualified clinician.",
                "mentions": mentions,
            })

    for i, left in enumerate(allergy_mentions):
        for right in allergy_mentions[i + 1:]:
            if left["doc_id"] == right["doc_id"] and left["page_number"] == right["page_number"]:
                continue
            if left["none_reported"] != right["none_reported"]:
                findings.append({
                    "kind": "allergy_record_conflict", "severity": "high_review",
                    "title": "Potential allergy-record discrepancy",
                    "message": "One record appears to report no known allergies while another contains an allergy-related entry. Verify the exact allergy status against the original records.",
                    "mentions": [left, right],
                })

    for lab, mentions in lab_units.items():
        units = {mention["unit"] for mention in mentions}
        if len(units) > 1:
            findings.append({
                "kind": "lab_unit_conflict", "severity": "review",
                "title": f"Different units recorded for {lab.title()}",
                "message": "The same lab name appears with different units across the supplied records. Confirm unit conversion and source context before comparing values.",
                "mentions": mentions,
            })

    # Stable order and cap output to keep the UI usable on large record sets.
    seen = set()
    unique_findings = []
    for finding in findings:
        signature = (finding["kind"], finding["title"], finding.get("doc_id"), finding.get("page_number"), finding.get("passage"))
        if signature in seen:
            continue
        seen.add(signature)
        unique_findings.append(finding)
    return unique_findings[:200]

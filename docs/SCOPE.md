# Scope rules

These rules are non-negotiable. They keep Evidex within the Health-a-thon 2026 scope, which excludes diagnosis, treatment recommendations, clinical decision support, clinical risk scoring, interpretation of medical data, and autonomous clinical advice.

1. **Patient facts come only from the doctor.** Every patient-fact sentence must trace to an exact text span in doctor-provided input. The system extracts and restructures. It never infers, interprets or adds.
2. **Evidence is general, never patient-specific.** Evidex reports what guidelines and studies say. It never states what a patient should receive, and never judges whether evidence applies to a patient.
3. **Clinical reasoning stays with the doctor.** For example, the reason for referral is a required field that the doctor writes.
4. **Missing information becomes a question.** Empty sections show "Not provided"; they are never filled in.
5. **No real patient data in the MVP.** Use synthetic cases only. A PII scrubber runs on all input.
6. **No autonomous action.** Nothing is sent or published. Export unlocks only after sign-off.
7. **No answer without sources.** If the library has nothing, Evidex says "Not found in sources."
8. **No invented references.** Every citation must come from a real API response or a real library document.

## Amendment, 7 Nov 2026 (approved by Dr Vignesh M for the three-phase version)

9. **Two source layers.** The spine (ADA 2026, Sections 2-3) is the grounding layer: a topic the spine does not recognise is answered "Not found in sources", and guideline numbers are checked against it. Published literature from PubMed, and papers or decks the user uploads, add context on top of a spine topic. Every literature statement cites a real PubMed record (PMID and link taken from the API response) or the uploaded file, and every number in it must appear in that source's text (checked by code).
10. **Uploads are papers or teaching material, never patient records.** A PII check runs on every uploaded file and typed request and blocks text that looks like a patient record (record numbers, dates of birth, phone or Aadhaar numbers).
11. **Patient and public persona = general education only.** Plain language, no individual advice, and every output for this persona ends with "General information, not advice for any individual. Talk to your doctor."

If any change conflicts with these rules, it must not be merged.

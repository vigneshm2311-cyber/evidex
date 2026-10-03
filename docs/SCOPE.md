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

If any change conflicts with these rules, it must not be merged.

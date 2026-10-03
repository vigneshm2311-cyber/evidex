# Decisions log

| Date | Decision | Reason |
|------|----------|--------|
| 2026-10-02 | Represent content as structured claims, not prose | Makes verification, review and reuse across documents tractable |
| 2026-10-02 | Fixed LangGraph route rather than free-roaming agents | Predictable and auditable; no step can be skipped |
| 2026-10-02 | Deterministic code for reference, quote and number checks | These checks must not hallucinate |
| 2026-10-02 | Synthetic patient cases only in the MVP | Hackathon scope and data protection |

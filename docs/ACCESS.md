# Evidex: access instructions for evaluators

**Link:** https://evidex-synexis.streamlit.app
**Access code:** given in the submission form (not stored in this public repository)

1. Open the link. If the app has been idle, Streamlit shows a short "waking up" screen (about 30 s); click the button if one appears.
2. Enter the access code.
3. Open the **ℹ️ How to test** tab and follow the steps (about 10 minutes). In short:
   - a patient-specific dose question is **declined**;
   - a dosing or device topic returns **"Not found in sources"**;
   - a teaching topic produces a **draft** whose claims each cite spine rows with their ADA anchor;
   - **editing a number** to a wrong value is flagged by the Verifier and cannot be approved;
   - after **sign-off**, the draft downloads as **PPTX** or **DOCX** containing exactly the approved text.

## Good to know
- Evidex is hosted on Streamlit Community Cloud with its database on Supabase (Mumbai). The language model is Qwen3.8-27B on Groq: the request text and the selected spine rows are sent to Groq, so Evidex is for general teaching topics only, never patient details.
- A draft takes about 1–3 minutes (mostly waiting for the free model tier's rate limits). One draft runs at a time; if another evaluator is running one, the app asks you to wait a minute. Limit: 10 drafts per hour per visitor.
- Evaluators can create, review, sign off and export. Editing the knowledge spine needs the doctor's separate code.
- No patient data is used or stored. Evidex gives general guideline information only, never advice about an individual patient.
- Please keep the page open while a draft runs; if the app restarts mid-run, finished drafts are still in the **Drafts** tab.

**Contact:** TODO(doctor) email

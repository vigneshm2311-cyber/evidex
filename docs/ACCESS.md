# Evidex: access instructions for evaluators

**Link:** https://evidex-synexis.streamlit.app
**Access code:** given in the submission form (not stored in this public repository)

1. Open the link. If the app has been idle, Streamlit shows a short "waking up" screen (about 30 s); click the button if one appears.
2. Enter the access code. The **ℹ️ How it works** tab has the same steps.
3. **Make something (about 5 minutes).** In **✨ Create**, pick an example such as *"Flowchart for screening type 2 diabetes in adults"* (or type your own, and attach a paper PDF or a PPTX deck if you like), choose who it is for and the format, and press **Continue**.
   - Evidex says what it understood (title, topic, the questions it will answer). Edit anything, then press **Build it** (2–5 minutes).
   - The draft appears in the chosen format with numbered references (guideline entries and PubMed papers).
4. **Try to break it.** Open **✏️** on a statement, change a number (e.g. 25 to 27) and save: it is flagged *"A number does not match its source"* and approval is blocked until you fix or remove it.
5. **Approve and download.** Enter your name, tick the confirmation and press **Approve and get my file**: slides, handout, flowchart or one-page PDF, containing exactly the approved text.
6. **Safety.** Ask about a specific patient (*"What dose should I start for my 54-year-old patient?"*): Evidex declines and suggests a general question. Ask for an uncovered topic (*"Insulin pump settings"*): **"Not found in sources"**.

## Good to know
- Evidex grounds every draft in a doctor-curated knowledge base from the ADA Standards of Care 2026 (diagnosis, classification, screening, prevention) and adds context from recent PubMed research and any files you attach. Every PubMed reference comes from PubMed itself, and every number is checked by code against its source.
- Hosted on Streamlit Community Cloud with the database on Supabase (Mumbai). The language model is Qwen3.8-27B on Groq: your request, file text and the selected sources are sent to it, so use general topics and papers only, never patient details (a check blocks files that look like patient records).
- **Free AI tier:** the prototype runs on Groq's free tier (about 8,000 tokens a minute and 200,000 a day per model, shared by all evaluators; roughly 8–15 drafts a day per model). A busy minute makes a draft slower; when a model's daily cap is reached Evidex switches to backup models automatically. If a draft is slow or fails, please try again in a few minutes.
- One draft is built at a time; if another evaluator is building one, the app asks you to wait a minute. Limit: 10 requests per hour per visitor.
- **Library** holds the knowledge base (encyclopedia, graph, and the doctor's verification progress). Editing it needs the doctor's separate code.
- Drafts you create are visible to other evaluators in **My drafts**. Evidex gives general teaching material only, never advice about an individual patient.

**Demo video:** https://drive.google.com/file/d/1Jach-O1yG04XrHin774niluvRJNw7RR_/view?usp=sharing

**Contact:** Dr Vignesh M · vigneshm2311@gmail.com · Mughil · mughilarul@gmail.com

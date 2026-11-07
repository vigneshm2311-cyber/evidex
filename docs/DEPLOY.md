# Deploying Evidex on Streamlit Community Cloud (hosted model)

The online version runs the same code with `LLM_PROVIDER=groq`: the app on Streamlit Community Cloud,
the database on Supabase (unchanged), and the model (Qwen3.8-27B) on Groq. No laptop is needed.

## 1. Groq API key
1. Sign in at https://console.groq.com and create an API key (API Keys → Create).
2. For local testing, add it to `.env` (never commit it):
   ```
   LLM_PROVIDER=groq
   LLM_MODEL=qwen/qwen3.8-27b
   GROQ_API_KEY=<your key>
   ```
   Switch back to the local model with `LLM_PROVIDER=ollama` and `LLM_MODEL=qwen3:8b`.

## 2. Create the app
1. Sign in at https://share.streamlit.io with the GitHub account that owns `vigneshm2311-cyber/evidex`.
2. Create app → from GitHub repo: repository `vigneshm2311-cyber/evidex`, branch `main`, file `app.py`.
3. Advanced settings → Python version **3.11**.
4. Advanced settings → Secrets: paste the block below with your real values (from `.env`).
5. Deploy. The first build takes a few minutes. The link looks like `https://<name>.streamlit.app`.

```toml
DATABASE_URL = ""        # Supabase session pooler URI (same as .env)
ACCESS_CODE = ""         # evaluator code
DOCTOR_CODE = ""         # doctor code
SESSION_SECRET = ""      # any long random string
LLM_PROVIDER = "groq"
LLM_MODEL = "qwen/qwen3.8-27b"
GROQ_API_KEY = ""
RUNS_PER_HOUR = "10"
```

## Notes
- Root-level secrets are copied into the environment at start-up (`app.py`), so the code reads them like `.env`.
- A free Streamlit app sleeps after a period without visitors; the first visit wakes it (about 30 s).
- Drafts run in a background thread; if the app restarts mid-run that run is lost, but finished drafts are in the database.
- Groq's free tier allows ~8,000 tokens a minute and **200,000 tokens a day per model** (about 8-15 drafts). The client waits out a busy minute (up to ~3 min); when the main model's daily allowance is used up it switches to the backup models in `LLM_FALLBACKS` (default `openai/gpt-oss-120b,openai/gpt-oss-20b`, each with its own allowance). The model actually used is logged per call and named in each export's note. Groq's paid Dev tier removes the daily cap.
- The request text and the selected spine rows are sent to Groq. Evidex is for general teaching topics only; no patient data.
- Evaluation results for the hosted model: `eval/REPORT.md` (re-run with `LLM_PROVIDER=groq`).

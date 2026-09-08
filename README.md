![Job-CV generator logo](images/job_cv_ai_generator.jpg)

# Job-CV Matcher & Tailor

An AI based tool for people intensively applying to jobs.

Automated assistant to compare a LaTeX CV with a job description, highlight gaps, and generate tailored LaTeX CVs and cover letters.

Runs on your web browser.

**Inputs:**

1. A CV (plain text or LaTeX `.tex`)
2. A job description (Job URL, or paste description directly in the text area)

**Outputs:**

1. A **list of skills** and requirements that **Match** and **Do Not Match** between your CV and the job description.
2. A version of **your CV, adapted** to this job (output as LaTeX).
3. A **Cover Letter** for the job application (plain text).

---

## Supported Environments

| Platform | Works? | Notes |
| :--- | :--- | :--- |
| **Linux** | ✅ Yes | Fully supported. |
| **macOS** | ✅ Yes | Fully supported. |
| **Windows (WSL / Git Bash)** | ✅ Yes | Use Windows Subsystem for Linux or Git Bash (included with Git for Windows). |
| **Windows (CMD / PowerShell)** | ❌ No | The `Makefile` is not compatible with native Windows shells. |

If you are on Windows, **please use Git Bash** or **WSL** to run `make` commands. The `Makefile` includes a friendly error message if you accidentally run it in CMD/PowerShell.

> **Alternative:** You can also run the app directly with `streamlit run app.py` (see below) without using `make`.

---

## Requires

Relies on AI. You will need to provide an **API key for any AI** service of your choice.

Supported providers: **OpenAI GPT**, **Codex**, **Gemini 2.5 Flash**, **Groq**, and **Anthropic (Claude)**. Set the key in the sidebar; it is stored locally in `credentials.json`.

**Advice:** Edit `CV_guidelines.md` and `CL_guidelines.md` in the project root to guide generation. These files are passed to the AI as context, together with your CV and job description, to personalise your outputs.

---

## Quickstart

### 1. Set up your API keys

- Copy `credentials.json.example` to `credentials.json` and fill in your API keys.
- Alternatively, you can use [Streamlit Cloud Secrets](https://docs.streamlit.io/streamlit-community-cloud/get-started/deploy-an-app/connect-to-data-sources/secrets-management) for deployment.


### 2. Install dependencies

```bash
make setup
```

### 3. Run locally:

#### Option A: Using `make` (recommended on Linux/macOS/WSL/Git Bash)

```bash
make run
```

#### Option B: Direct call to streamlit server (any platform, after activating your virtual environment)

```bash
streamlit run app.py
```





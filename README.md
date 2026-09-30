![Job-CV generator logo](images/job_cv_ai_generator.jpg)

# Job-CV Matcher & Tailor

An AI based tool for people intensively applying to jobs.

Automated assistant to compare a LaTeX CV with a job description, highlight gaps, and generate tailored LaTeX CVs and cover letters.

Runs on your web browser.

**Inputs:**

1. A job description (Job URL, or paste description directly in the text area)
2. Your CV — uploaded once on the **Profile** page (`/profile`) as either LaTeX `.tex` or `.pdf`, then reused for every analysis. PDFs must contain selectable text (a scan will be rejected).
3. Your name, also on the **Profile** page — generated cover letters are signed with it.

**Outputs:**

1. A **list of skills** and requirements that **Match** and **Do Not Match** between your CV and the job description.
2. A version of **your CV, adapted** to this job (output as LaTeX).
3. A **Cover Letter** for the job application (plain text).
4. An **estimated pay range** for the position, shown under the company name. Toggle it in the sidebar
   (`Estimate compensation`). The data source is a pluggable provider in `src/compensation/estimator.py`;
   the default asks the configured AI for an estimate, and a licensed salary API can be dropped in by
   implementing `CompensationProvider`.
5. A **history of matches** — every analysis is saved (`/history`) with its requirements, match score and
   pay estimate, and can be reopened in the app. The newest 100 are kept per user.

---

## Supported Environments

| Platform | Works? | Notes |
| :--- | :--- | :--- |
| **Linux** | ✅ Yes | Fully supported. |
| **macOS** | ✅ Yes | Fully supported. |
| **Windows (WSL / Git Bash)** | ✅ Yes | Use Windows Subsystem for Linux or Git Bash (included with Git for Windows). |
| **Windows (CMD / PowerShell)** | ❌ No | The `Makefile` is not compatible with native Windows shells. |

If you are on Windows, **please use Git Bash** or **WSL** to run `make` commands. The `Makefile` includes a friendly error message if you accidentally run it in CMD/PowerShell.

> **Alternative:** You can also run the app directly with `streamlit run landing.py` (see below) without using `make`.

`landing.py` is the entrypoint (the marketing landing page at `/`); the actual tool lives in `pages/app.py` (`/app`), the account/CV page in `pages/profile.py` (`/profile`), and past analyses in `pages/history.py` (`/history`).

---

## Requires

Relies on AI. You will need to provide an **API key for any AI** service of your choice.

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
streamlit run landing.py
```

---

## Email confirmation (SMTP)

New accounts cannot log in until they confirm their email address. Mail is sent as
`jobcv@luilver.com` through any SMTP server.

### Route 1 — Google Workspace SMTP (needs an App Password)

1. Turn on 2-Step Verification for the sending account, then create an **App Password**
   (Google Account -> Security -> 2-Step Verification -> App passwords -> *Mail*).
2. Put the 16-character password in `.streamlit/secrets.toml` or `credentials.json`:

   ```toml
   SMTP_HOST = "smtp.gmail.com"
   SMTP_PORT = 465
   SMTP_USER = "jobcv@luilver.com"
   SMTP_APP_PASSWORD = "xxxx xxxx xxxx xxxx"   # spaces are stripped
   SMTP_FROM = "jobcv@luilver.com"
   SMTP_FROM_NAME = "Job-CV AI Generator"
   APP_BASE_URL = "https://your-deployment.example.com"   # used to build the link
   ```

> App Passwords are an **admin-controlled** Workspace feature. If the App Password page
> says "The setting you are looking for is not available for your account", the domain is
> on the free Cloud Identity tier (no security features) or an admin disabled app
> passwords. Either way, use Route 2.

### Route 2 — Transactional relay (no Google policy)

1. Create an account with a provider that supports custom sending domains (Resend,
   Brevo, Amazon SES, …) and add `luilver.com` as a sending domain — they will give you
   SPF and DKIM records to publish in DNS.
2. Copy the provider's SMTP settings into the same keys:

   ```toml
   SMTP_HOST = "smtp.resend.com"      # or smtp-relay.brevo.com
   SMTP_PORT = 465
   SMTP_USER = "resend"               # the provider's login, not the mailbox
   SMTP_APP_PASSWORD = "<api key>"     # the provider's SMTP key
   SMTP_FROM = "jobcv@luilver.com"    # the verified sender
   ```

Values may also come from environment variables with the same names.

### Verify

```bash
.venv/bin/python scripts/check_smtp.py            # authenticate + send a test email
.venv/bin/python scripts/check_smtp.py --no-send   # authenticate only
```

It prints the resolved host/login/sender (never the secret), flags a wrong-length Google
app password, and reminds you about SPF/DKIM in relay mode.

Links point at `{APP_BASE_URL}/confirm?verify=<token>`; tokens expire after 24 hours and
the login screen has a "Resend confirmation email" form. The landing page still accepts
the older `/?verify=` links.

`/confirm` is a separate route on purpose: if the app sits behind Cloudflare Access,
exempt `jobcv.luilver.com/confirm*` for everyone so subscribers can confirm without a
Cloudflare login, while the rest of the app stays private.

---

## TODO

See [TODO.md](TODO.md) for planned work (PayPal config persistence, recurring billing,
password reset, and more).






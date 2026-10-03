![Job-CV AI Generator logo](images/logo.svg)

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

`landing.py` is the entrypoint (the marketing landing page at `/`); the actual tool lives in `pages/app.py` (`/app`), the account/CV page in `pages/profile.py` (`/profile`), past analyses in `pages/history.py` (`/history`), and LinkedIn job discovery in `pages/jobs.py` (`/jobs`), with the OAuth redirect landing on `pages/linkedin_callback.py` (`/linkedin_callback`).

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

## Job Discovery (LinkedIn)

A daily pass that searches LinkedIn's public guest job search for remote roles, scores
each posting against your stored CV, and emails one digest of the matches with a tailored
CV and cover letter attached. It is on the **Job Discovery** page (`/jobs`).

**Nothing is submitted to LinkedIn.** Postings are only read. The digest links to each
posting so you apply yourself. This is a deliberate limit, not a missing feature: applying
requires a member session and automating it violates LinkedIn's terms and risks the account.

### Connect LinkedIn

1. Create an app at <https://www.linkedin.com/developers/apps> and request the
   **Sign In with LinkedIn using OpenID Connect** product.
2. Under **Auth → OAuth 2.0 settings**, add this redirect URL *exactly*:
   `{APP_BASE_URL}/linkedin_callback`
3. Put the credentials in `.streamlit/secrets.toml` (see `secrets.toml.example`):

   ```toml
   LINKEDIN_CLIENT_ID     = "..."
   LINKEDIN_CLIENT_SECRET = "..."
   ```

Only the three consumer OIDC scopes (`openid profile email`) are requested. Your name,
email and member id are stored; the access token is discarded as soon as the profile is
read. No LinkedIn credentials are ever kept.

### How a run scores postings

Search cards carry a title, company and location — but no description, so a keyword score
on the card alone would reject every real match. The run is therefore staged, cheapest
work first:

1. **Card triage** — a recall-biased yes/no so only plausible postings cost a fetch.
2. **Fetch descriptions** for the survivors, capped at 25.
3. **Keyword prefilter** on title + description, keeping the best few.
4. **AI review** — one call per survivor, up to `daily_llm_budget`.
5. **Write materials** for the top matches, up to `daily_gen_budget`.

Every posting the run considers is recorded, including the ones turned down, so a run
searches each posting once and never re-fetches it.

### Run it daily

```bash
# In docker-compose, the `worker` service polls and runs whatever is due.
docker compose up -d worker

# Or run a pass by hand:
.venv/bin/python scripts/daily_digest.py --dry-run     # who would run
.venv/bin/python scripts/daily_digest.py                # everyone due now
.venv/bin/python scripts/daily_digest.py --user-id 1    # one account, ignoring the hour
```

Each account has a digest hour and timezone under its discovery settings. The database
records when a digest last went out, so a restart or a second worker cannot send a
duplicate — the once-a-day stamp, not the scheduler, is what makes it safe.

---

## TODO

See [TODO.md](TODO.md) for planned work (PayPal config persistence, recurring billing,
password reset, and more).






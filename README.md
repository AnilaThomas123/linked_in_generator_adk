# LinkedIn Editorial Generator

A configuration-driven editorial pipeline that finds recent, evidence-backed material for three topics, drafts LinkedIn posts for senior executives, and stops at a human gate. A person approves, edits or discards every draft. Nothing is posted automatically: the approved post, first comment and infographic are copied to LinkedIn by hand, and the completed publication is confirmed back in the dashboard.

## Topics and settings

Topics are defined in `config/topics.yaml`:

- Cybersecurity (the default topic)
- AI Governance (`governance_risk`)
- AI Announcements (`ai_announcements`)

Current settings in the same file:

- Two drafts per topic per run.
- A 30-day research window. Articles without a readable publication date are dropped.
- Minimum audience-fit score of 4 for the research analyst's topics.
- Post length between 700 and 1,300 characters, with a banned-phrase list.
- Up to three revisions per draft for the editorial and claims gates.

Sources are listed in `config/sources.yaml`. Some official feeds are marked unverified in that file; check them before relying on them.

## How a run works

1. **Collect.** The collector fetches the configured feeds and pages and stores URLs and short evidence excerpts under `sources/`. Only pages it actually fetched can be cited later.
2. **Screen.** Shared selection rules (`app/selection.py`) apply to both run paths:
   - A date gate drops articles that have no readable date or are older than the research window.
   - A research analyst makes one structured evidence-brief call per topic, using `config/research_prompt.txt`. Only topics that pass the audience-fit, why-now and decision-relevance checks, and that cite fetched evidence, can select articles.
   - Articles are ranked by the analyst's topic rank, so the strongest article is drafted first.
3. **Write.** The writer produces a post with a consequence-first hook on line 1, a stance, the evidence, one action with an owner, one limitation, and a closing question to a named role.
4. **Check.** Gates run for credibility, faithfulness (minimum score 0.90), editorial quality, claims (every number must appear in the source), hook and stance. Drafts that fail a revisable gate are revised; the rest are rejected with reasons.
5. **Review.** A person approves, edits or discards each draft in the dashboard. Approval generates a 1080 × 1350 infographic, runs a visual QA check, and writes `ready_to_post/<draft_id>/` with the post text, first comment and image.
6. **Publish by hand, then confirm.** After posting on LinkedIn, confirm the publication in the dashboard. This creates a PDF snapshot in `published_pdfs/` and an audit record.

If a run produces fewer drafts than requested, the run report lists the reason for each dropped article (for example, "older than the 30-day research window").

## Setup

1. Create a virtual environment: `python -m venv .venv`
2. Activate it and install: `pip install -e ".[dev,adk,observability]"`
3. Copy `.env.example` to `.env` and add credentials locally. Never commit `.env`.
   - `OPENAI_API_KEY` is required for live research and generation.
   - `OPENAI_MODEL` selects the model. Check the value in your `.env`; the code default is `gpt-6-luna`, which has not been verified against the OpenAI API.
4. Review `config/topics.yaml` and `config/sources.yaml`.

## Running the project

The scripts in the project folder are the normal way to run it on Windows:

| Script | What it does |
| --- | --- |
| `.\start_all.ps1` | Starts `adk web` on port 8080 and the dashboard on port 8000, then runs the service check. |
| `.\stop_all.ps1` | Stops both servers and turns LangSmith tracing off. |
| `.\enable_langsmith.ps1` | Turns LangSmith tracing on. It prompts for the API key, which is typed hidden and never written to a file. Open a new terminal afterwards. |
| `.\reset_clean_slate.ps1` | Deletes all agent-generated data (drafts, posts, infographics, PDFs, sources, the database and the audit log) after you type `RESET`. Code, config, tests, `.env` and `.venv` are kept. It refuses to run while the app is up. |

Command-line equivalents:

- `python -m app.cli run-once`: run the live pipeline once.
- `python -m app.cli dashboard`: start the review dashboard on port 8000.
- `python -m app.cli brown-test`: run offline readiness checks.
- `python -m app.cli snapshot-replay`: save the latest successful live run for replay.
- `python -m app.cli clear-drafts`: remove drafts only.
- `python check_services.py`: check that adk web, the dashboard and LangSmith respond.
- `python check_langsmith.py`: check that LangSmith tracing is configured.

## Run with ADK Web

`start_all.ps1` starts ADK Web on port 8080. To run it by hand:

```powershell
adk web --port 8080
```

The ADK agent is `root_agent`, defined in `app/agent.py`. It can show the configuration and run the editorial workflow. Individual approval, discard and publication actions happen in the dashboard.

## Dashboard

The dashboard is at `http://127.0.0.1:8000`. It is designed for Google OAuth with a single allowed account. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_GOOGLE_EMAILS` and `REQUIRE_GOOGLE_AUTH=true` in `.env` to enable it. If OAuth is not set, local development allows access without sign-in. GitHub users need their own Google OAuth application.

The dashboard shows whether a pipeline run is in progress, and its "Show discarded" toggle stays disabled until at least one draft has been discarded.

## LinkedIn publishing

LinkedIn API credentials are not needed. Publishing is manual: approve a draft, post it yourself on LinkedIn, then confirm completion in the dashboard. The project does not automate LinkedIn through a browser.

## Tracing

When `LANGSMITH_TRACING` is on, traces are sent to LangSmith for ADK and OpenAI generation. Tracing is off by default and `stop_all.ps1` turns it off again.

## Known gaps

- The date gate drops undated articles, which can reduce the number of drafts.
- A failed infographic does not yet block approval.
- The metrics-recording form is not in the dashboard UI yet; the backend endpoint exists.
- Some sources are unverified, and the selection and writer changes have not been run end-to-end against OpenAI in this environment.
- There is no scheduled weekly run. Runs are started manually.

# Demo and brown-test runbook

Project folder: `C:\Downloads\linked_in_generator_adk`

## Before the session

1. Confirm `.env` contains `OPENAI_API_KEY`, `OPENAI_MODEL` (verify the value) and a non-default `APP_SESSION_SECRET`.
2. Turn LangSmith tracing on yourself, because the key is entered hidden:

   ```powershell
   cd C:\Downloads\linked_in_generator_adk
   .\enable_langsmith.ps1
   ```

   Open a new terminal afterwards.
3. If testing Google login, set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_GOOGLE_EMAILS` and `REQUIRE_GOOGLE_AUTH=true`.
4. Run the offline checks and the selection tests:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   python -m app.cli brown-test
   python -m pytest tests/test_selection.py -q
   ```

5. Start everything and confirm the service check passes:

   ```powershell
   .\start_all.ps1
   ```

   The check should show HTTP 200 for adk web and the dashboard, and LangSmith tracing on.
6. Run the pipeline once from the dashboard, about 20–30 minutes before the session. A live run calls OpenAI and takes a few minutes. Reset clears all drafts, so this run is what provides drafts for the demo.

## Demo sequence

Servers started by `start_all.ps1`:

- ADK Web: `http://127.0.0.1:8080`. Show the configuration, then the editorial workflow.
- Dashboard: `http://127.0.0.1:8000`.

In the dashboard:

1. Run the pipeline and point out the running indicator. Open the run report and show the rejections with reasons, such as articles older than the 30-day window.
2. Review each draft: the hook on line 1, the stance, and the evidence. Edit a field to show that title, content and first comment are editable.
3. Approve one draft. This generates the infographic and writes `ready_to_post/<draft_id>/` with the post text, first comment and image.
4. Discard another draft. "Show discarded" is greyed out until something is discarded.
5. Post one approved draft on LinkedIn by hand, then confirm it in the dashboard. This creates a PDF in `published_pdfs/` and a row in `reports/editorial_audit.csv`.

If live research fails during the session, ask ADK Web for a replay of the saved run. Create that snapshot once after a successful live run with:

```powershell
python -m app.cli snapshot-replay
```

Inside ADK Web, the reviewer can also ask the coordinator to approve a named draft ID, which runs the same approval steps as the dashboard.

## After the session

```powershell
.\stop_all.ps1
```

This stops both servers and turns LangSmith tracing off. To clear generated data before the next session, use `.\reset_clean_slate.ps1` and type `RESET` when prompted. The script refuses to run while the servers are up, so stop them first.

## Acceptance checks

- No draft reaches manual publishing without explicit approval.
- Discarded drafts cannot be confirmed as published.
- Unsupported evidence is rejected by the faithfulness gate.
- Articles without a readable date, or older than the research window, never become drafts. The run report shows the reason.
- Articles cited in the research brief must have been fetched by the collector.
- The post text contains no template labels, and every number in it appears in the source (`CLAIMS` gate).
- Approved drafts generate PNG infographics under `infographics/` with a visual QA result.
- Approved drafts write `ready_to_post/<draft_id>/` with the post, first comment and image.
- Infographic path, status and QA result are recorded in the audit CSV.
- Confirmed publications create PDFs under `published_pdfs/`.
- LangSmith shows the ADK and OpenAI generation traces when tracing is on.

## Known gaps to mention during the demo

- A failed infographic does not yet block approval.
- The metrics-recording form is not in the dashboard yet.
- The newest selection and writer changes have been checked by unit tests and a stubbed run only. The first live run is a test.
- Some official sources are unverified in `config/sources.yaml`.

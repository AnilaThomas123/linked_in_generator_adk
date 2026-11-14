"""Check that adk web and the dashboard are running, and that LangSmith records a run
for a message sent through adk web.

Usage (PowerShell, from the project folder, with the venv active):
    python check_services.py
"""
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ADK_URL = os.getenv("ADK_URL", "http://127.0.0.1:8080/")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://127.0.0.1:8000/")
PROJECT = os.getenv("LANGSMITH_PROJECT") or "linked-in-generator"


def http_status(url, timeout=5):
    """Return the HTTP status code, or None if nothing answers.
    An HTTP error code still means a server is running there."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def report(label, ok, detail):
    print(f"[{'OK' if ok else 'FAIL'}] {label}: {detail}")
    return ok


def check_web_servers():
    results = []
    status = http_status(ADK_URL)
    results.append(report("adk web", status is not None,
                          f"responded with HTTP {status}" if status else
                          f"nothing at {ADK_URL}. Start it with: adk web --port 8080"))
    status = http_status(DASHBOARD_URL)
    results.append(report("dashboard", status is not None,
                          f"responded with HTTP {status}" if status else
                          f"nothing at {DASHBOARD_URL}. Start it with: python -m app.cli dashboard"))
    return all(results)


def langsmith_ready():
    tracing = (os.getenv("LANGSMITH_TRACING") or os.getenv("LANGCHAIN_TRACING_V2") or "").lower()
    if tracing != "true":
        return report("LangSmith tracing", False,
                      "not on. Run enable_langsmith.ps1 and open a new terminal")
    if not (os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")):
        return report("LangSmith key", False, "no API key set")
    try:
        from langsmith import Client
    except ImportError:
        return report("langsmith package", False, "not installed. Run: pip install langsmith")
    try:
        client = Client()
        list(client.list_runs(project_name=PROJECT, limit=1))
        return report("LangSmith connection", True, f"can read project '{PROJECT}'")
    except Exception as exc:
        return report("LangSmith connection", False, str(exc))


def latest_run_since(client, started_at):
    """Return the newest run in the project that began after started_at, or None."""
    for run in client.list_runs(project_name=PROJECT, limit=20):
        start = run.start_time
        if start is None:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start >= started_at:
            return run
    return None


def check_flow_trace():
    from langsmith import Client

    client = Client()
    started_at = datetime.now(timezone.utc)
    print()
    print("Now open adk web, send one message to the agent, wait for the reply,")
    input("then press Enter here to check LangSmith... ")

    run = None
    for _ in range(6):  # traces can take a few seconds to appear
        run = latest_run_since(client, started_at)
        if run:
            break
        import time
        time.sleep(5)

    if run is None:
        return report("agent flow trace", False,
                      f"no new run in '{PROJECT}' since this check started")
    return report("agent flow trace", True,
                  f"found run '{run.name}' started {run.start_time}")


def main():
    servers_ok = check_web_servers()
    langsmith_ok = langsmith_ready()

    flow_ok = False
    if langsmith_ok:
        flow_ok = check_flow_trace()

    print()
    if servers_ok and langsmith_ok and flow_ok:
        print("All checks passed. The agent flow is being traced.")
        sys.exit(0)
    print("Some checks failed. See the [FAIL] lines above.")
    sys.exit(1)


if __name__ == "__main__":
    main()

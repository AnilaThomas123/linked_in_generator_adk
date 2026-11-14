"""Check that LangSmith tracing is configured and can send a trace."""
import os
import sys
import uuid

try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env if the project uses one
except ImportError:
    pass

TRACING_VARS = ["LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"]
KEY_VARS = ["LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"]


def first_set(names):
    for name in names:
        value = os.getenv(name)
        if value:
            return name, value
    return None, None


def main():
    ok = True

    flag_name, flag_value = first_set(TRACING_VARS)
    if flag_value and flag_value.lower() == "true":
        print(f"[OK]   Tracing enabled via {flag_name}")
    else:
        print("[FAIL] Tracing is not enabled. Set LANGSMITH_TRACING=true")
        ok = False

    key_name, key_value = first_set(KEY_VARS)
    if key_value:
        print(f"[OK]   API key found in {key_name} (value hidden)")
    else:
        print("[FAIL] No API key. Set LANGSMITH_API_KEY")
        ok = False

    project = os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or "default"
    print(f"[INFO] Project: {project}")

    if not ok:
        sys.exit(1)

    try:
        from langsmith import Client, traceable
    except ImportError:
        print("[FAIL] langsmith is not installed. Run: pip install langsmith")
        sys.exit(1)

    client = Client()
    try:
        list(client.list_projects(limit=1))  # confirms the key and endpoint work
        print("[OK]   LangSmith API connection works")
    except Exception as exc:
        print(f"[FAIL] Could not reach LangSmith with this key: {exc}")
        sys.exit(1)

    marker = f"config-check-{uuid.uuid4().hex[:8]}"

    @traceable(name="config_check", project_name=project)
    def ping(text):
        return f"ok: {text}"

    ping(marker)
    print(f"[OK]   Sent test trace 'config_check' with input {marker}")
    print(f"       Wait 10-30 seconds, open LangSmith, choose project '{project}',")
    print(f"       and search for '{marker}'. If it appears, tracing works.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
LOG_PATH = RUNTIME / "crypto-bot.log"

SECRET_KEYS = {
    "CDC_API_KEY",
    "CDC_API_SECRET",
    "CRYPTO_COM_API_KEY",
    "CRYPTO_COM_API_SECRET",
    "GROQ_API_KEY",
    "RUNNER_TOKEN",
    "CLOUDFLARE_RUNNER_TOKEN",
}


def api_url() -> str:
    value = os.getenv("CLOUDFLARE_API_URL", "").rstrip("/")
    if not value:
        raise SystemExit("Falta CLOUDFLARE_API_URL")
    return value


def runner_token() -> str:
    value = os.getenv("RUNNER_TOKEN") or os.getenv("CLOUDFLARE_RUNNER_TOKEN")
    if not value:
        raise SystemExit("Falta RUNNER_TOKEN")
    return value


def request_json(method: str, path: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url()}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "User-Agent": os.getenv("RUNNER_USER_AGENT", "curl/8.14.1"),
            "Authorization": f"Bearer {runner_token()}",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Cloudflare API {exc.code}: {detail}") from exc


def redact(value):
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if key in SECRET_KEYS or "secret" in key.lower() or "api_key" in key.lower() else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def read_log_events(since: float, limit: int = 120):
    if not LOG_PATH.exists():
        return []
    events = []
    for line in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        try:
            ts_text = str(item.get("ts", ""))
            event_time = time.mktime(time.strptime(ts_text[:19], "%Y-%m-%dT%H:%M:%S"))
            if event_time + 2 < since:
                continue
        except Exception:
            pass
        events.append(redact(item))
    return events[-limit:]


def seed_local_feedback(limit: int = 300):
    try:
        data = request_json("GET", f"/api/v1/logs?limit={limit}")
    except SystemExit:
        raise
    except Exception:
        return
    rows = data.get("logs", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", encoding="utf-8") as handle:
        for row in reversed(rows):
            if not isinstance(row, dict) or not row.get("event"):
                continue
            item = {"ts": row.get("ts"), "event": row.get("event"), "data": row.get("data", {})}
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def apply_runtime_env(next_job):
    config = next_job.get("config") if isinstance(next_job, dict) else {}
    if not isinstance(config, dict):
        config = {}
    os.environ.update(
        {
            "AUTO_EXECUTE": "YES",
            "AUTO_LIVE": "YES",
            "CONFIRM_LIVE": "YES",
            "SUPERVISOR_EXECUTE": "NO",
            "AI_PROVIDER": "groq",
            "AI_FALLBACK_PROVIDERS": "technical",
            "GROQ_MODEL": str(next_job.get("ai", {}).get("model") or os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b"),
            "REQUIRE_AI_FOR_EXECUTION": "YES",
            "UNIVERSE_AUTOPILOT_APPLY": "YES",
        }
    )
    for key, value in config.items():
        if key in SECRET_KEYS:
            continue
        os.environ[str(key)] = str(value)


def run_cycle(timeout: int):
    started = time.time()
    proc = subprocess.run(
        [sys.executable, "crypto_auto.py"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout[-8000:], time.time() - started


def run_supervisor(timeout: int, cycle: int | None):
    every = int(os.getenv("SUPERVISOR_EVERY_CYCLES", "1"))
    if every <= 0 or (cycle and cycle % every != 0):
        return {"skipped": True, "reason": "interval"}
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "crypto_supervisor.py", "--once"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return {"skipped": False, "returncode": proc.returncode, "duration_seconds": round(time.time() - started, 2), "stdout_tail": proc.stdout[-2000:]}
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else ""
        return {"skipped": False, "returncode": 124, "duration_seconds": timeout, "stdout_tail": output}


def main():
    parser = argparse.ArgumentParser(description="Cloudflare-controlled crypto-bot runner")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("RUNNER_CYCLE_TIMEOUT_SECONDS", "420")))
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    next_job = request_json("GET", "/api/v1/runner/next")
    if next_job.get("desired_state") != "running":
        request_json("POST", "/api/v1/runner/report", {"status": "paused", "cycle": next_job.get("cycle"), "summary": {"reason": "desired_state_not_running"}})
        print("Runner pausado por Cloudflare")
        return 0
    if not os.getenv("GROQ_API_KEY"):
        raise SystemExit("Falta GROQ_API_KEY en el runner")
    if not (os.getenv("CDC_API_KEY") or os.getenv("CRYPTO_COM_API_KEY")):
        raise SystemExit("Falta CDC_API_KEY/CRYPTO_COM_API_KEY en el runner")
    if not (os.getenv("CDC_API_SECRET") or os.getenv("CRYPTO_COM_API_SECRET")):
        raise SystemExit("Falta CDC_API_SECRET/CRYPTO_COM_API_SECRET en el runner")
    apply_runtime_env(next_job)
    seed_local_feedback()
    since = time.time()
    try:
        returncode, output, duration = run_cycle(args.timeout)
        status = "ok" if returncode == 0 else "error"
    except subprocess.TimeoutExpired as exc:
        returncode, duration, status = 124, args.timeout, "timeout"
        output = (exc.stdout or "")[-8000:] if isinstance(exc.stdout, str) else ""
    supervisor = run_supervisor(int(os.getenv("RUNNER_SUPERVISOR_TIMEOUT_SECONDS", "240")), next_job.get("cycle"))
    report = {
        "status": status,
        "cycle": next_job.get("cycle"),
        "returncode": returncode,
        "duration_seconds": round(duration, 2),
        "summary": {"stdout_tail": output[-2000:], "supervisor": supervisor},
        "logs": read_log_events(since),
    }
    request_json("POST", "/api/v1/runner/report", report)
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return 0 if returncode == 0 else returncode


if __name__ == "__main__":
    raise SystemExit(main())

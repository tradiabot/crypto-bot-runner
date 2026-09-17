#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
LOG_PATH = RUNTIME / "crypto-bot.log"
COST_BASIS_PATH = RUNTIME / "cost_basis.json"

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
            ts_text = str(item.get("ts", "")).strip()
            # Local events use ISO 8601 while D1 uses ``YYYY-MM-DD HH:MM:SS``.
            # Treat naive values as UTC: GitHub runners are ephemeral and seeded
            # history must never be uploaded again as activity from this cycle.
            parsed = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            event_time = parsed.timestamp()
        except (TypeError, ValueError):
            # An event without a trustworthy timestamp cannot safely be
            # attributed to the current run.
            continue
        if event_time + 2 < since:
            continue
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
    seen_trade_ids = set()
    with LOG_PATH.open("w", encoding="utf-8") as handle:
        for row in reversed(rows):
            if not isinstance(row, dict) or not row.get("event"):
                continue
            data = row.get("data", {}) if isinstance(row.get("data"), dict) else {}
            if row.get("event") == "trade_executed":
                result = data.get("result", {}) if isinstance(data.get("result"), dict) else {}
                trade_id = result.get("id")
                if trade_id is not None:
                    trade_key = str(trade_id)
                    if trade_key in seen_trade_ids:
                        continue
                    seen_trade_ids.add(trade_key)
            item = {"ts": row.get("ts"), "event": row.get("event"), "data": data}
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
            "AI_FALLBACK_PROVIDERS": "",
            "GROQ_MODEL": str(next_job.get("ai", {}).get("model") or os.getenv("GROQ_MODEL") or "openai/gpt-oss-20b"),
            "REQUIRE_AI_FOR_EXECUTION": "YES",
            "GROQ_REASONING_EFFORT": "low",
            "GROQ_TIMEOUT_SECONDS": "45",
            "GROQ_MAX_COMPLETION_TOKENS": "350",
            "AI_PREFILTER_CANDIDATES": "2",
            "AI_CHUNK_SIZE": "1",
            "TRADING_SWARM_MAX_CHUNK": "1",
            "UNIVERSE_AUTOPILOT_APPLY": "YES",
        }
    )
    for key, value in config.items():
        if key in SECRET_KEYS:
            continue
        os.environ[str(key)] = str(value)


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def hydrate_cost_basis(next_job):
    """Restore accounting state sent by the Cloudflare control plane."""
    config = next_job.get("config") if isinstance(next_job, dict) else {}
    raw = config.get("COST_BASIS_STATE_JSON", "") if isinstance(config, dict) else ""
    if not raw:
        return False
    try:
        state = json.loads(raw)
        if not isinstance(state, dict) or not isinstance(state.get("positions", {}), dict):
            return False
        _write_json_atomic(COST_BASIS_PATH, state)
        return True
    except (TypeError, ValueError, OSError):
        return False


def sync_cost_basis():
    """Import recent Crypto.com history and persist a safe accounting snapshot."""
    if os.getenv("COST_BASIS_SYNC", "YES").upper() != "YES":
        return
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "sync_cost_basis.py"], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=70, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stdout[-500:] or f"exit_{proc.returncode}")
        state = json.loads(COST_BASIS_PATH.read_text(encoding="utf-8")) if COST_BASIS_PATH.exists() else {}
        positions = state.get("positions", {}) if isinstance(state, dict) else {}
        from bot_logger import log_event
        log_event("cost_basis_synced", state={"positions": positions, "updated_at": state.get("updated_at", time.time())}, duration_seconds=round(time.time() - started, 2))
    except Exception as exc:
        from bot_logger import log_event
        log_event("cost_basis_sync_error", error=str(exc)[:500], duration_seconds=round(time.time() - started, 2))


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
    hydrate_cost_basis(next_job)
    sync_cost_basis()
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

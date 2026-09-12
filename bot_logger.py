#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.join(ROOT, ".runtime")
LOG_PATH = os.path.join(RUNTIME_DIR, "crypto-bot.log")
MAX_BYTES = int(os.getenv("BOT_LOG_MAX_BYTES", "1048576"))

SECRET_KEYS = {"api_key", "secret", "keysecret", "signature", "token", "access_id"}


def _redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in SECRET_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _rotate_if_needed():
    if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) >= MAX_BYTES:
        rotated = LOG_PATH + ".1"
        if os.path.exists(rotated):
            os.replace(rotated, rotated + ".old")
        os.replace(LOG_PATH, rotated)


def _ensure_runtime_dir():
    try:
        if os.path.isdir(RUNTIME_DIR):
            return
        os.makedirs(RUNTIME_DIR, exist_ok=True)
    except OSError:
        return


def log_event(event, **data):
    try:
        _ensure_runtime_dir()
        _rotate_if_needed()
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "epoch": time.time(),
            "event": event,
            "data": _redact(data),
        }
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        return False
    return True


def read_events(limit=200):
    try:
        if not os.path.exists(LOG_PATH):
            return []
        with open(LOG_PATH, encoding="utf-8") as f:
            lines=f.readlines()[-limit:]
    except OSError:
        return []
    events=[]
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def feedback_summary(limit=200):
    events=read_events(limit)
    counts={}
    last_decision=None
    last_trade=None
    last_block=None
    for event in events:
        name=event.get("event","unknown")
        counts[name]=counts.get(name,0)+1
        if name in {"decision","decision_hold"}:
            last_decision=event
        elif name == "trade_executed":
            last_trade=event
        elif name.endswith("block") or name in {"scan_timeout","scanner_unavailable","execution_skipped"}:
            last_block=event
    return {"log_path":LOG_PATH,"events_reviewed":len(events),"counts":counts,"last_decision":last_decision,"last_trade":last_trade,"last_block":last_block}


def print_feedback(limit=200):
    print(json.dumps(feedback_summary(limit), indent=2, ensure_ascii=False))

#!/usr/bin/env python3
"""Universe autopilot for the crypto bot.

This module proposes and optionally writes TRADE_INCLUDE / TRADE_EXCLUDE
based on market opportunities and local portfolio context.
"""
import json
import os
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")


def _split_csv(value):
    return [part.strip().upper() for part in (value or "").split(",") if part.strip()]


def _join_csv(values):
    return ",".join(values)


def _read_env():
    values = {}
    order = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key] = value
                    order.append(key)
    return values, order


def _write_env(values, order):
    fd, temp = tempfile.mkstemp(prefix=".env.", dir=ROOT, text=True)
    try:
        seen = set()
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for key in order:
                if key in values and key not in seen:
                    f.write(f"{key}={values[key]}\n")
                    seen.add(key)
            for key, value in values.items():
                if key not in seen:
                    f.write(f"{key}={value}\n")
        os.chmod(temp, 0o600)
        os.replace(temp, ENV_PATH)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _env_flag(name, default="NO"):
    return os.getenv(name, default).upper() == "YES"


def _float_env(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _score_item(item, opp=None, held_amount=0.0):
    try:
        d24 = float(item.get("change_24h", 0) or 0)
    except (TypeError, ValueError):
        d24 = 0.0
    try:
        w1 = float(item.get("change_1w", 0) or 0)
    except (TypeError, ValueError):
        w1 = 0.0
    try:
        rank = float(item.get("rank", 999) or 999)
    except (TypeError, ValueError):
        rank = 999.0
    try:
        conf = float((opp or {}).get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    action = str((opp or {}).get("action", "HOLD")).upper()
    liquidity = max(0.0, (100.0 - rank) / 100.0)
    score = (w1 * 0.60) + (d24 * 0.35) + liquidity
    if action == "BUY":
        score += conf * 2.0
    elif action == "SELL":
        score -= conf * 1.0
    if held_amount > 0:
        score += 0.35
    return score


def recommend(report, perf=None):
    perf = perf or {}
    universe = [x for x in report.get("universe", []) if isinstance(x, dict)]
    ai = report.get("ai", {}) if isinstance(report.get("ai"), dict) else {}
    opps = {str(x.get("symbol", "")).upper(): x for x in ai.get("opportunities", []) if isinstance(x, dict)}
    positions = perf.get("positions", {}) if isinstance(perf, dict) else {}

    current_include = _split_csv(os.getenv("TRADE_INCLUDE", ""))
    current_exclude = _split_csv(os.getenv("TRADE_EXCLUDE", ""))
    pinned = _split_csv(os.getenv("UNIVERSE_AUTOPILOT_CORE", "BTC,ETH"))
    keep_current_holdings = _env_flag("UNIVERSE_AUTOPILOT_KEEP_HOLDINGS", "YES")
    max_include = int(os.getenv("UNIVERSE_AUTOPILOT_MAX_INCLUDE", "4") or 4)
    min_score = _float_env("UNIVERSE_AUTOPILOT_MIN_SCORE", "0.05")
    min_buy_conf = _float_env("UNIVERSE_AUTOPILOT_MIN_BUY_CONFIDENCE", "0.60")
    exclude_floor = _float_env("UNIVERSE_AUTOPILOT_EXCLUDE_FLOOR", "-0.25")

    scored = []
    held_symbols = set()
    for sym, pdata in positions.items():
        try:
            if float((pdata or {}).get("amount", 0) or 0) > 0:
                held_symbols.add(str(sym).upper())
        except (TypeError, ValueError):
            continue

    for item in universe:
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol or symbol in {"USDC", "USDT"}:
            continue
        held_amount = 0.0
        if symbol in positions:
            try:
                held_amount = float((positions.get(symbol) or {}).get("amount", 0) or 0)
            except (TypeError, ValueError):
                held_amount = 0.0
        opp = opps.get(symbol)
        score = _score_item(item, opp=opp, held_amount=held_amount)
        scored.append((score, symbol, item, opp, held_amount))

    scored.sort(key=lambda row: row[0], reverse=True)

    recommended_include = []
    include_reasons = {}
    for score, symbol, item, opp, held_amount in scored:
        if symbol in pinned:
            continue
        if keep_current_holdings and symbol in held_symbols:
            continue
        action = str((opp or {}).get("action", "HOLD")).upper()
        confidence = float((opp or {}).get("confidence", 0.0) or 0.0) if isinstance(opp, dict) else 0.0
        if action == "BUY" and confidence >= min_buy_conf and score >= min_score:
            recommended_include.append(symbol)
            include_reasons[symbol] = {
                "score": round(score, 4),
                "action": action,
                "confidence": round(confidence, 4),
            }
        if len(recommended_include) >= max_include:
            break

    recommended_exclude = []
    exclude_reasons = {}
    for score, symbol, item, opp, held_amount in reversed(scored):
        if symbol in pinned:
            continue
        if keep_current_holdings and symbol in held_symbols:
            continue
        if symbol in recommended_include:
            continue
        if symbol in current_include and score <= exclude_floor:
            recommended_exclude.append(symbol)
            exclude_reasons[symbol] = {
                "score": round(score, 4),
                "reason": "low_score_current_include",
            }

    # Preserve ordering but remove duplicates and excluded symbols.
    include_final = []
    for symbol in pinned + current_include + recommended_include:
        if symbol and symbol not in recommended_exclude and symbol not in include_final:
            include_final.append(symbol)
    # We do not auto-drop pinned or currently-held assets from the base list.
    # Exclusion is limited to explicit extras.
    exclude_final = []
    for symbol in current_exclude + recommended_exclude:
        if symbol and symbol not in exclude_final and symbol not in pinned and symbol not in held_symbols:
            exclude_final.append(symbol)

    changed = include_final != current_include or exclude_final != current_exclude
    return {
        "enabled": _env_flag("UNIVERSE_AUTOPILOT_ENABLED", "NO"),
        "apply": _env_flag("UNIVERSE_AUTOPILOT_APPLY", "NO"),
        "changed": changed,
        "current_include": current_include,
        "current_exclude": current_exclude,
        "recommended_include": recommended_include,
        "recommended_exclude": recommended_exclude,
        "include_final": include_final,
        "exclude_final": exclude_final,
        "include_reasons": include_reasons,
        "exclude_reasons": exclude_reasons,
        "pinned": pinned,
    }


def apply_recommendation(recommendation):
    if not recommendation or not recommendation.get("enabled"):
        return {"applied": False, "reason": "disabled"}
    if not recommendation.get("apply"):
        return {"applied": False, "reason": "apply_flag_disabled"}
    values, order = _read_env()
    values["TRADE_INCLUDE"] = _join_csv(recommendation.get("include_final", []))
    values["TRADE_EXCLUDE"] = _join_csv(recommendation.get("exclude_final", []))
    if "TRADE_INCLUDE" not in order:
        order.append("TRADE_INCLUDE")
    if "TRADE_EXCLUDE" not in order:
        order.append("TRADE_EXCLUDE")
    _write_env(values, order)
    return {"applied": True, "trade_include": values["TRADE_INCLUDE"], "trade_exclude": values["TRADE_EXCLUDE"]}


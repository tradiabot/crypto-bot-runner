#!/usr/bin/env python3
"""Safe Crypto.com execution bridge.

Uses the installed Crypto.com skill scripts for every API call. USDC is the
only source currency and execution requires an explicit confirmation.
"""
import json
import os
import re
import subprocess
import sys
import time
from decimal import Decimal, ROUND_DOWN

ROOT = os.path.dirname(__file__)
LOCAL_SKILL = os.path.join(ROOT, "crypto_com_skill", "scripts")
SKILL = os.getenv("CRYPTO_COM_SKILL_SCRIPTS", LOCAL_SKILL if os.path.isdir(LOCAL_SKILL) else "/root/.agents/skills/crypto-com-app/scripts")
SOURCE = "USDC"
MAX_USDC = 2.0
RUNTIME_DIR = os.path.join(ROOT, ".runtime")
LOT_STEP_OVERRIDES_PATH = os.path.join(RUNTIME_DIR, "lot_step_overrides.json")
LOT_STEP_CACHE = {"ts": 0.0, "data": {}}

def max_usdc_limit():
    try:
        return float(os.getenv("CRYPTO_COM_MAX_TRADE_USDC", os.getenv("MAX_TRADE_USDC", str(MAX_USDC))))
    except (TypeError, ValueError):
        return MAX_USDC
_BALANCE_CACHE = {"ts": 0.0, "data": None}


def env():
    key = os.getenv("CDC_API_KEY") or os.getenv("CRYPTO_COM_API_KEY")
    secret = os.getenv("CDC_API_SECRET") or os.getenv("CRYPTO_COM_API_SECRET")
    if not key or not secret:
        raise SystemExit("Faltan credenciales locales de Crypto.com")
    value = os.environ.copy()
    value["CDC_API_KEY"] = key
    value["CDC_API_SECRET"] = secret
    return value


def run(script, *args):
    timeout = float(os.getenv("CDC_API_TIMEOUT_SECONDS", "45"))
    try:
        result = subprocess.run(
            ["npx", "tsx", f"{SKILL}/{script}", *args],
            env=env(), text=True, capture_output=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(f"Crypto.com timeout tras {timeout:.0f}s en {script}")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Respuesta invalida de Crypto.com: {exc}")
    if not data.get("ok"):
        raise SystemExit(data.get("error_message", data.get("error", "API error")))
    return data["data"]


def balances(force=False):
    ttl = float(os.getenv("CDC_BALANCE_CACHE_SECONDS", "20"))
    now = time.time()
    if not force and _BALANCE_CACHE["data"] is not None and now - _BALANCE_CACHE["ts"] <= ttl:
        return _BALANCE_CACHE["data"]
    data = run("account.ts", "balances", "crypto")
    _BALANCE_CACHE["ts"] = now
    _BALANCE_CACHE["data"] = data
    return data


def _load_lot_step_overrides():
    now = time.time()
    ttl = float(os.getenv("LOT_STEP_OVERRIDE_CACHE_SECONDS", "30"))
    if LOT_STEP_CACHE["data"] and now - LOT_STEP_CACHE["ts"] <= ttl:
        return LOT_STEP_CACHE["data"]
    try:
        with open(LOT_STEP_OVERRIDES_PATH, encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    LOT_STEP_CACHE["ts"] = now
    LOT_STEP_CACHE["data"] = data
    return data


def _save_lot_step_override(currency, step):
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    data = _load_lot_step_overrides()
    data[currency.upper()] = str(step)
    tmp = LOT_STEP_OVERRIDES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, LOT_STEP_OVERRIDES_PATH)
    LOT_STEP_CACHE["ts"] = time.time()
    LOT_STEP_CACHE["data"] = data
    os.environ[f"{currency.upper()}_LOT_STEP"] = str(step)


def _parse_lot_step_error(message):
    text = str(message or "")
    match = re.search(r"Amount of ([A-Z0-9]+) needs to be multiple of ([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"([A-Z0-9]+) needs to be multiple of ([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if not match:
        return None, None
    currency = match.group(1).upper()
    try:
        step = Decimal(match.group(2))
    except Exception:
        return None, None
    if step <= 0:
        return None, None
    return currency, step


def _parse_min_order_error(message):
    text = str(message or "")
    patterns = [
        r"minimum order size(?: is|:)?\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Z0-9]+)",
        r"below minimum order size(?: is|:)?\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Z0-9]+)",
        r"order size(?: is|:)?\s*below minimum(?:\s*([0-9]+(?:\.[0-9]+)?))?\s*([A-Z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        try:
            if match.lastindex and match.lastindex >= 2 and match.group(1) and match.group(2):
                value = Decimal(match.group(1))
                currency = match.group(2).upper()
            else:
                continue
        except Exception:
            continue
        if value > 0:
            return currency, value
    return None, None


def _repair_quote_payload(payload, error_message):
    from_currency = str(payload.get("from_currency", "")).upper()
    to_currency = str(payload.get("to_currency", "")).upper()
    amount = Decimal(str(payload.get("from_amount", "0") or "0"))
    if amount <= 0:
        return None
    repaired_currency, repaired_step = _parse_lot_step_error(error_message)
    if repaired_currency and repaired_currency == from_currency:
        _save_lot_step_override(repaired_currency, repaired_step)
        rounded = round_amount(from_currency, amount)
        if rounded > 0 and rounded != amount:
            payload = dict(payload)
            payload["from_amount"] = format(rounded, "f")
            return payload
    repaired_currency, repaired_min = _parse_min_order_error(error_message)
    if repaired_currency and repaired_currency in {from_currency, to_currency}:
        rounded = round_amount(from_currency, repaired_min)
        if rounded > 0 and rounded != amount:
            payload = dict(payload)
            payload["from_amount"] = format(rounded, "f")
            return payload
    return None


def available(currency):
    currency = currency.upper()
    data = balances()
    wallets = data.get("crypto", {}).get("wallets", [])
    for wallet in wallets:
        if wallet.get("currency") == currency:
            amount = wallet.get("available", {}).get("amount") or wallet.get("balance", {}).get("amount") or "0"
            native = wallet.get("native_available", {}).get("amount") or wallet.get("native_balance", {}).get("amount") or "0"
            return {"currency": currency, "amount": float(amount), "native_usd": float(native)}
    return {"currency": currency, "amount": 0.0, "native_usd": 0.0}


def round_amount(currency, amount):
    currency = currency.upper()
    default_steps = {
        "BTC": "0.0000001",
        "ETH": "0.000001",
        "CRO": "0.1",
        "SOL": "0.00001",
        "LINK": "0.000001",
        "POL": "0.01",
        "ADA": "0.01",
    }
    default_step = default_steps.get(currency, "0.000001")
    step = Decimal(
        os.getenv(
            f"{currency}_LOT_STEP",
            _load_lot_step_overrides().get(currency, os.getenv("SELL_LOT_STEP", default_step)),
        )
    )
    if step <= 0:
        raise SystemExit(f"{currency}_LOT_STEP debe ser mayor que 0")
    return (Decimal(str(amount)) / step).to_integral_value(rounding=ROUND_DOWN) * step


def quote_exchange(from_currency, to_currency, amount):
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    amount_dec = round_amount(from_currency, amount)
    if amount_dec <= 0:
        raise SystemExit("amount de intercambio debe ser mayor que 0")
    payload = {
        "from_currency": from_currency,
        "to_currency": to_currency,
        "from_amount": format(amount_dec, "f"),
        "side": "buy",
    }
    try:
        return run("trade.ts", "quote", "exchange", json.dumps(payload))
    except SystemExit as exc:
        error_message = str(exc)
        if os.getenv("AUTO_REPAIR_LOT_STEPS", "YES").upper() == "YES":
            repaired_payload = _repair_quote_payload(payload, error_message)
            if repaired_payload and repaired_payload.get("from_amount") != payload["from_amount"]:
                return run("trade.ts", "quote", "exchange", json.dumps(repaired_payload))
        raise


def quote_buy(to_currency="BTC", amount=2.0, from_currency=SOURCE):
    amount = float(amount)
    limit = max_usdc_limit()
    floor = float(os.getenv("MIN_TRADE_USDC", "1.0"))
    if from_currency.upper() == SOURCE and (amount < floor or amount > limit):
        raise SystemExit(f"amount debe estar entre {floor} y {limit} USDC")
    return quote_exchange(from_currency, to_currency, amount)


def quote_sell(from_currency="BTC", amount=0.0):
    return quote_exchange(from_currency, SOURCE, amount)


def quote(to_currency="BTC", amount=2.0):
    return quote_buy(to_currency, amount)


def confirm(quotation_id):
    if os.getenv("CONFIRM_LIVE") != "YES":
        raise SystemExit("Ejecución bloqueada: establece CONFIRM_LIVE=YES explícitamente")
    return run("trade.ts", "confirm", "exchange", quotation_id)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in {"quote", "confirm"}:
        raise SystemExit("Uso: crypto_com_adapter.py quote [BTC] [2] | confirm <quotation_id>")
    if sys.argv[1] == "quote":
        print(json.dumps(quote(sys.argv[2] if len(sys.argv) > 2 else "BTC",
                               sys.argv[3] if len(sys.argv) > 3 else 2), indent=2))
    else:
        print(json.dumps(confirm(sys.argv[2]), indent=2))

#!/usr/bin/env python3
import hashlib, hmac, json, os, time, urllib.parse, urllib.request, uuid

BASE = os.getenv("COINEX_BASE_URL", "https://api.coinex.com/v2")
QUOTE_CURRENCY = os.getenv("COINEX_QUOTE_CURRENCY", "USDT").upper()
MAX_QUOTE = float(os.getenv("MAX_TRADE_USDT", "2"))

def load_env():
    path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))

def credentials():
    load_env()
    key = os.getenv("COINEX_ACCESS_ID")
    secret = os.getenv("COINEX_SECRET_KEY")
    if not key or not secret:
        raise SystemExit("Faltan COINEX_ACCESS_ID y COINEX_SECRET_KEY en .env")
    return key, secret

def request(method, path, params=None, body=None, private=False):
    params = params or {}
    query = urllib.parse.urlencode(sorted(params.items()))
    full_path = path + ("?" + query if query else "")
    payload = "" if body is None else json.dumps(body, separators=(",", ":"))
    headers = {"Content-Type": "application/json"}
    if private:
        key, secret = credentials()
        timestamp = str(int(time.time() * 1000))
        prepared = method.upper() + full_path + payload + timestamp
        sign = hmac.new(secret.encode(), prepared.encode(), hashlib.sha256).hexdigest()
        headers.update({"X-COINEX-KEY": key, "X-COINEX-SIGN": sign, "X-COINEX-TIMESTAMP": timestamp})
    req = urllib.request.Request(BASE + full_path, data=payload.encode() if method.upper() != "GET" else None, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read())
    except Exception as exc:
        raise SystemExit(f"CoinEx API no disponible: {exc}")
    if data.get("code") != 0:
        raise SystemExit(f"CoinEx API error {data.get('code')}: {data.get('message')}")
    return data.get("data")

def balance():
    return request("GET", "/assets/spot/balance", private=True)
def quote(to_currency="BTC", amount=2.0):
    amount = float(amount)
    if amount <= 0 or amount > MAX_QUOTE:
        raise SystemExit(f"amount debe estar entre 0 y {MAX_QUOTE} {QUOTE_CURRENCY}")
    market = f"{to_currency.upper()}{QUOTE_CURRENCY}"
    ticker = request("GET", "/spot/ticker", {"market": market})
    row = ticker[0] if isinstance(ticker, list) else ticker
    price = float(row.get("last", row.get("close", 0)))
    if price <= 0: raise SystemExit("CoinEx no devolvió precio válido")
    return {"id": str(uuid.uuid4()), "market": market, "price": price, "quote_amount": amount, "base_amount": amount / price, "currency": QUOTE_CURRENCY}

def confirm(quotation_id):
    if os.getenv("CONFIRM_LIVE") != "YES":
        raise SystemExit("Ejecución bloqueada: falta confirmación explícita")
    raise SystemExit("La cotización CoinEx debe conservarse en el mismo proceso; use autoejecución desde el menú")

def execute(quotation):
    if os.getenv("CONFIRM_LIVE") != "YES": raise SystemExit("Ejecución bloqueada")
    return request("POST", "/spot/order", body={"market": quotation["market"], "market_type": "SPOT", "side": "buy", "type": "market", "ccy": QUOTE_CURRENCY, "amount": f"{quotation['quote_amount']:.8f}"}, private=True)

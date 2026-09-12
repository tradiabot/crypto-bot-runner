import os
from crypto_com_adapter import quote as cdc_quote, quote_buy as cdc_quote_buy, quote_sell as cdc_quote_sell, quote_exchange as cdc_quote_exchange, confirm as cdc_confirm, available as cdc_available, balances as cdc_balances
from coinex_adapter import quote as cx_quote, execute as cx_execute, balance as cx_balance

def load_local():
    path=os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k,v=line.strip().split("=",1); os.environ.setdefault(k,v.strip().strip('"').strip("'"))

def split_symbols(value):
    return [x.strip().upper() for x in (value or "").split(",") if x.strip()]

def selected():
    load_local()
    return os.getenv("EXCHANGE", "crypto_com").lower()

def balance():
    return cx_balance() if selected() == "coinex" else cdc_balances()

def available(asset="USDC"):
    if selected() == "coinex":
        data = cx_balance() or {}
        return {"currency": asset.upper(), "amount": float(data.get(asset.upper(), 0) or 0), "native_usd": 0.0}
    return cdc_available(asset)

def _amount(value):
    if isinstance(value, dict):
        value=value.get("amount", 0)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def available_balances():
    data=balance() or {}
    normalized={}
    if selected() == "coinex":
        rows=data if isinstance(data,list) else data.get("data",[]) if isinstance(data,dict) else []
        for row in rows:
            if not isinstance(row,dict):
                continue
            symbol=str(row.get("ccy") or row.get("currency") or row.get("asset") or "").upper()
            if not symbol:
                continue
            amount=_amount(row.get("available") or row.get("available_amount") or row.get("balance"))
            normalized[symbol]={"currency":symbol,"amount":amount,"native_usd":0.0}
        return normalized
    wallets=data.get("crypto",{}).get("wallets",[]) if isinstance(data,dict) else []
    for wallet in wallets:
        if not isinstance(wallet,dict):
            continue
        symbol=str(wallet.get("currency") or "").upper()
        if not symbol:
            continue
        amount=_amount(wallet.get("available") or wallet.get("balance"))
        native_usd=_amount(wallet.get("native_available") or wallet.get("native_balance"))
        normalized[symbol]={"currency":symbol,"amount":amount,"native_usd":native_usd}
    return normalized

def universe():
    load_local()
    base=split_symbols(os.getenv("TRADE_UNIVERSE","BTC,ETH,SOL,CRO,LINK,POL"))
    includes=split_symbols(os.getenv("TRADE_INCLUDE",""))
    excludes=set(split_symbols(os.getenv("TRADE_EXCLUDE","")))
    merged=[]
    for sym in base + includes:
        if sym and sym not in excludes and sym not in merged:
            merged.append(sym)
    return merged

def quote(asset="BTC", amount=2.0):
    return cx_quote(asset, amount) if selected() == "coinex" else cdc_quote(asset, amount)

def quote_buy(asset="BTC", amount=2.0, source="USDC"):
    if selected() == "coinex":
        if source.upper() != "USDT": raise SystemExit("CoinEx solo soporta compras con USDT en esta CLI")
        return cx_quote(asset, amount)
    return cdc_quote_buy(asset, amount, source)

def quote_sell(asset="BTC", amount=0.0):
    if selected() == "coinex": raise SystemExit("Venta CoinEx no implementada en esta CLI")
    return cdc_quote_sell(asset, amount)

def quote_exchange(source="USDC", asset="BTC", amount=0.0):
    if selected() == "coinex": raise SystemExit("Exchange generico CoinEx no implementado en esta CLI")
    return cdc_quote_exchange(source, asset, amount)

def confirm(quotation):
    if selected() == "coinex": return cx_execute(quotation)
    return cdc_confirm(quotation["id"] if isinstance(quotation, dict) else quotation)

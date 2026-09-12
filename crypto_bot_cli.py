#!/usr/bin/env python3
import argparse, json, os, subprocess, sys
from exchange_adapter import selected as selected_exchange, balance as selected_balance, quote, confirm

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILL = "/root/.agents/skills/crypto-com-app/scripts"

def redact(value):
    if isinstance(value, dict): return {k:("[REDACTED]" if k.lower() in {"api_key","secret","keysecret"} else redact(v)) for k,v in value.items()}
    if isinstance(value, list): return [redact(v) for v in value]
    return value
def load_env():
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    os.environ["CDC_API_KEY"] = os.getenv("CDC_API_KEY", os.getenv("CRYPTO_COM_API_KEY", ""))
    os.environ["CDC_API_SECRET"] = os.getenv("CDC_API_SECRET", os.getenv("CRYPTO_COM_API_SECRET", ""))
    if os.getenv("EXCHANGE", "crypto_com").lower() != "coinex" and (not os.environ["CDC_API_KEY"] or not os.environ["CDC_API_SECRET"]):
        raise SystemExit("Faltan credenciales locales en .env")

def api(script, *args):
    p = subprocess.run(["npx", "tsx", f"{SKILL}/{script}", *args], text=True, capture_output=True, env=os.environ.copy())
    if p.returncode or not p.stdout.strip():
        raise SystemExit(p.stderr.strip() or "Crypto.com no devolvió respuesta")
    data = json.loads(p.stdout)
    if not data.get("ok"):
        raise SystemExit(data.get("error_message", data.get("error", "API error")))
    return data["data"]

def main():
    load_env()
    parser = argparse.ArgumentParser(prog="crypto-bot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    q=sub.add_parser("quote"); q.add_argument("asset",default="BTC",nargs="?"); q.add_argument("amount",default="2",nargs="?")
    c=sub.add_parser("confirm"); c.add_argument("quotation_id")
    args=parser.parse_args()
    if args.command == "status":
        if selected_exchange() == "coinex":
            rows=selected_balance(); print("BALANCE COINEX · SPOT")
            for w in rows: print(f"{w.get('ccy','?')} · disponible {w.get('available','-')} · congelado {w.get('frozen','-')}")
        else:
            data=api("account.ts", "balances", "all"); print("BALANCE CRYPTO.COM · FUENTE USDC · MÁXIMO POR OPERACIÓN 2 USDC")
            for w in data.get("crypto",{}).get("wallets",[]): print(f"{w.get('currency','?')} · disponible {w.get('available',{}).get('amount','-')} · valor USD {w.get('native_available',{}).get('amount','-')}")
    elif args.command == "quote":
        amount = float(args.amount)
        if amount <= 0 or amount > 2: raise SystemExit("El máximo es 2 USDC por operación")
        data = quote(args.asset.upper(), amount)
        print(json.dumps(data, indent=2))
        print("\nPara ejecutar: CONFIRM_LIVE=YES crypto-bot confirm <id>")
    else:
        if os.getenv("CONFIRM_LIVE") != "YES": raise SystemExit("Bloqueado: usa CONFIRM_LIVE=YES explícitamente")
        print(json.dumps(redact(confirm(args.quotation_id)), indent=2))
    return 0

if __name__ == "__main__": sys.exit(main())

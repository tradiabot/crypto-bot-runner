#!/usr/bin/env python3
import json, os, sys
from exchange_adapter import quote, confirm

def load_env():
    path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def main():
    load_env()
    asset = (sys.argv[1] if len(sys.argv) > 1 else "BTC").upper()
    amount = float(sys.argv[2] if len(sys.argv) > 2 else "2")
    data = quote(asset, amount)
    print("\n╔══════════════════════════════════════════╗")
    print("║          AI BOT · CONFIRM TRADE          ║")
    print("╚══════════════════════════════════════════╝")
    if "from_amount" in data:
        print(f"Fuente: USDC   Importe: {data['from_amount']['amount']} USDC")
        print(f"Recibes: {data['to_amount']['amount']} {data['to_amount']['currency']}")
        print(f"Comisión: {data['fee']['amount']} {data['fee']['currency']}")
        print(f"Válida durante: {data['countdown']} segundos")
    else:
        print(f"Exchange: CoinEx · Mercado: {data['market']}")
        print(f"Importe: {data['quote_amount']} {data['currency']} · Recibes aprox.: {data['base_amount']} {asset}")
        print(f"Precio estimado: {data['price']}")
    answer = input("Escribe CONFIRMAR para ejecutar: ").strip()
    if answer != "CONFIRMAR":
        print("Cancelado. No se ejecutó ninguna orden.")
        return 0
    os.environ["CONFIRM_LIVE"] = "YES"
    result = confirm(data["id"])
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__": main()

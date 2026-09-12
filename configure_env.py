#!/usr/bin/env python3
import getpass, os, tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, ".env")

def read_existing():
    values = {}
    if os.path.exists(PATH):
        for line in open(PATH, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                values[k] = v
    return values

def main():
    values = read_existing()
    exchange = input("Exchange [crypto_com/coinex] (actual se conserva): ").strip().lower()
    if exchange in {"crypto_com", "coinex"}: values["EXCHANGE"] = exchange
    if values.get("EXCHANGE", "crypto_com") == "coinex":
        values["COINEX_ACCESS_ID"] = getpass.getpass("CoinEx access ID: ").strip()
        values["COINEX_SECRET_KEY"] = getpass.getpass("CoinEx secret key: ").strip()
        quote = input("CoinEx moneda base [USDT]: ").strip().upper() or "USDT"
        values["COINEX_QUOTE_CURRENCY"] = quote
    else:
        key = getpass.getpass("Crypto.com API key: ").strip()
        secret = getpass.getpass("Crypto.com API secret: ").strip()
        values["CRYPTO_COM_API_KEY"] = key
        values["CRYPTO_COM_API_SECRET"] = secret
        values["CDC_API_KEY"] = key
        values["CDC_API_SECRET"] = secret
    fd, temp = tempfile.mkstemp(prefix=".env.", dir=ROOT, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for k, v in values.items(): f.write(f"{k}={v}\n")
        os.chmod(temp, 0o600)
        os.replace(temp, PATH)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    print(f"Configuración guardada para {values.get('EXCHANGE', 'crypto_com')}. Claves no mostradas.")

if __name__ == "__main__": main()

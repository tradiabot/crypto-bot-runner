#!/usr/bin/env python3
"""Terminal monitor skeleton for Crypto.com + Ollama/OLMo.

Credentials are read only from environment variables; never print them.
"""
import json
import os
import sys
import time
import urllib.request


def log(level, message):
    print(f"[{time.strftime('%H:%M:%S')}] [{level:<5}] {message}", flush=True)


def ollama_health():
    url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            data = json.loads(response.read())
        names = [m.get("name", "") for m in data.get("models", [])]
        model = os.getenv("OLLAMA_MODEL", "olmo-3:7b-instruct")
        return model in names or any(model.split(":")[0] in n for n in names)
    except Exception:
        return False


def validate_config():
    required = ("CRYPTO_COM_API_KEY", "CRYPTO_COM_API_SECRET")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Faltan credenciales en .env: " + ", ".join(missing))
    if not ollama_health():
        raise RuntimeError("Ollama no está disponible con el modelo configurado")


def main():
    mode = os.getenv("TRADING_MODE", "live").lower()
    print("\033[2J\033[H", end="")
    print("╔════════════════════════════════════════════════════╗")
    print("║              CRYPTO.COM · AI BOT                  ║")
    print("╚════════════════════════════════════════════════════╝")
    log("MODE", mode.upper())
    log("SOURCE", "USDC")
    log("AI", os.getenv("OLLAMA_MODEL", "olmo-3:7b-instruct"))
    try:
        validate_config()
    except RuntimeError as exc:
        log("ERROR", str(exc))
        log("INFO", "No se han enviado órdenes.")
        return 2
    log("OK", "Configuración validada")
    log("INFO", "Adaptador de ejecución pendiente de conectar a la API oficial")
    log("INFO", "Ctrl+C para detener")
    try:
        while True:
            log("SCAN", "Escaneando mercados spot...")
            time.sleep(15)
    except KeyboardInterrupt:
        log("STOP", "Bot detenido por el usuario")
    return 0


if __name__ == "__main__":
    sys.exit(main())


#!/usr/bin/env python3
import json, os, sys, urllib.request
from crypto_com_adapter import quote

def ask(model, prompt):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "think": False, "format": "json",
                       "options": {"temperature": 0, "num_predict": 120}}).encode()
    req = urllib.request.Request(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434") + "/api/generate", data=body, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(json.loads(r.read()).get("response", "{}"))

def main():
    asset = (sys.argv[1] if len(sys.argv) > 1 else "BTC").upper()
    amount = float(sys.argv[2] if len(sys.argv) > 2 else "2")
    q = quote(asset, amount)
    prompt = f'''Eres un analista de trading spot. Devuelve solo JSON válido con action BUY o HOLD, confidence entre 0 y 1 y reason breve. Nunca inventes datos. La orden propuesta debe respetar USDC y máximo 2 USDC. Cotización real recibida: {json.dumps(q, ensure_ascii=False)}'''
    result = ask(os.getenv("OLLAMA_MODEL", "qwen3:0.6b"), prompt)
    print(json.dumps({"asset": asset, "source": "USDC", "quotation": q, "ai": result}, indent=2, ensure_ascii=False))
    print("\nNo se ejecutó ninguna orden. Para ejecutar se requiere confirmación explícita con el quotation id.")

if __name__ == "__main__": main()

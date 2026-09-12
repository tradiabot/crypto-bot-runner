#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile

ROOT=os.path.dirname(os.path.abspath(__file__))
ENV_PATH=os.path.join(ROOT, ".env")

def read_env():
    values={}
    order=[]
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                raw=line.rstrip("\n")
                if "=" in raw and not raw.lstrip().startswith("#"):
                    k,v=raw.split("=",1)
                    values[k]=v
                    order.append(k)
    return values, order

def write_env(values, order):
    fd,tmp=tempfile.mkstemp(prefix=".env.",dir=ROOT,text=True)
    try:
        seen=set()
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            for k in order:
                if k in values and k not in seen:
                    f.write(f"{k}={values[k]}\n")
                    seen.add(k)
            for k,v in values.items():
                if k not in seen:
                    f.write(f"{k}={v}\n")
        os.chmod(tmp,0o600)
        os.replace(tmp,ENV_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def ollama_models():
    try:
        p=subprocess.run(["ollama","list"], text=True, capture_output=True, timeout=15)
    except Exception as exc:
        raise SystemExit(f"No se pudo ejecutar ollama list: {exc}")
    if p.returncode != 0:
        raise SystemExit(p.stderr.strip() or "ollama list fallo")
    models=[]
    for line in p.stdout.splitlines()[1:]:
        parts=line.split()
        if parts:
            models.append(parts[0])
    return models

def main():
    values, order=read_env()
    current=values.get("OLLAMA_MODEL","qwen3:0.6b")
    models=ollama_models()
    print("Modelos Ollama instalados:\n")
    for i,m in enumerate(models,1):
        mark="*" if m == current else " "
        print(f"{i}) {mark} {m}")
    print(f"\nActual: {current}")
    choice=input("Selecciona numero, escribe modelo, o Enter para conservar: ").strip()
    if not choice:
        print("Modelo sin cambios.")
        return
    if choice.isdigit():
        idx=int(choice)-1
        if idx < 0 or idx >= len(models):
            raise SystemExit("Seleccion invalida")
        selected=models[idx]
    else:
        selected=choice
    values["OLLAMA_MODEL"]=selected
    if "OLLAMA_MODEL" not in order:
        order.append("OLLAMA_MODEL")
    write_env(values, order)
    print(f"Modelo guardado: {selected}")
    print("Relanza la CLI/loop para que el proceso activo tome el cambio.")

if __name__=="__main__":
    main()

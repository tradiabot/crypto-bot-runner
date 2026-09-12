#!/usr/bin/env python3
import getpass
import os
import tempfile

ROOT=os.path.dirname(os.path.abspath(__file__))
ENV_PATH=os.path.join(ROOT,".env")

def read_env():
    values={}
    order=[]
    try:
        with open(ENV_PATH,encoding="utf-8") as handle:
            for line in handle:
                raw=line.rstrip("\n")
                if "=" in raw and not raw.lstrip().startswith("#"):
                    key,value=raw.split("=",1)
                    values[key]=value
                    order.append(key)
    except FileNotFoundError:
        pass
    return values,order

def write_env(values,order):
    fd,path=tempfile.mkstemp(prefix=".env.",dir=ROOT,text=True)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as handle:
            seen=set()
            for key in [*order,*values]:
                if key in values and key not in seen:
                    handle.write(f"{key}={values[key]}\n")
                    seen.add(key)
        os.chmod(path,0o600)
        os.replace(path,ENV_PATH)
    finally:
        if os.path.exists(path): os.unlink(path)

def main():
    values,order=read_env()
    current=values.get("AI_PROVIDER","ollama")
    try:
        provider=input(f"Proveedor IA [groq/ollama/technical] ({current}): ").strip().lower() or current
    except (EOFError,KeyboardInterrupt):
        print("\nConfiguración cancelada.")
        return 130
    if provider not in {"groq","ollama","technical"}:
        raise SystemExit("Proveedor inválido")
    values["AI_PROVIDER"]=provider
    if provider == "groq":
        key=getpass.getpass("Groq API key (Enter conserva la existente): ").strip()
        if key: values["GROQ_API_KEY"]=key
        if not values.get("GROQ_API_KEY","").strip():
            raise SystemExit("Falta GROQ_API_KEY; no se modificó la configuración")
        model=input(f"Modelo Groq [{values.get('GROQ_MODEL','openai/gpt-oss-20b')}]: ").strip()
        values["GROQ_MODEL"]=model or values.get("GROQ_MODEL","openai/gpt-oss-20b")
        fallback=input(f"Fallbacks [{values.get('AI_FALLBACK_PROVIDERS','technical')}]: ").strip()
        values["AI_FALLBACK_PROVIDERS"]=fallback or values.get("AI_FALLBACK_PROVIDERS","technical")
    write_env(values,order)
    print(f"Proveedor IA guardado: {provider}. La clave no se mostró ni se registró.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())

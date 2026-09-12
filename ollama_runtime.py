#!/usr/bin/env python3
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:11434"
LOG_FILE = "/tmp/crypto-bot-ollama.log"


def base_url():
    return os.getenv("OLLAMA_URL", DEFAULT_URL).rstrip("/")


def model():
    return os.getenv("OLLAMA_MODEL", "qwen3:0.6b")


def _request(path, payload=None, timeout=5):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base_url() + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def tags_ready(timeout=3):
    try:
        _request("/api/tags", timeout=timeout)
        return True
    except Exception:
        return False


def start_server():
    log = open(LOG_FILE, "a", encoding="utf-8")
    try:
        server_env=os.environ.copy()
        # Keep local inference bounded to one model/request at a time.
        server_env.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
        server_env.setdefault("OLLAMA_NUM_PARALLEL", "1")
        subprocess.Popen(["ollama", "serve"], stdout=log, stderr=log, start_new_session=True, env=server_env)
        return log
    except OSError:
        log.close()
        raise


def ensure_server(wait_seconds=25):
    if tags_ready():
        return True, "activo"
    handle = start_server()
    try:
        for _ in range(wait_seconds):
            if tags_ready():
                return True, "iniciado"
            time.sleep(1)
        return False, f"no responde; revisa {LOG_FILE}"
    finally:
        handle.close()


def cleanup_loaded_models():
    """Release stale model workers before a new CLI session.

    This only unloads models from Ollama; it does not stop the Ollama server
    and never touches exchange or trading processes.
    """
    if os.getenv("OLLAMA_CLEANUP_ON_START", "YES").upper() != "YES":
        return
    try:
        result=subprocess.run(["ollama", "ps"], text=True, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return
    names=[]
    for line in result.stdout.splitlines()[1:]:
        parts=line.split()
        if parts and parts[0] not in names:
            names.append(parts[0])
    for name in names:
        try:
            subprocess.run(["ollama", "stop", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue


def warmup_generation(timeout=45):
    payload = {
        "model": model(),
        "prompt": "Responde solo JSON: {\"ok\":true}",
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 20},
    }
    raw = _request("/api/generate", payload=payload, timeout=timeout)
    parsed = json.loads(raw)
    return json.loads(parsed.get("response", "{}"))


def ensure_ready(wait_seconds=25, generation_timeout=45):
    ok, status = ensure_server(wait_seconds=wait_seconds)
    if not ok:
        return False, status
    last_error = None
    for attempt in range(2):
        try:
            warmup_generation(timeout=generation_timeout)
            suffix = "tras reintento" if attempt else "listo"
            return True, f"{status}; modelo {model()} {suffix}"
        except urllib.error.URLError as exc:
            last_error = f"Ollama escucha pero el modelo no responde: {exc.reason}"
        except Exception as exc:
            last_error = f"Ollama escucha pero la prueba del modelo fallo: {type(exc).__name__}: {exc}"
        if attempt == 0 and not tags_ready():
            ok, status = ensure_server(wait_seconds=wait_seconds)
            if not ok:
                return False, status
    return False, last_error or "Ollama no esta disponible"

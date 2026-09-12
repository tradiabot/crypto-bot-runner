#!/usr/bin/env python3
import concurrent.futures, json, os, re, subprocess, sys, time, urllib.error, urllib.request
from ollama_runtime import ensure_server
from exchange_adapter import universe as configured_universe
from trading_swarm import enabled as swarm_enabled, prompt_prefix as swarm_prompt_prefix, role_for_cycle
ROOT=os.path.dirname(os.path.abspath(__file__))
MARKET_CACHE_PATH=os.path.join(ROOT,".runtime","latest-market.json")
SKILL="/root/.agents/skills/crypto-com-app/scripts"; DEFAULT=["BTC","ETH","SOL","CRO","ADA","AVAX","LINK","DOT","MATIC","APT"]
def load_env():
 p=os.path.join(os.path.dirname(__file__),".env")
 if os.path.exists(p):
  for line in open(p,encoding="utf-8"):
   if "=" in line and not line.lstrip().startswith("#"):
    k,v=line.strip().split("=",1); os.environ.setdefault(k, v.strip().strip('"').strip("'"))
 os.environ["CDC_API_KEY"]=os.getenv("CDC_API_KEY",os.getenv("CRYPTO_COM_API_KEY","")); os.environ["CDC_API_SECRET"]=os.getenv("CDC_API_SECRET",os.getenv("CRYPTO_COM_API_SECRET",""))
def val(text,key,default=None):
 m=re.search(r'"'+re.escape(key)+r'"\s*:\s*(?:"([^"]*)"|(true|false|null)|(-?[0-9.]+))',text)
 if not m:return default
 return next((x for x in m.groups() if x is not None),default)
def coin(symbol):
 body=json.dumps({"keyword":symbol,"sort_by":"rank","sort_direction":"asc","native_currency":"USD","page_size":100})
 try: coin_timeout=max(10,int(os.getenv("CDC_COIN_TIMEOUT_SECONDS","45")))
 except ValueError: coin_timeout=45
 try:
  p=subprocess.run(["npx","tsx",f"{SKILL}/coins.ts","search",body],text=True,capture_output=True,env=os.environ.copy(),check=False,timeout=coin_timeout)
 except subprocess.TimeoutExpired:
  print(f"[WARN] {symbol}: consulta agotó {coin_timeout}s",file=sys.stderr)
  return None
 try:
  raw=p.stdout[p.stdout.find("{"):].strip() if "{" in p.stdout else p.stdout
  data=json.loads(raw)
 except json.JSONDecodeError:
  token=f'"symbol": "{symbol}"'
  pos=p.stdout.find(token)
  if pos < 0:
   print(f"[WARN] {symbol}: no encontrado",file=sys.stderr); return None
  start=p.stdout.rfind('\n      {',0,pos)
  end=p.stdout.find('\n      },',pos)
  if start < 0: start=max(0,pos-5000)
  if end < 0: end=pos+3000
  window=p.stdout[start:end]
  m=re.search(r'"price_usd"\s*:\s*\{.*?"amount"\s*:\s*"([^"]+)"',window,re.S); price=m.group(1) if m else None
  if not price: print(f"[WARN] {symbol}: sin precio",file=sys.stderr); return None
  vm=re.search(r'"volume_24h_native"\s*:\s*\{.*?"amount"\s*:\s*"([^"]+)"',window,re.S)
  return {"symbol":symbol,"tradable":val(window,"is_tradable"),"price_usd":price,"change_24h":val(window,"percent_change_24h_native"),"change_1w":val(window,"percent_change_1w_native"),"volume_24h":vm.group(1) if vm else None,"rank":val(window,"rank")}
 coins=data.get("data",{}).get("coins",[]) if data.get("ok") else []
 target=next((c for c in coins if c.get("symbol")==symbol or c.get("rails_id")==symbol), None)
 if not target:
  # Crypto.com search can intermittently miss CRO; retry by common name once.
  if symbol == "CRO":
   body2=json.dumps({"keyword":"Cronos","sort_by":"rank","sort_direction":"asc","native_currency":"USD","page_size":100})
   try:
    p2=subprocess.run(["npx","tsx",f"{SKILL}/coins.ts","search",body2],text=True,capture_output=True,env=os.environ.copy(),check=False,timeout=coin_timeout)
   except subprocess.TimeoutExpired:
    print("[WARN] CRO: consulta alternativa agotada",file=sys.stderr)
    p2=None
   if p2 is None:
    return None
   try:
    raw2=p2.stdout[p2.stdout.find("{"):].strip() if "{" in p2.stdout else p2.stdout
    data2=json.loads(raw2)
    coins2=data2.get("data",{}).get("coins",[]) if data2.get("ok") else []
    target=next((c for c in coins2 if c.get("symbol")=="CRO" or c.get("rails_id")=="CRO"), None)
   except Exception:
    target=None
  if not target:
   print(f"[WARN] {symbol}: no encontrado",file=sys.stderr); return None
 price=(target.get("price_usd") or {}).get("amount")
 if not price: print(f"[WARN] {symbol}: sin precio",file=sys.stderr); return None
 volume=(target.get("volume_24h_native") or {}).get("amount")
 return {"symbol":target.get("symbol") or symbol,"tradable":str(target.get("is_tradable",False)).lower(),"price_usd":price,"change_24h":target.get("percent_change_24h_native"),"change_1w":target.get("percent_change_1w_native"),"volume_24h":volume,"rank":str(target.get("rank",""))}
def safe_coin(symbol):
 try:
  return coin(symbol)
 except Exception as exc:
  print(f"[WARN] {symbol}: consulta falló: {exc}",file=sys.stderr)
  return None

def fetch_coins(symbols):
 try:
  configured_workers=int(os.getenv("MARKET_SCAN_WORKERS","2"))
 except ValueError:
  configured_workers=2
 workers=max(1,min(len(symbols),configured_workers))
 if workers == 1:
  return [result for result in (safe_coin(symbol) for symbol in symbols) if result]
 with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
  return [result for result in executor.map(safe_coin,symbols) if result]

def cached_market_items(symbols):
 try:
  max_age=max(60, int(os.getenv("MARKET_CACHE_FALLBACK_SECONDS", "900")))
  if not os.path.exists(MARKET_CACHE_PATH) or time.time()-os.path.getmtime(MARKET_CACHE_PATH) > max_age:
   return []
  with open(MARKET_CACHE_PATH, encoding="utf-8") as handle:
   report=json.load(handle)
  cached=report.get("universe", []) if isinstance(report, dict) else []
  wanted=set(symbols)
  return [item for item in cached if isinstance(item, dict) and item.get("symbol") in wanted]
 except (OSError, ValueError, TypeError):
  return []

def parse_ai_response(raw_text):
 if isinstance(raw_text, bytes):
  raw_text=raw_text.decode("utf-8", "replace")
 raw_text=str(raw_text).strip()
 try:
  outer=json.loads(raw_text)
 except json.JSONDecodeError:
  outer=None
  for line in raw_text.splitlines():
   line=line.strip()
   if not line:
    continue
   try:
    candidate=json.loads(line)
   except json.JSONDecodeError:
    continue
   if isinstance(candidate, dict) and ("response" in candidate or "opportunities" in candidate):
    outer=candidate
    break
  if outer is None:
   outer={"response":raw_text}
 if isinstance(outer, dict) and ("opportunities" in outer or "symbol" in outer):
  return outer
 response=outer.get("response", outer.get("output", outer.get("text", ""))) if isinstance(outer, dict) else outer
 if isinstance(response, (dict, list)):
  return response
 response=str(response).strip()
 response=re.sub(r"<think>.*?</think>", "", response, flags=re.I|re.S).strip()
 response=re.sub(r"^```(?:json)?\s*", "", response, flags=re.I).strip()
 response=re.sub(r"\s*```$", "", response).strip()
 response=re.sub(r"^[\x00-\x1f\x7f]+", "", response).strip()
 if not response:
  raise json.JSONDecodeError("empty response", raw_text, 0)
 try:
  return json.loads(response)
 except json.JSONDecodeError:
  decoder=json.JSONDecoder()
  for marker in ("{", "["):
   pos=response.find(marker)
   if pos >= 0:
    try:
     parsed, _=decoder.raw_decode(response[pos:])
     return parsed
    except json.JSONDecodeError:
     pass
  for open_c, close_c in (("[", "]"), ("{", "}")):
   a=response.find(open_c)
   if a >= 0:
    depth=0
    in_string=False
    escape=False
    for idx, ch in enumerate(response[a:], start=a):
     if escape:
      escape=False
      continue
     if ch == "\\":
      escape=True
      continue
     if ch == '"':
      in_string=not in_string
      continue
     if in_string:
      continue
     if ch == open_c:
      depth += 1
     elif ch == close_c:
      depth -= 1
      if depth == 0:
       candidate=response[a:idx+1]
       try:
        return json.loads(candidate)
       except json.JSONDecodeError:
        break
  ops=[]
  for line in response.splitlines():
   clean=re.sub(r"^[^A-Za-z0-9]+", "", line.strip()).replace("*", "").strip()
   if not clean:
    continue
   sym_m=re.search(r'(?i)(?:^|\s|[*#])([A-Z][A-Z0-9]{1,12})(?=\s*[:|,-]?\s*(?:BUY|SELL|HOLD)\b)', clean)
   act_m=re.search(r'(?i)\b(BUY|SELL|HOLD)\b', clean)
   if not sym_m or not act_m:
    continue
   conf_m=re.search(r'(?i)\b(?:conf(?:idence)?|score)\s*[=: ]\s*([01](?:\.\d+)?)', clean)
   if conf_m:
    try:
     conf=float(conf_m.group(1))
    except ValueError:
     conf=0.0
   else:
    # A missing confidence is not a calibrated signal; never promote it to a trade.
    conf=0.0
   ops.append({"symbol":sym_m.group(1).upper(),"action":act_m.group(1).upper(),"confidence":conf,"reason":clean[:180]})
  if ops:
   return ops
  raise json.JSONDecodeError("unparseable ai response", response[:200], 0)

UNSUPPORTED_REASON_RE = re.compile(
 r"\b(?:ceo|insider|shares?|stocks?|earnings|dividends?|company|sec filing|executive|analysts?|upgrades?|downgrades?|price targets?|target prices?|ratings?|bought|sold|purchases?|sales?|partnerships?|mergers?|acquisitions?|announcements?|news|sector|whales?|on-chain|sentiment|accion(?:es)?|empresa|directiv[oa]s?|analistas?|objetivos? de precio|noticias?|compr[oó]|vendi[oó]|resultados trimestrales)\b",
 re.I,
)

def unsupported_ai_reason(reason):
 return bool(UNSUPPORTED_REASON_RE.search(str(reason or "")))

def normalize_ai_result(result, items):
 allowed={x.get("symbol") for x in items}
 if isinstance(result, list):
  opps=result
 elif isinstance(result, dict) and "opportunities" in result:
  opps=result.get("opportunities", [])
 elif isinstance(result, dict) and "symbol" in result:
  opps=[result]
 else:
  opps=[]
 clean=[]
 market_by_symbol={str(item.get("symbol","")).upper():item for item in items if isinstance(item,dict)}
 for op in opps:
  if not isinstance(op, dict):
   continue
  symbol=str(op.get("symbol","")).upper().strip()
  if symbol not in allowed:
   continue
  raw_action=str(op.get("action","HOLD")).upper().strip()
  action=raw_action
  if action not in {"BUY", "SELL", "HOLD"}:
   found=[a for a in ("BUY","SELL","HOLD") if a in raw_action]
   action=found[0] if len(found)==1 else "HOLD"
  try:
   confidence=float(op.get("confidence",op.get("conf",0.0)))
  except (TypeError, ValueError):
   mapped={"LOW":0.35,"MEDIUM":0.60,"HIGH":0.80,"BAJA":0.35,"MEDIA":0.60,"ALTA":0.80}
   confidence=mapped.get(str(op.get("confidence","")).upper(),0.0)
  confidence=max(0.0,min(1.0,confidence))
  reason=str(op.get("reason","") or "sin razon")[:180]
  if action != "HOLD" and unsupported_ai_reason(reason):
   fallback=technical_fallback(market_by_symbol.get(symbol,{"symbol":symbol}), "señal IA descartada; fallback tecnico por mercado")
   action=fallback.get("action","HOLD"); confidence=float(fallback.get("confidence",0.0) or 0.0); reason=fallback.get("reason","señal descartada: razón externa no verificable")
  elif action != "HOLD":
   market=market_by_symbol.get(symbol,{})
   try:
    change_24h=float(market.get("change_24h",0) or 0)
   except (TypeError,ValueError):
    change_24h=0.0
   try:
    change_1w=float(market.get("change_1w",0) or 0)
   except (TypeError,ValueError):
    change_1w=0.0
   reason=f"métricas suministradas: d24={change_24h:.2f}%, w1={change_1w:.2f}%"
  clean.append({"symbol":symbol,"action":action,"confidence":confidence,"reason":reason})
 if not clean:
  raise json.JSONDecodeError("sin opportunities validas", json.dumps(result)[:200], 0)
 return {"opportunities":clean}

AI_SYSTEM_PROMPT = (
 "Eres un formateador de señales de trading. Responde exclusivamente con JSON válido, "
 "sin markdown ni texto adicional. Usa únicamente símbolos, acciones y métricas suministrados; "
 "nunca inventes noticias, empresas, directivos ni transacciones internas."
)

def ask_ollama_model(model, prompt):
 options={
  "temperature":float(os.getenv("OLLAMA_TEMPERATURE","0.15")),
  "num_predict":int(os.getenv("OLLAMA_NUM_PREDICT","90")),
  "num_ctx":int(os.getenv("OLLAMA_NUM_CTX","1024")),
 }
 payload={"model":model,"prompt":prompt,"stream":False,"think":False,"options":options,
          "system":AI_SYSTEM_PROMPT}
 output_format=os.getenv("OLLAMA_OUTPUT_FORMAT", "").strip().lower()
 if output_format == "json":
  payload["format"] = "json"
 body=json.dumps(payload).encode()
 url=os.getenv("OLLAMA_URL","http://127.0.0.1:11434")+"/api/generate"
 timeout=int(os.getenv("OLLAMA_GENERATE_TIMEOUT_SECONDS", os.getenv("OLLAMA_MODEL_TIMEOUT_SECONDS", "60")))
 retries=max(0, int(os.getenv("OLLAMA_404_RETRIES", "1")))
 for attempt in range(retries + 1):
  req=urllib.request.Request(url,data=body,headers={"Content-Type":"application/json"})
  try:
   with urllib.request.urlopen(req,timeout=timeout) as response:
    return parse_ai_response(response.read())
  except urllib.error.HTTPError as exc:
   if exc.code != 404 or attempt >= retries:
    raise
   time.sleep(min(2.0, 0.5 * (attempt + 1)))

def ask_groq_model(model, prompt):
 api_key=os.getenv("GROQ_API_KEY", "").strip()
 if not api_key:
  raise RuntimeError("GROQ_API_KEY no configurada")
 base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
 payload={
  "model":model,
  "messages":[
   {"role":"system","content":AI_SYSTEM_PROMPT},
   {"role":"user","content":prompt},
  ],
  "temperature":float(os.getenv("GROQ_TEMPERATURE", "0.15")),
  "max_completion_tokens":int(os.getenv("GROQ_MAX_COMPLETION_TOKENS", "500")),
  "response_format":{"type":"json_object"},
 }
 request=urllib.request.Request(
  base_url+"/chat/completions",
  data=json.dumps(payload).encode(),
  headers={
   "Authorization":"Bearer "+api_key,
   "Content-Type":"application/json",
   "Accept":"application/json",
   # Groq's Cloudflare edge rejects Python urllib's default user-agent with
   # error 1010 on this Android/Termux network.
   "User-Agent":os.getenv("GROQ_USER_AGENT","curl/8.14.1"),
  },
 )
 timeout=max(5,int(os.getenv("GROQ_TIMEOUT_SECONDS", "30")))
 with urllib.request.urlopen(request,timeout=timeout) as response:
  outer=json.loads(response.read())
 try:
  content=outer["choices"][0]["message"]["content"]
 except (KeyError,IndexError,TypeError) as exc:
  raise json.JSONDecodeError("respuesta Groq sin contenido",json.dumps(outer)[:200],0) from exc
 return parse_ai_response(content)

def scan_trend_score(item):
 try:
  d24=float(item.get("change_24h",0) or 0)
 except Exception:
  d24=0.0
 try:
  w1=float(item.get("change_1w",0) or 0)
 except Exception:
  w1=0.0
 try:
  rank=float(item.get("rank",999) or 999)
 except Exception:
  rank=999.0
 liquidity=max(0.0,(100.0-rank)/100.0)
 return (w1*0.60)+(d24*0.35)+liquidity

def prefilter_items(items):
 try:
  limit=int(os.getenv("AI_PREFILTER_CANDIDATES","3"))
 except ValueError:
  limit=3
 if limit <= 0 or len(items) <= limit:
  return items
 tradable=[x for x in items if str(x.get("tradable","false")).lower()=="true"] or items
 return sorted(tradable, key=scan_trend_score, reverse=True)[:limit]

def ask_once(items, role=None):
 compact=[]
 for x in items:
  def rnum(v, nd=3):
   try: return round(float(v), nd)
   except Exception: return 0
  compact.append({"s":x.get("symbol"),"p":rnum(x.get("price_usd"),6),"d24":rnum(x.get("change_24h"),2),"w1":rnum(x.get("change_1w"),2),"t":str(x.get("tradable","false")).lower()=="true"})
 symbols=", ".join(x.get("s", "?") for x in compact)
 rows=";".join(f"{x['s']}:p{x['p']}:d{x['d24']}:w{x['w1']}" for x in compact)
 if len(compact) == 1:
  sym=compact[0].get("s","?")
  if swarm_enabled():
   prompt=swarm_prompt_prefix(role, items, rows, os.getenv("AI_FEEDBACK_CONTEXT","").strip())
   prompt += "\n" + anti_repeat_guidance()
  else:
   prompt=(
    f"Analyze only {sym}. Return ONLY a valid JSON object containing an opportunities array; each item has symbol, action, confidence, reason. "
    f"Use symbol {sym}; action must be BUY, SELL, or HOLD; confidence must be a number from 0 to 1; reason must be short. No markdown. "
    "Use only p, d24 and w1 from Market; never use news, companies, shares, CEOs or insider transactions. "
    "Market="+rows
   )
 else:
  if swarm_enabled():
   prompt=swarm_prompt_prefix(role, items, rows, os.getenv("AI_FEEDBACK_CONTEXT","").strip())
   prompt += "\n" + anti_repeat_guidance()
  else:
   prompt=(
    "Analyze the listed assets. Return ONLY a valid JSON object with an opportunities array of objects with keys symbol, action, confidence, reason. "
    "Use listed symbols only; action must be BUY, SELL, or HOLD; confidence must be a number from 0 to 1. No markdown. "
    "Use only p, d24 and w1 from Market; never use news, companies, shares, CEOs or insider transactions. "
    "Do not repeat the same exact opportunities unless conditions changed materially. "
    "Market="+rows
   )
 errors=[]
 primary_provider=os.getenv("AI_PROVIDER", "ollama").strip().lower()
 fallback_providers=[x.strip().lower() for x in os.getenv("AI_FALLBACK_PROVIDERS", "ollama,technical").split(",") if x.strip()]
 providers=[]
 for provider in [primary_provider,*fallback_providers]:
  if provider not in providers:
   providers.append(provider)
 for provider in providers:
  if provider == "technical":
   result={"opportunities":[technical_fallback(x,"IA remota no disponible; fallback técnico seguro") for x in items]}
   result["model_used"]="technical"
   result["fallback_from"]=errors
   return result
  if provider == "groq":
   models=[os.getenv("GROQ_MODEL","openai/gpt-oss-20b")]
  elif provider == "ollama":
   ok, detail=ensure_server(wait_seconds=20)
   if not ok:
    errors.append(f"ollama: {detail}")
    continue
   primary=os.getenv("OLLAMA_MODEL","qwen3:0.6b")
   fallback_raw=os.getenv("OLLAMA_FALLBACK_MODELS",os.getenv("OLLAMA_FALLBACK_MODEL","qwen3:1.7b"))
   models=[]
   for model in [primary,*[x.strip() for x in fallback_raw.split(",") if x.strip()]]:
    if model not in models: models.append(model)
  else:
   errors.append(f"proveedor desconocido: {provider}")
   continue
  for model in models:
   label=f"{provider}/{model}"
   try:
    raw=ask_groq_model(model,prompt) if provider == "groq" else ask_ollama_model(model,prompt)
    result=normalize_ai_result(raw,items)
    result["provider_used"]=provider
    result["model_used"]=model
    if errors: result["fallback_from"]=errors
    return result
   except urllib.error.HTTPError as exc:
    detail=f"HTTP {exc.code}"
    if exc.code == 429: detail="cuota temporal agotada (HTTP 429)"
    errors.append(f"{label}: {detail}")
   except urllib.error.URLError as exc:
    errors.append(f"{label}: no respondió: {exc.reason}")
   except (TimeoutError, OSError):
    errors.append(f"{label}: timeout")
   except (json.JSONDecodeError, RuntimeError) as exc:
    errors.append(f"{label}: {exc}")
   print(f"[IA] {label} falló; probando fallback",file=sys.stderr,flush=True)
 raise RuntimeError("; ".join(errors) or "La IA devolvio JSON invalido")
def technical_fallback(item, reason="fallback tecnico"):
 try:
  d24=float(item.get("change_24h",0) or 0); w1=float(item.get("change_1w",0) or 0)
 except Exception:
  d24=w1=0.0
 action="HOLD"; confidence=0.0
 if os.getenv("ALLOW_TECHNICAL_FALLBACK_SIGNALS","NO").upper()=="YES":
  if d24 > 1.0 and w1 > 5.0:
   action="BUY"; confidence=0.61; reason="fallback tecnico: tendencia positiva"
  elif d24 < -5.0:
   action="SELL"; confidence=0.61; reason="fallback tecnico: caida fuerte"
 return {"symbol":item.get("symbol","?"),"action":action,"confidence":confidence,"reason":reason}

def anti_repeat_guidance():
 return (
  "Avoid repeating the exact same BUY/SELL picks across cycles unless the market score changed materially "
  "or confidence increased. Prefer only the top 1-2 opportunities; leave the rest as HOLD."
 )

def ask(items):
 original_items=list(items)
 items=prefilter_items(original_items)
 try:
  configured_chunk=max(1,int(os.getenv("AI_CHUNK_SIZE","3")))
 except ValueError:
  configured_chunk=3
 # Small local models follow the format much more reliably one asset at a time.
 # Remote inference uses one request for all prefiltered candidates, conserving
 # the free daily quota. Local Ollama can retain its smaller chunks.
 if os.getenv("AI_PROVIDER","ollama").strip().lower() == "groq":
  chunk_size=len(items)
 else:
  chunk_size=min(configured_chunk, len(items))
 if len(items) <= chunk_size:
  try:
   result=ask_once(items)
  except RuntimeError as exc:
   raise
  seen={op.get("symbol") for op in result.get("opportunities",[]) if isinstance(op,dict)}
  for x in original_items:
   sym=x.get("symbol","?")
   if sym not in seen:
    result.setdefault("opportunities",[]).append({"symbol":sym,"action":"HOLD","confidence":0.0,"reason":"fuera del top tendencia IA; decision segura"})
  return result
 all_ops=[]
 model_used=None
 fallback=[]
 errors=[]
 role=role_for_cycle() if swarm_enabled() else None
 for i in range(0, len(items), chunk_size):
  chunk=items[i:i+chunk_size]
  try:
   result=ask_once(chunk, role=role)
   all_ops.extend(result.get("opportunities", []))
   model_used=model_used or result.get("model_used")
   fallback.extend(result.get("fallback_from", []) or [])
  except RuntimeError as exc:
   errors.append(str(exc))
   for x in chunk:
    all_ops.append(technical_fallback(x, "IA no parseable; decision segura"))
 seen={op.get("symbol") for op in all_ops if isinstance(op, dict)}
 for x in original_items:
  sym=x.get("symbol","?")
  if sym not in seen:
   all_ops.append(technical_fallback(x, "fuera del top tendencia IA; decision segura"))
 out={"opportunities":all_ops}
 if model_used:
  out["model_used"]=model_used
 if fallback:
  out["fallback_from"]=fallback
 if errors and not any(op.get("confidence",0)>0 for op in all_ops):
  raise RuntimeError("; ".join(errors))
 if errors:
  out["partial_errors"]=errors
 out["swarm"]={"enabled": swarm_enabled(), "role": role, "chunk_size": chunk_size} if swarm_enabled() else {"enabled": False}
 return out

def write_market_cache(report):
 os.makedirs(os.path.dirname(MARKET_CACHE_PATH),exist_ok=True)
 temp_path=f"{MARKET_CACHE_PATH}.{os.getpid()}.tmp"
 try:
  with open(temp_path,"w",encoding="utf-8") as handle:
   json.dump(report,handle,ensure_ascii=False)
  os.replace(temp_path,MARKET_CACHE_PATH)
 finally:
  if os.path.exists(temp_path):
   os.unlink(temp_path)

def plain(report):
 print("ESCANEO DE OPORTUNIDADES · FUENTE USDC")
 for x in report["universe"]:
  print(f"{x.get('symbol','?')} · precio ${x.get('price_usd','-')} · 24h {x.get('change_24h','-')} · semana {x.get('change_1w','-')} · tradable {x.get('tradable','-')}")
 print("DECISIÓN IA")
 for x in report["ai"].get("opportunities",[]):
  print(f"{x.get('symbol','?')} · {x.get('action','HOLD')} · confianza {x.get('confidence','-')} · {x.get('reason','')}")
def main():
 load_env(); mode="--plain" in sys.argv;
 if mode: print("Progreso 10% · preparando activos...", flush=True)
 symbols=[x.upper() for x in sys.argv[1:] if not x.startswith("--")]
 universe_env=configured_universe()
 full_scan=os.getenv("AUTO_FULL_SCAN_NOW","NO").upper()=="YES"
 if symbols:
  scan_symbols=symbols
 elif full_scan:
  scan_symbols=[]
  for sym in universe_env + DEFAULT:
   if sym not in scan_symbols:
    scan_symbols.append(sym)
 else:
  scan_symbols=universe_env or DEFAULT
 items=fetch_coins(scan_symbols)
 cached_fallback=False
 if not items:
  items=cached_market_items(scan_symbols)
  cached_fallback=bool(items)
 if not items: raise SystemExit("No se encontraron activos")
 if mode: print("Progreso 60% · datos recibidos; consultando IA...", flush=True)
 if cached_fallback:
  print("Mercado no disponible; usando caché solo para HOLD", file=sys.stderr)
  ai={"opportunities":[{"symbol":x.get("symbol","?"),"action":"HOLD","confidence":0.0,"reason":"mercado temporalmente no disponible; caché segura, no operar"} for x in items],"cache_fallback":True}
 else:
  try:
   ai=ask(items)
  except RuntimeError as exc:
   print(f"IA no disponible: {exc}; usando decision conservadora HOLD", file=sys.stderr)
   ai={"opportunities":[{"symbol":x.get("symbol","?"),"action":"HOLD","confidence":0.0,"reason":"IA no disponible; no operar"} for x in items],"error":str(exc)}
 report={"source":"USDC","universe":items,"ai":ai,"market_cache_fallback":cached_fallback}
 write_market_cache(report)
 if mode: print("Progreso 100% · análisis terminado.", flush=True)
 if mode: plain(report)
 else: print(json.dumps(report,indent=2,ensure_ascii=False))
if __name__=="__main__": main()

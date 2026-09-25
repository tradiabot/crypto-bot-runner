#!/usr/bin/env python3
import json, os, signal as process_signal, subprocess, tempfile, time
import urllib.error, urllib.request
import fcntl
from datetime import datetime, timezone
from exchange_adapter import quote_buy, quote_sell, quote_exchange, confirm, available, selected as selected_exchange, universe as exchange_universe, available_balances as account_balances
from risk_gate import validate
from bot_logger import log_event, feedback_summary, read_events
from performance_tracker import decision_context
from cost_basis import position as basis_position, load_cost_basis, save_cost_basis, apply_trade
from adaptive_risk import status as adaptive_status, feedback_context as adaptive_feedback_context
from universe_autopilot import recommend as recommend_universe, apply_recommendation as apply_universe_recommendation
ROOT=os.path.dirname(os.path.abspath(__file__))
STATE_DIR=os.path.join(ROOT,".runtime")
STATE_PATH=os.path.join(STATE_DIR,"trade_state.json")
EXECUTION_LOCK_PATH=os.path.join(STATE_DIR,"execution.lock")
INSTANCE_LOCK_PATH=os.path.join(STATE_DIR,"crypto-auto.lock")

def _write_lock_metadata(lock, label):
    try:
        lock.seek(0)
        lock.truncate(0)
        payload={"pid":os.getpid(),"label":label,"ts":time.time()}
        lock.write(json.dumps(payload))
        lock.flush()
        os.fsync(lock.fileno())
    except Exception:
        pass

def _read_lock_metadata(path):
    try:
        with open(path, encoding="utf-8") as f:
            data=f.read().strip()
        return json.loads(data) if data else {}
    except Exception:
        return {}

def acquire_file_lock(path, label, wait_seconds=0.0):
    os.makedirs(STATE_DIR, exist_ok=True)
    deadline=time.time()+max(0.0, wait_seconds)
    while True:
        lock=open(path, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _write_lock_metadata(lock, label)
            return lock
        except BlockingIOError:
            lock.close()
            if time.time() >= deadline:
                return None
            time.sleep(0.5)


def acquire_execution_lock():
    try:
        wait_seconds = float(os.getenv("EXECUTION_LOCK_WAIT_SECONDS", "8"))
    except (TypeError, ValueError):
        wait_seconds = 8.0
    return acquire_file_lock(EXECUTION_LOCK_PATH, "execution", wait_seconds=wait_seconds)

def terminate_process_group(proc, grace_seconds=5):
    if proc is None:
        return "", ""
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, process_signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, process_signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=grace_seconds)
        except (subprocess.TimeoutExpired, OSError):
            pass
    except OSError:
        pass
    for stream in (proc.stdout,proc.stderr,proc.stdin):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
    return "", ""

def interrupt_scan(_signum, _frame):
    raise KeyboardInterrupt

def load_env_file():
    path=os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    locked={"AUTO_EXECUTE","AUTO_LIVE","CONFIRM_LIVE","SUPERVISOR_EXECUTE"}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line=raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value=line.split("=", 1)
            key=key.strip()
            if key in locked and key in os.environ:
                continue
            os.environ[key]=value.strip().strip('"').strip("'")
def dynamic_env_value(name, default):
    """Read a runtime-tunable non-secret setting from .env for each cycle."""
    path=os.path.join(ROOT, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line=raw.strip()
                if line and not line.startswith("#") and line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return os.getenv(name, default)


def today_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state=json.load(f)
    except Exception:
        state={}
    if state.get("day") != today_key():
        state={"day":today_key(),"trades_today":0,"last_trade_ts":0}
    return state

def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp=STATE_PATH+".tmp"
    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(state,f,indent=2)
    os.replace(tmp,STATE_PATH)

def frequency_gate(state):
    max_day=int(dynamic_env_value("MAX_TRADES_PER_DAY", "6"))
    cooldown=int(dynamic_env_value("COOLDOWN_AFTER_TRADE_SECONDS", "900"))
    now=time.time()
    errors=[]
    if int(state.get("trades_today",0)) >= max_day:
        errors.append("max_trades_per_day")
    remaining=int(cooldown - (now - float(state.get("last_trade_ts",0) or 0)))
    if remaining > 0:
        errors.append(f"cooldown_active_{remaining}s")
    return {"approved":not errors,"errors":errors,"limits":{"max_trades_per_day":max_day,"cooldown_after_trade_seconds":cooldown},"state":state}

def _result_amount(value):
    if isinstance(value, dict):
        value=value.get("amount", 0)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def record_cost_basis(result):
    if not isinstance(result, dict):
        return
    source=result.get("amount", {})
    target=result.get("to_amount", {})
    native=result.get("native_amount", {})
    data=load_cost_basis()
    apply_trade(data, source.get("currency"), _result_amount(source), target.get("currency"), _result_amount(target), _result_amount(native), txid=str(result.get("id") or "") or None, ts=result.get("created_at") or time.time())
    save_cost_basis(data)

def record_trade():
    state=load_state()
    state["trades_today"]=int(state.get("trades_today",0))+1
    state["last_trade_ts"]=time.time()
    save_state(state)
    return state

def redact(value):
    if isinstance(value, dict):
        return {k:("[REDACTED]" if k.lower() in {"api_key","secret","keysecret"} else redact(v)) for k,v in value.items()}
    if isinstance(value, list): return [redact(v) for v in value]
    return value

def confidence_value(value, default=0.0):
    if isinstance(value, str):
        mapped={"low":0.35,"medium":0.60,"med":0.60,"high":0.80,"alta":0.80,"media":0.60,"baja":0.35}
        text=value.strip().lower()
        if text in mapped:
            return mapped[text]
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)

def env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)

def env_pct_map(name):
    """Lee 'BTC:0.042,ETH:0.054' como {'BTC': 0.042, 'ETH': 0.054}."""
    out={}
    for parte in (os.getenv(name,"") or "").split(","):
        parte=parte.strip()
        if not parte or ":" not in parte:
            continue
        sym,_,val=parte.partition(":")
        try:
            out[sym.strip().upper()]=float(val)
        except (TypeError, ValueError):
            continue
    return out

def risk_levels_for(symbol):
    """Objetivo y stop del activo, en fraccion (0.042 = 4.2%).

    Un stop fijo igual para todos se dispara dentro del ruido de un solo dia:
    el rango diario medio de BTC es ~3.5% y el de POL ~6.7%, asi que un 3%
    global vende POL por fluctuacion normal antes de que la tesis se cumpla.
    El worker mide el rango real de cada moneda y manda estos mapas; si no
    llegan, se cae al valor global de siempre.
    """
    sym=(symbol or "").strip().upper()
    stop=env_pct_map("STOP_LOSS_PCT_BY_ASSET").get(sym)
    prof=env_pct_map("MIN_PROFIT_TO_SELL_PCT_BY_ASSET").get(sym)
    if stop is None:
        stop=env_float("STOP_LOSS_PCT","0.04")
    if prof is None:
        prof=env_float("MIN_PROFIT_TO_SELL_PCT","0.015")
    # Topes de cordura: un mapa mal formado no debe poder desactivar el stop
    # ni exigir una ganancia inalcanzable.
    stop=min(max(stop,0.02),0.20)
    prof=min(max(prof,0.005),0.30)
    return prof, stop

def configured_universe():
    items=list(exchange_universe())
    base=["USDC","BTC","ETH","CRO"]
    out=[]
    for sym in items+base:
        if sym not in out:
            out.append(sym)
    return out

def asset_max_weight(symbol):
    symbol=symbol.upper()
    return env_float(f"{symbol}_MAX_WEIGHT", env_float("MAX_ASSET_WEIGHT", "0.35"))

def asset_min_weight(symbol):
    symbol=symbol.upper()
    return env_float(f"{symbol}_MIN_WEIGHT", "0")

def asset_target_weight(symbol):
    symbol=symbol.upper()
    return env_float(f"{symbol}_TARGET_WEIGHT", asset_max_weight(symbol))

def portfolio_snapshot(items):
    try:
        balances_by_symbol=account_balances()
    except (Exception,SystemExit) as exc:
        log_event("portfolio_balance_error", error=str(exc))
        balances_by_symbol={}
    symbols=configured_universe()
    for symbol, balance in balances_by_symbol.items():
        if not isinstance(balance, dict):
            continue
        if symbol not in symbols and (float(balance.get("amount",0.0) or 0.0)>0 or float(balance.get("native_usd",0.0) or 0.0)>0):
            symbols.append(symbol)
    assets={}
    total=0.0
    for symbol in symbols:
        bal=balances_by_symbol.get(symbol)
        if not isinstance(bal, dict):
            bal={"currency":symbol,"amount":0.0,"native_usd":0.0}
        native=float(bal.get("native_usd",0.0) or 0.0)
        if native <= 0 and symbol in items:
            try:
                native=float(bal.get("amount",0.0) or 0.0)*float(items[symbol].get("price_usd",0) or 0)
            except Exception:
                native=0.0
        assets[symbol]={"amount":float(bal.get("amount",0.0) or 0.0),"native_usd":native}
        total+=native
    for symbol,data in assets.items():
        data["weight"]=(data["native_usd"]/total) if total>0 else 0.0
        data["max_weight"]=asset_max_weight(symbol)
        data["target_weight"]=asset_target_weight(symbol)
        data["min_weight"]=asset_min_weight(symbol)
    return {"total_usd":total,"assets":assets}

def held_stable_excluded():
    """Stablecoins kept out of `items`.

    USDC is the funding currency and must never be sellable. USDT is excluded
    by default too, but it can be released via HELD_STABLE_EXCLUDE so a held
    USDT balance becomes eligible for conditional orders (e.g. USDT_USD) and
    stops being dead capital. AI-driven sells still skip it further down.
    """
    raw=os.getenv("HELD_STABLE_EXCLUDE", "USDC,USDT")
    return {x.strip().upper() for x in raw.split(",") if x.strip()} or {"USDC"}


def include_held_assets(items, snapshot):
    """Keep held assets eligible for risk-reducing sells when scans omit them."""
    merged=dict(items)
    excluded=held_stable_excluded()
    for symbol, asset in snapshot.get("assets", {}).items():
        if symbol in excluded or symbol in merged:
            continue
        amount=float(asset.get("amount", 0.0) or 0.0)
        native_usd=float(asset.get("native_usd", 0.0) or 0.0)
        if amount <= 0 or native_usd <= 0:
            continue
        merged[symbol]={
            "symbol":symbol,
            "price_usd":native_usd / amount,
            "change_24h":0.0,
            "change_1w":0.0,
            "rank":999,
            "tradable":"true",
            "source":"held_balance",
        }
    return merged

def avg_cost_for(symbol, perf):
    try:
        avg=float(basis_position(symbol).get("avg_cost_usd",0.0) or 0.0)
        if avg > 0:
            return avg
    except Exception:
        pass
    try:
        return float(perf.get("positions",{}).get(symbol,{}).get("avg_cost_usd",0.0) or 0.0)
    except Exception:
        return 0.0

def cost_basis_meta(symbol, perf):
    try:
        p=basis_position(symbol)
        if p.get("avg_cost_usd",0):
            return {"estimated": bool(p.get("estimated", False)), "source": p.get("source", "cost_basis")}
    except Exception:
        pass
    try:
        p=perf.get("positions",{}).get(symbol,{})
        return {"estimated": bool(p.get("estimated", False)), "source": p.get("source", "performance_tracker")}
    except Exception:
        return {"estimated": False, "source": "unknown"}

def price_for(symbol, market, snapshot):
    try:
        price=float(market.get("price_usd",0) or 0)
    except Exception:
        price=0.0
    if price <= 0:
        held=snapshot.get("assets",{}).get(symbol,{})
        amt=held.get("amount",0.0)
        price=(held.get("native_usd",0.0)/amt) if amt>0 else 0.0
    return price

def profit_state(symbol, market, perf, snapshot):
    price=price_for(symbol, market, snapshot)
    avg=avg_cost_for(symbol, perf)
    min_profit, stop_loss = risk_levels_for(symbol)
    if avg <= 0 or price <= 0:
        return {"known":False,"estimated":False,"source":"unknown","price":price,"avg_cost":avg,"profit_pct":0.0,"profit_ok":False,"stop_loss":False}
    pct=(price-avg)/avg
    meta=cost_basis_meta(symbol, perf)
    return {"known":True,"estimated":meta.get("estimated",False),"source":meta.get("source","cost_basis"),"price":price,"avg_cost":avg,"profit_pct":pct,"profit_ok":pct>=min_profit,"stop_loss":pct<=-stop_loss}

def buy_allowed(symbol, snapshot):
    asset=snapshot.get("assets",{}).get(symbol,{})
    weight=asset.get("weight",0.0)
    max_weight=asset.get("max_weight",asset_max_weight(symbol))
    if weight >= max_weight:
        return False, f"{symbol} sobreponderado {weight*100:.1f}% >= limite {max_weight*100:.1f}%"
    return True, ""

def btc_rotation_allowed(snapshot):
    btc=snapshot.get("assets",{}).get("BTC",{})
    min_weight=asset_min_weight("BTC")
    if btc.get("weight",0.0) <= min_weight:
        return False, f"BTC bajo minimo {btc.get('weight',0.0)*100:.1f}% <= {min_weight*100:.1f}%"
    return True, ""

def trend_score(market):
    try:
        d=float(market.get("change_24h",0) or 0)
    except Exception:
        d=0.0
    try:
        w=float(market.get("change_1w",0) or 0)
    except Exception:
        w=0.0
    try:
        rank=float(market.get("rank",999) or 999)
    except Exception:
        rank=999.0
    # Momentum score: weekly trend weighted more, 24h confirms, rank lightly favors liquidity.
    liquidity=max(0.0, (100.0-rank)/100.0)
    return (w*0.60) + (d*0.35) + liquidity

def candidate_score(candidate, market):
    return trend_score(market) + confidence_value(candidate.get("confidence",0.0))*10.0

def recent_trade_block(symbol, action):
    try:
        cooldown=int(os.getenv("PER_ASSET_COOLDOWN_SECONDS","3600"))
    except ValueError:
        cooldown=3600
    try:
        reversal_cooldown=int(os.getenv("REVERSAL_COOLDOWN_SECONDS", str(max(cooldown, 3600))))
    except ValueError:
        reversal_cooldown=max(cooldown, 3600)
    try:
        error_cooldown=int(os.getenv("EXECUTION_ERROR_COOLDOWN_SECONDS","900"))
    except ValueError:
        error_cooldown=900
    requested_action=str(action or "").upper()
    now=time.time()
    for event in reversed(read_events(500)):
        name=event.get("event")
        data=event.get("data",{}) if isinstance(event.get("data"),dict) else {}
        if str(data.get("symbol","")).upper() != symbol.upper():
            continue
        if name == "execution_skipped" and error_cooldown > 0:
            try:
                age=now-float(event.get("epoch",0) or 0)
            except (TypeError, ValueError):
                age=error_cooldown
            if age < error_cooldown:
                return True, f"{symbol} bloqueado por error reciente {int(error_cooldown-age)}s"
        if name != "trade_executed":
            continue
        signal=data.get("signal",{}) if isinstance(data.get("signal"),dict) else {}
        previous_action=str(signal.get("action","")).upper()
        try:
            age=now-float(event.get("epoch",0) or 0)
        except (TypeError, ValueError):
            age=max(cooldown, reversal_cooldown)
        if previous_action == requested_action and cooldown > 0 and age < cooldown:
            return True, f"{symbol} {requested_action} cooldown activo {int(cooldown-age)}s"
        if previous_action and previous_action != requested_action and reversal_cooldown > 0 and age < reversal_cooldown:
            return True, f"{symbol} reversa {previous_action}->{requested_action} bloqueada {int(reversal_cooldown-age)}s"
        if previous_action == requested_action:
            return False, ""
    return False, ""

def strong_sell_override_allowed(signal, market, asset, pstate, overweight, above_target):
    """Allow a bounded non-profit sell only when the AI signal is strong enough."""
    if os.getenv("ALLOW_STRONG_AI_SELLS_WITHOUT_PROFIT", "YES").upper() != "YES":
        return False
    if str(signal.get("action", "")).upper() != "SELL":
        return False
    try:
        confidence = float(signal.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < env_float("STRONG_AI_SELL_CONFIDENCE", "0.88"):
        return False
    if pstate.get("stop_loss") or overweight or above_target:
        return True
    if not pstate.get("known"):
        return True
    try:
        change_24h = float(market.get("change_24h", 0) or 0)
    except (TypeError, ValueError):
        change_24h = 0.0
    try:
        change_1w = float(market.get("change_1w", 0) or 0)
    except (TypeError, ValueError):
        change_1w = 0.0
    try:
        weight = float(asset.get("weight", 0.0) or 0.0)
    except (TypeError, ValueError):
        weight = 0.0
    bearish_enough = (
        change_24h <= env_float("STRONG_AI_SELL_MAX_24H_PCT", "1.0")
        or change_1w <= env_float("STRONG_AI_SELL_MAX_1W_PCT", "2.0")
    )
    meaningful_position = weight >= env_float("STRONG_AI_SELL_MIN_WEIGHT", "0.05")
    return bearish_enough and meaningful_position

def sell_high_policy_allowed(symbol, signal, market, asset, pstate, overweight, above_target):
    """Global buy-low/sell-high guard for real execution.

    Normal sells should realize profit over average cost. Selling below cost is
    reserved for explicit defense (stop-loss/emergency) or dust cleanup, not for
    routine rebalance/take-profit signals.
    """
    if os.getenv("BUY_LOW_SELL_HIGH_POLICY", "YES").upper() != "YES":
        return True, ""
    strategy=str(signal.get("strategy","")).upper()
    if strategy == "DUST_SWEEP":
        return True, ""
    if pstate.get("stop_loss"):
        return os.getenv("ALLOW_DEFENSIVE_STOP_SELLS", "YES").upper() == "YES", "stop defensivo deshabilitado"
    if pstate.get("profit_ok"):
        return True, ""
    if not pstate.get("known"):
        if os.getenv("ALLOW_SELL_WITH_UNKNOWN_COST", "NO").upper() == "YES" and (overweight or above_target):
            return True, ""
        return False, f"{symbol} costo promedio desconocido; no se vende sin confirmar ganancia"
    if (overweight or above_target) and os.getenv("ALLOW_REBALANCE_SELLS_WITHOUT_PROFIT", "NO").upper() == "YES":
        return True, ""
    profit_pct=float(pstate.get("profit_pct",0.0) or 0.0)
    min_profit, _stop = risk_levels_for(symbol)
    return False, f"{symbol} venta bloqueada: profit {profit_pct*100:.2f}% menor a minimo {min_profit*100:.2f}%"

def chase_limit_for(symbol):
    """Subida en 24h a partir de la cual se considera que el activo ya corrio.

    Un limite unico para todos repite el error del stop fijo: 2.5% bloqueaba el
    20-25% de los dias verdes en BTC/ETH pero el 47-60% en SOL, CRO, POL y LINK,
    donde 2.5% es un dia normal. El worker manda el percentil 75 de las subidas
    de cada activo, asi se descarta solo el cuartil alto -un pico de verdad- y
    no medio mes de dias corrientes. Sin mapa, cae al valor global de siempre.
    """
    sym=(symbol or "").strip().upper()
    lim=env_pct_map("BUY_MAX_24H_CHASE_PCT_BY_ASSET").get(sym)
    if lim is None:
        lim=env_float("BUY_MAX_24H_CHASE_PCT","2.5")
    return min(max(lim, 1.0), 12.0)

def buy_low_policy_allowed(symbol, market, signal):
    """Avoid chasing high short-term pumps unless confidence/trend justify it."""
    if os.getenv("BUY_LOW_SELL_HIGH_POLICY", "YES").upper() != "YES":
        return True, ""
    try:
        d=float(market.get("change_24h",0) or 0)
    except (TypeError, ValueError):
        d=0.0
    try:
        w=float(market.get("change_1w",0) or 0)
    except (TypeError, ValueError):
        w=0.0
    confidence=confidence_value(signal.get("confidence",0.0), 0.0)
    max_24h=chase_limit_for(symbol)
    breakout_conf=env_float("BUY_BREAKOUT_MIN_CONFIDENCE","0.82")
    if d >= max_24h and not (w > 0 and confidence >= breakout_conf):
        return False, f"{symbol} compra bloqueada: subida 24h {d:.2f}% >= {max_24h:.2f}%; evitar comprar alto"
    if d > 0.8 and w < -1.0:
        return False, f"{symbol} compra bloqueada: rebote corto contra tendencia semanal {w:.2f}%"
    return True, ""

def normalize_ai_opportunity(op):
    if not isinstance(op, dict):
        return {"action":"HOLD","confidence":0.0,"reason":"respuesta IA invalida"}
    out=dict(op)
    action=str(out.get("action", "HOLD")).upper().strip()
    if action not in {"BUY", "SELL", "HOLD"}:
        action="HOLD"
        out["reason"]=(str(out.get("reason") or "") + "; accion IA invalida").strip("; ")
    out["action"]=action
    out["confidence"]=confidence_value(out.get("confidence"), 0.0)
    return out

def calibrated(signal, market):
    try:
        d=float(market.get("change_24h",0)); w=float(market.get("change_1w",0)); raw=confidence_value(signal.get("confidence",0.0))
    except (TypeError,ValueError): d=w=0.0; raw=0.0
    action=signal.get("action") if signal.get("action") in {"BUY","SELL","HOLD"} else "HOLD"
    if action=="SELL":
        if d <= -1.0: confidence=0.72; reason="salida por ruptura bajista 24h"
        elif d >= 0.6 and w >= -1.0: confidence=0.66; reason="toma parcial de ganancia por impulso positivo"
        else: confidence=0.45; reason="venta sin confirmacion suficiente"
    elif action=="BUY":
        if d>0 and w>0: confidence=0.80; reason="tendencia 24h y semanal positivas"
        elif d>=0 and w>-5: confidence=0.68; reason="tendencia corta positiva y semanal dentro de límite"
        elif -1.5 <= d < 0 and w > -4 and raw >= 0.65: confidence=0.62; reason="entrada DCA en retroceso controlado"
        else: confidence=0.45; reason="tendencia y retroceso fuera de límites"
    else:
        confidence=0.45; reason="sin oportunidad validada"
    return {
        "action":action,
        "confidence":confidence,
        "strategy":signal.get("strategy","TREND"),
        "reason":reason,
        "source":("USDT" if selected_exchange() == "coinex" else "USDC"),
        "origin":signal.get("origin", "technical"),
        "ai_provider":signal.get("ai_provider"),
        "ai_model":signal.get("ai_model"),
    }

def sell_signal(symbol, market):
    try:
        d=float(market.get("change_24h",0)); w=float(market.get("change_1w",0))
    except (TypeError,ValueError):
        d=w=0.0
    if d <= -1.0:
        return {"symbol":symbol,"action":"SELL","confidence":0.72,"strategy":"PRICE_TRACKER","reason":"salida por ruptura bajista 24h"}
    if d >= 0.6 and w >= -1.0:
        return {"symbol":symbol,"action":"SELL","confidence":0.66,"strategy":"PRICE_TRACKER","reason":"toma parcial de ganancia por impulso positivo"}
    return {"symbol":symbol,"action":"HOLD","confidence":0.0,"strategy":"HOLD","reason":"sin condicion de venta"}

def reserve_ratio():
    try:
        return float(os.getenv("BASE_RESERVE_RATIO","0.40"))
    except ValueError:
        return 0.40

def reserve_pct_text():
    return f"{reserve_ratio()*100:.0f}%"

def max_base_trade_amount():
    configured=float(os.getenv("MAX_TRADE_USDC","2"))
    if selected_exchange() == "crypto_com":
        configured=min(configured, float(os.getenv("CRYPTO_COM_MAX_TRADE_USDC","2")))
    return configured

def capital_status():
    usdc=available("USDC")
    ratio=reserve_ratio()
    total=float(usdc.get("amount",0.0) or 0.0)
    reserve=total*ratio
    free=max(0.0,total-reserve)
    return {"currency":"USDC","total":total,"reserve_ratio":ratio,"reserved":reserve,"free":free}

def free_usdc_amount():
    return capital_status()["free"]

def free_btc_amount():
    btc=available("BTC")
    reserve=btc.get("amount",0.0)*reserve_ratio()
    return max(0.0, btc.get("amount",0.0)-reserve), btc



def dust_sweep_enabled():
    return os.getenv("DUST_SWEEP_TO_USDC", "0").strip().upper() in {"1", "YES", "TRUE", "ON"}

def dust_sweep_recent(symbol):
    try:
        interval_hours=float(os.getenv("DUST_SWEEP_INTERVAL_HOURS", "24"))
    except (TypeError, ValueError):
        interval_hours=24.0
    if interval_hours <= 0:
        return False, ""
    now=time.time()
    for event in reversed(read_events(800)):
        name=event.get("event")
        data=event.get("data",{}) if isinstance(event.get("data"),dict) else {}
        if str(data.get("symbol", "")).upper() != symbol.upper():
            continue
        if name not in {"trade_executed", "dust_sweep_error", "dust_sweep_block"}:
            continue
        signal=data.get("signal",{}) if isinstance(data.get("signal"),dict) else {}
        if name == "trade_executed" and signal.get("strategy") != "DUST_SWEEP":
            continue
        try:
            age=now-float(event.get("epoch",0) or 0)
        except (TypeError, ValueError):
            age=interval_hours*3600
        remaining=interval_hours*3600-age
        if remaining > 0:
            return True, f"barrido reciente; faltan {int(remaining)}s"
        return False, ""
    return False, ""

def pick_dust_sweep(snapshot):
    if not dust_sweep_enabled():
        return None, None, 0.0
    if selected_exchange() != "crypto_com":
        return None, None, 0.0
    max_value=env_float("DUST_MAX_VALUE_USD", "1")
    min_value=env_float("DUST_MIN_VALUE_USD", "0.05")
    excluded={x.strip().upper() for x in os.getenv("DUST_SWEEP_EXCLUDE", "USDC,USDT,USD,BTC,ETH").split(",") if x.strip()}
    candidates=[]
    for symbol, asset in snapshot.get("assets", {}).items():
        symbol=str(symbol).upper()
        if symbol in excluded:
            continue
        amount=float(asset.get("amount",0.0) or 0.0)
        value=float(asset.get("native_usd",0.0) or 0.0)
        if amount <= 0 or value < min_value or value > max_value:
            continue
        recent, reason=dust_sweep_recent(symbol)
        if recent:
            log_event("dust_sweep_block", symbol=symbol, value_usd=value, reason=reason)
            continue
        candidates.append((value, symbol, amount))
    if not candidates:
        return None, None, 0.0
    value, symbol, amount=sorted(candidates, key=lambda x: x[0], reverse=True)[0]
    signal={
        "action":"SELL",
        "confidence":0.90,
        "strategy":"DUST_SWEEP",
        "reason":f"barrido de polvo a USDC: valor aprox USD {value:.2f}",
        "source":symbol,
        "origin":"dust_sweep",
    }
    log_event("dust_sweep_candidate", symbol=symbol, amount=amount, value_usd=value, signal=signal)
    return symbol, signal, amount

def configured_conditional_orders():
    raw=os.getenv("CONDITIONAL_ORDERS_JSON","").strip()
    if not raw:
        return []
    try:
        data=json.loads(raw)
    except json.JSONDecodeError as exc:
        log_event("conditional_order_config_error", error=str(exc))
        return []
    return data if isinstance(data, list) else []

def configured_force_sell_once():
    raw=os.getenv("FORCE_SELL_ONCE_JSON","").strip()
    if not raw:
        return None
    try:
        data=json.loads(raw)
    except json.JSONDecodeError as exc:
        log_event("force_sell_config_error", error=str(exc))
        return None
    if not isinstance(data, dict):
        return None
    symbol=str(data.get("symbol","")).upper().strip()
    if not symbol:
        return None
    return data

def pick_force_sell_once(items, snapshot):
    force=configured_force_sell_once()
    if not force:
        return None, None, 0.0
    symbol=str(force.get("symbol","")).upper().strip()
    if symbol not in items:
        log_event("force_sell_block", symbol=symbol, reason="symbol_not_in_market")
        return None, None, 0.0
    try:
        value_usd=float(force.get("value_usd",0) or 0)
        price=float(items.get(symbol,{}).get("price_usd",0) or 0)
    except (TypeError, ValueError):
        value_usd=0.0
        price=0.0
    held=snapshot.get("assets",{}).get(symbol,{"amount":0.0,"native_usd":0.0})
    if price <= 0 or value_usd <= 0 or held.get("amount",0.0) <= 0:
        log_event("force_sell_block", symbol=symbol, value_usd=value_usd, price_usd=price, reason="invalid_price_amount_or_balance")
        return None, None, 0.0
    amount=min(float(held.get("amount",0.0) or 0.0), value_usd / price)
    if amount <= 0:
        log_event("force_sell_block", symbol=symbol, value_usd=value_usd, reason="amount_zero")
        return None, None, 0.0
    pstate=profit_state(symbol, items.get(symbol,{}), decision_context(), snapshot)
    signal={
        "action":"SELL",
        "confidence":0.95,
        "strategy":"FORCED_LOSS_SELL",
        "origin":"user_authorized_force_sell",
        "reason":str(force.get("reason") or f"venta forzada autorizada por usuario para {symbol} por USD {value_usd:.2f}, aunque exista perdida"),
        "source":symbol,
        "force_sell_once_id":str(force.get("id","")),
        "profit_state":pstate,
        "allow_loss_sell":True,
    }
    log_event("force_sell_candidate", symbol=symbol, amount=amount, value_usd=value_usd, signal=signal)
    return symbol, signal, amount

def cloud_execution_mode_allows(signal):
    """Re-read Cloudflare authority immediately before a real order.

    A cycle can spend a few seconds calling the model.  This final check makes
    a SEMI/AUTO change effective even if it happened after the cycle started.
    Local runs have no Cloudflare URL and retain their existing behavior.
    """
    url=os.getenv("CLOUDFLARE_API_URL", "").rstrip("/")
    token=os.getenv("RUNNER_TOKEN") or os.getenv("CLOUDFLARE_RUNNER_TOKEN")
    if not url:
        return True, "local_execution_boundary"
    if not token:
        return False, "cloud authority unavailable: runner token missing"
    try:
        request=urllib.request.Request(
            f"{url}/api/v1/runner/next",
            headers={"Accept":"application/json", "Authorization":f"Bearer {token}", "User-Agent":"crypto-bot-execution-check"},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            data=json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return False, f"cloud authority check failed: {exc}"
    if not isinstance(data, dict) or data.get("desired_state") != "running":
        return False, "cloud motor is not running"
    mode=str(data.get("execution_mode", "semi")).lower()
    is_accepted_conditional=signal.get("origin") == "conditional_order" and bool(signal.get("conditional_order_id"))
    if mode == "auto" or is_accepted_conditional:
        return True, f"cloud mode {mode}"
    return False, "modo semiautomatico: señal IA requiere aceptación como orden condicionada"

def pick_conditional_order(items, snapshot):
    orders=configured_conditional_orders()
    if not orders:
        return None, None, 0.0
    for order in orders:
        if not isinstance(order, dict):
            continue
        order_id=str(order.get("id","")).strip()
        symbol=str(order.get("symbol","")).upper().strip()
        side=str(order.get("side","")).upper().strip()
        if not order_id or symbol not in items or side not in {"BUY","SELL"}:
            continue
        market=items.get(symbol,{})
        try:
            price=float(market.get("price_usd",0) or 0)
            trigger=float(order.get("trigger_price_usd",0) or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or trigger <= 0:
            continue
        touched=(side=="BUY" and price <= trigger) or (side=="SELL" and price >= trigger)
        if not touched:
            log_event("conditional_order_watch", conditional_order_id=order_id, symbol=symbol, side=side, price_usd=price, trigger_price_usd=trigger)
            continue
        blocked, block_reason=recent_trade_block(symbol, side)
        if blocked:
            log_event("conditional_order_block", conditional_order_id=order_id, symbol=symbol, side=side, reason=block_reason)
            continue
        if side=="BUY":
            amount=float(order.get("amount_usdc",0) or 0)
            if amount <= 0:
                continue
            free=free_usdc_amount()
            if amount > free:
                log_event("conditional_order_block", conditional_order_id=order_id, symbol=symbol, side=side, reason=f"USDC libre insuficiente {free:.2f} < orden {amount:.2f}")
                continue
            allowed, reason=buy_allowed(symbol, snapshot)
            if not allowed:
                log_event("conditional_order_block", conditional_order_id=order_id, symbol=symbol, side=side, reason=reason)
                continue
            signal={"action":"BUY","confidence":0.91,"strategy":"CONDITIONAL_ORDER","reason":f"orden condicionada {order_id}: precio {price:.8g} <= trigger {trigger:.8g}","source":"USDC","origin":"conditional_order","conditional_order_id":order_id}
            return symbol, signal, amount
        held=snapshot.get("assets",{}).get(symbol,{"amount":0.0,"native_usd":0.0})
        value_usd=float(order.get("value_usd",0) or 0)
        if value_usd <= 0 or held.get("native_usd",0.0) <= 0:
            continue
        amount=min(float(held.get("amount",0.0) or 0.0), value_usd / price)
        asset=snapshot.get("assets",{}).get(symbol,{})
        pstate=profit_state(symbol, market, decision_context(), snapshot)
        if order_id.startswith("grid-"):
            # Un grid cosecha oscilaciones de horas. Exigirle el objetivo de la
            # tesis larga (dimensionado a varios dias de rango) lo deja comprando
            # en cada caida sin poder vender nunca: solo saldria por stop.
            # Su minimo propio solo tiene que cubrir el coste de ida y vuelta.
            grid_min=env_float("GRID_MIN_PROFIT_PCT", "0.025")
            pct=float(pstate.get("profit_pct", 0.0) or 0.0)
            pstate=dict(pstate, profit_ok=bool(pstate.get("known")) and pct >= grid_min)
        target_weight=asset.get("target_weight", asset_target_weight(symbol))
        overweight=asset.get("weight",0.0) > asset.get("max_weight",asset_max_weight(symbol))
        above_target=asset.get("weight",0.0) > target_weight
        signal={"action":"SELL","confidence":0.91,"strategy":"CONDITIONAL_ORDER","reason":f"orden condicionada {order_id}: precio {price:.8g} >= trigger {trigger:.8g}","source":symbol,"origin":"conditional_order","conditional_order_id":order_id,"profit_state":pstate}
        ok, reason=sell_high_policy_allowed(symbol, signal, market, asset, pstate, overweight, above_target)
        if not ok:
            log_event("conditional_order_block", conditional_order_id=order_id, symbol=symbol, side=side, reason=reason, profit_state=pstate)
            continue
        return symbol, signal, amount
    return None, None, 0.0

def pick_authorized_order(items):
    """Return only app-authorized execution candidates.

    These are not free-form AI trades: they are either a one-shot forced sell
    explicitly approved by the user or an active conditional order already
    accepted in the app.  Keeping this separate lets those orders remain live
    even when the AI provider has a temporary error, while the normal AI
    autopilot stays fail-closed.
    """
    snapshot=portfolio_snapshot(items)
    items.update(include_held_assets(items, snapshot))
    log_event("portfolio_snapshot", total_usd=snapshot.get("total_usd",0.0), assets=snapshot.get("assets",{}))
    force_symbol, force_signal, force_amount=pick_force_sell_once(items, snapshot)
    if force_symbol and force_signal and force_amount > 0:
        return force_symbol, force_signal, force_amount
    conditional_symbol, conditional_signal, conditional_amount=pick_conditional_order(items, snapshot)
    if conditional_symbol and conditional_signal and conditional_amount > 0:
        return conditional_symbol, conditional_signal, conditional_amount
    return None, None, 0.0

def pick_signal(opps, items, perf=None, adaptive=None):
    perf=perf or {}
    adaptive=adaptive or adaptive_status()
    authorized_symbol, authorized_signal, authorized_amount=pick_authorized_order(items)
    if authorized_symbol and authorized_signal and authorized_amount > 0:
        return authorized_symbol, authorized_signal, authorized_amount
    snapshot=portfolio_snapshot(items)
    autopilot_mode=str(os.getenv("TRADING_AUTOPILOT_MODE","SEMI")).upper().strip()
    if autopilot_mode not in {"AUTO","AUTOMATIC","PILOT"}:
        reason="modo semiautomatico: IA/supervisor propone, pero solo se ejecutan ordenes condicionadas aceptadas en la app"
        log_event("semiauto_hold", reason=reason, active_conditional_orders=len(configured_conditional_orders()))
        return None, {"action":"HOLD","confidence":0.0,"strategy":"SEMI_AUTO","reason":reason,"source":"USDC"}, 0.0
    avoid=set(perf.get("avoid_rebuy_symbols", []))
    opps=[normalize_ai_opportunity(x) for x in opps]
    trendy=[]
    for x in opps:
        sym=x.get("symbol")
        if not sym or sym not in items:
            continue
        # No preference by asset: BUY signals are ranked by trend; strong HOLD assets can become candidates only if technical fallback is enabled upstream.
        if x.get("action")=="BUY":
            trendy.append(x)
    buys=sorted(trendy, key=lambda x: candidate_score(x, items.get(x.get("symbol"),{})), reverse=True)
    max_usdc=min(max_base_trade_amount(), float(adaptive.get("max_trade_usdc", max_base_trade_amount()) or 0.0))
    min_usdc=float(os.getenv("MIN_TRADE_USDC","5"))
    min_btc_native=float(os.getenv("MIN_BTC_TRADE_NATIVE_USD","1"))
    free_usdc=free_usdc_amount()
    low_capital_mode=free_usdc < min_usdc
    sell_candidates=[]
    for symbol, market in items.items():
        if symbol in {"USDC","USDT"}:
            continue
        held=snapshot.get("assets",{}).get(symbol,{"amount":0.0,"native_usd":0.0})
        min_native=float(os.getenv("MIN_SELL_NATIVE_USD", "5"))
        if selected_exchange() == "crypto_com":
            min_native=max(min_native, float(os.getenv("CRYPTO_COM_MIN_SELL_NATIVE_USD", "5")))
        if held.get("amount",0.0) <= 0 or held.get("native_usd",0.0) < min_native:
            continue
        candidate=sell_signal(symbol, market)
        signal=calibrated(candidate, market)
        asset=snapshot.get("assets",{}).get(symbol,{})
        overweight=asset.get("weight",0.0) > asset.get("max_weight",asset_max_weight(symbol))
        pstate=profit_state(symbol, market, perf, snapshot)
        target_weight=asset.get("target_weight", asset_target_weight(symbol))
        above_target=asset.get("weight",0.0) > target_weight
        emergency_sell=bool(pstate.get("stop_loss"))
        if overweight and signal["action"]!="SELL":
            signal={"action":"SELL","confidence":0.70,"strategy":"REBALANCE","reason":f"rebalance: {symbol} pesa {asset.get('weight',0)*100:.1f}% sobre objetivo {target_weight*100:.1f}%","source":symbol}
        min_weight=asset.get("min_weight", asset_min_weight(symbol))
        if signal["action"]=="SELL" and min_weight > 0 and asset.get("weight",0.0) <= min_weight and not emergency_sell:
            log_event("allocation_block", symbol=symbol, action="SELL", reason=f"{symbol} en/bajo minimo {min_weight*100:.1f}%")
            continue
        if symbol == "CRO" and signal["action"]=="SELL" and not (above_target or emergency_sell):
            log_event("allocation_block", symbol=symbol, action="SELL", reason=f"CRO en/bajo objetivo {target_weight*100:.1f}%")
            continue
        if signal["action"]=="SELL" and signal["confidence"] >= 0.58:
            blocked, block_reason=recent_trade_block(symbol, "SELL")
            if blocked:
                log_event("asset_cooldown_block", symbol=symbol, action="SELL", reason=block_reason)
                continue
            if pstate["known"] and not (pstate["profit_ok"] or pstate["stop_loss"] or overweight or above_target or strong_sell_override_allowed(signal, market, asset, pstate, overweight, above_target)):
                log_event("profit_gate_block", symbol=symbol, reason="venta sin ganancia minima, sobrepeso ni stop-loss", profit_state=pstate)
                continue
            sell_policy_ok, sell_policy_reason=sell_high_policy_allowed(symbol, signal, market, asset, pstate, overweight, above_target)
            if not sell_policy_ok:
                log_event("sell_high_policy_block", symbol=symbol, action="SELL", reason=sell_policy_reason, signal=signal, profit_state=pstate, allocation={"overweight":overweight,"above_target":above_target})
                continue
            sell_fraction=float(os.getenv("SELL_FRACTION","0.5"))
            if symbol == "BTC":
                reserved_btc=held["amount"]*reserve_ratio()
                amount=min(held["amount"] * sell_fraction, max(0.0, held["amount"]-reserved_btc))
            else:
                amount=held["amount"] * sell_fraction
            try:
                price=float(market.get("price_usd",0) or 0)
            except (TypeError, ValueError):
                price=0.0
            sell_value_usd=amount*price if price>0 else held.get("native_usd",0.0)*sell_fraction
            if (overweight or above_target) and price>0 and snapshot.get("total_usd",0)>0:
                target_value=target_weight*snapshot["total_usd"]
                excess=max(0.0, asset.get("native_usd",0.0)-target_value)
                amount=min(amount, excess/price) if excess>0 else amount
                sell_value_usd=amount*price
            max_sell_value=env_float("MAX_SELL_NATIVE_USD","5")
            if price>0 and sell_value_usd > max_sell_value:
                amount=max_sell_value/price
                sell_value_usd=amount*price
            if selected_exchange() == "crypto_com" and sell_value_usd < min_native:
                required_amount = min_native / price if price > 0 else 0.0
                if (overweight or above_target) and required_amount > amount:
                    target_value = target_weight * snapshot.get("total_usd", 0)
                    excess = max(0.0, asset.get("native_usd", 0.0) - target_value)
                    if price > 0 and excess / price >= required_amount:
                        amount = required_amount
                        sell_value_usd = amount * price
                if sell_value_usd < min_native:
                    continue
            if amount > 0:
                signal["source"]=symbol
                signal["portfolio_weight"]=round(asset.get("weight",0.0),4)
                signal["profit_state"]=pstate
                priority=0
                if symbol != "BTC":
                    priority += 2
                if pstate.get("profit_ok"):
                    priority += 2
                if overweight or above_target:
                    priority += 1
                if low_capital_mode:
                    priority += 2
                if overweight or above_target:
                    return symbol, signal, amount
                sell_candidates.append((priority, symbol, signal, amount))
    if low_capital_mode and sell_candidates:
        sell_candidates.sort(key=lambda x: (x[0], x[2].get("confidence",0.0), x[2].get("profit_state",{}).get("profit_pct",0.0), x[2].get("portfolio_weight",0.0)), reverse=True)
        _, symbol, signal, amount = sell_candidates[0]
        return symbol, signal, amount
    for candidate in buys:
        symbol=candidate.get("symbol")
        if not symbol or symbol not in items or symbol in {"USDC","USDT"} or symbol in avoid:
            continue
        if symbol == "CRO" and os.getenv("CRO_BUY_DISABLED","YES").upper()=="YES":
            log_event("allocation_block", symbol=symbol, action="BUY", reason="CRO_BUY_DISABLED=YES; CRO solo se rebalancea por venta si excede objetivo")
            continue
        blocked, block_reason=recent_trade_block(symbol, "BUY")
        if blocked:
            log_event("asset_cooldown_block", symbol=symbol, action="BUY", reason=block_reason)
            continue
        allowed, reason=buy_allowed(symbol, snapshot)
        if not allowed:
            log_event("allocation_block", symbol=symbol, action="BUY", reason=reason)
            continue
        signal=calibrated(candidate, items[symbol])
        signal["trend_score"]=round(trend_score(items[symbol]),4)
        signal["reason"] += f"; ranking tendencia {signal['trend_score']}"
        buy_policy_ok, buy_policy_reason=buy_low_policy_allowed(symbol, items[symbol], signal)
        if not buy_policy_ok:
            log_event("buy_low_policy_block", symbol=symbol, action="BUY", reason=buy_policy_reason, signal=signal, market=items[symbol])
            continue
        if adaptive.get("halted"):
            log_event("adaptive_buy_block", symbol=symbol, action="BUY", reason="adaptive_halt", adaptive=adaptive)
            continue
        usdc_amount=min(max_usdc, free_usdc)
        min_conf=float(adaptive.get("min_confidence", 0.60) or 0.60)
        if signal["confidence"] < min_conf:
            log_event("adaptive_confidence_block", symbol=symbol, action="BUY", confidence=signal["confidence"], required=min_conf, adaptive=adaptive)
            continue
        if signal["confidence"] >= min_conf and usdc_amount >= min_usdc:
            signal["source"]="USDC"
            return symbol, signal, usdc_amount
        if symbol != "BTC":
            rot_ok, rot_reason=btc_rotation_allowed(snapshot)
            if not rot_ok:
                log_event("allocation_block", symbol=symbol, action="BUY_BTC_ROTATION", reason=rot_reason)
                continue
            btc_free, btc = free_btc_amount()
            btc_price=btc.get("native_usd",0.0)/btc.get("amount",1.0) if btc.get("amount",0.0)>0 else 0.0
            btc_trade=min(btc_free, max_usdc / btc_price) if btc_price>0 else 0.0
            if (not adaptive.get("halted") and signal["confidence"] >= max(0.68, min_conf) and btc_trade*btc_price >= min_btc_native):
                signal["source"]="BTC"
                signal["reason"] += f"; rotacion desde BTC con reserva {reserve_pct_text()}"
                return symbol, signal, btc_trade
    if sell_candidates:
        sell_candidates.sort(key=lambda x: (x[0], x[2].get("confidence",0.0), x[2].get("profit_state",{}).get("profit_pct",0.0), x[2].get("portfolio_weight",0.0)), reverse=True)
        _, symbol, signal, amount = sell_candidates[0]
        return symbol, signal, amount
    dust_symbol, dust_signal, dust_amount = pick_dust_sweep(snapshot)
    if dust_symbol and dust_signal and dust_amount > 0:
        return dust_symbol, dust_signal, dust_amount
    if free_usdc >= min_usdc:
        reason=f"capital libre {free_usdc:.2f} USDC; sin señal elegible por asignación, confianza o cooldown"
    else:
        reason=f"capital libre bajo mínimo sobre reserva {reserve_pct_text()} y sin venta elegible"
    return None, {"action":"HOLD","confidence":0.0,"strategy":"HOLD","reason":reason,"source":"USDC"}, 0.0
def print_ai_response(report):
    universe=report.get("universe", [])
    ai=report.get("ai", {}) if isinstance(report.get("ai"), dict) else {}
    opps=ai.get("opportunities", []) if isinstance(ai.get("opportunities"), list) else []
    print("\nRESPUESTA IA")
    if ai.get("error"):
        print(f"IA error: {ai.get('error')}")
    market={x.get("symbol"):x for x in universe if isinstance(x,dict)}
    for op in opps[:12]:
        if not isinstance(op, dict):
            continue
        sym=str(op.get("symbol","?")).upper()
        row=market.get(sym,{})
        nop=normalize_ai_opportunity(op)
        print(f"- {sym}: {nop.get('action','HOLD')} conf={nop.get('confidence',0):.2f} precio=${row.get('price_usd','-')} 24h={row.get('change_24h','-')}% 1w={row.get('change_1w','-')}% · {nop.get('reason','')}")
    if not opps:
        print("- sin oportunidades devueltas")

def main():
    load_env_file()
    instance_lock = acquire_file_lock(INSTANCE_LOCK_PATH, "worker", wait_seconds=0.0)
    if instance_lock is None:
        owner=_read_lock_metadata(INSTANCE_LOCK_PATH)
        owner_text=f"pid={owner.get('pid','?')}" if owner else "instancia activa"
        print(f"AUTO: otra instancia de crypto-auto ya está activa ({owner_text})", flush=True)
        log_event("auto_instance_skip", reason="instance_lock_active", owner=owner)
        return 0
    feedback=feedback_summary(120)
    perf=decision_context()
    adaptive=adaptive_status()
    ai_feedback=adaptive_feedback_context()
    log_event("adaptive_risk", **adaptive)
    log_event("auto_cycle_start", mode="AUTO_EXECUTE" if os.getenv("AUTO_EXECUTE")=="YES" else "ARMED", feedback=feedback, performance=perf, adaptive_risk=adaptive)
    state=load_state()
    freq=frequency_gate(state)
    frequency_blocked=not freq["approved"]
    evaluate_during_cooldown=os.getenv("EVALUATE_DURING_COOLDOWN","YES").upper()=="YES"
    if frequency_blocked:
        print(json.dumps({"mode":"AUTO_EXECUTE" if os.getenv("AUTO_EXECUTE")=="YES" else "ARMED","frequency_gate":freq},indent=2))
        log_event("frequency_block", frequency_gate=freq)
        if not evaluate_during_cooldown:
            print("AUTO: bloqueado por limite de frecuencia")
            return 0
        print("AUTO: cooldown activo; se evaluara IA pero no se ejecutaran ordenes")
    cycle_trades=0
    max_cycle=int(os.getenv("MAX_TRADES_PER_CYCLE","1"))
    print("AUTO [10%] preparando escaneo...", flush=True)
    proc=None
    scan_stdout=None
    scan_stderr=None
    previous_sigterm=process_signal.getsignal(process_signal.SIGTERM)
    process_signal.signal(process_signal.SIGTERM, interrupt_scan)
    try:
        scan_env=os.environ.copy()
        scan_env["AI_FEEDBACK_CONTEXT"]=json.dumps(ai_feedback, ensure_ascii=False, separators=(",", ":"))
        # Do not leave the scanner writing into undrained PIPEs while we poll it.
        # A moderately verbose scan can fill a pipe and deadlock until the outer
        # timeout. Temporary files keep progress reporting without backpressure.
        scan_stdout=tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        scan_stderr=tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        proc=subprocess.Popen(
            ["./start-bot","--json"],
            cwd=ROOT,
            text=True,
            stdout=scan_stdout,
            stderr=scan_stderr,
            env=scan_env,
            start_new_session=True,
        )
        started=time.monotonic()
        scan_timeout=int(os.getenv("AUTO_SCAN_TIMEOUT_SECONDS","120"))
        while True:
            try:
                if proc.poll() is not None:
                    break
            except OSError as exc:
                terminate_process_group(proc)
                print(f"\nAUTO: el escáner no pudo comprobar su estado ({exc}); no se opera en este ciclo")
                log_event("scanner_process_error", detail=str(exc))
                return 0
            elapsed=int(time.monotonic()-started)
            if elapsed >= scan_timeout:
                terminate_process_group(proc)
                print("\nAUTO: escaneo agotó tiempo; no se opera en este ciclo")
                log_event("scan_timeout", timeout_seconds=scan_timeout)
                return 0
            pct=min(65,10+elapsed)
            print(f"\033[2K\rAUTO [{pct:02d}%] escaneando mercado e IA... {elapsed:03d}/{scan_timeout}s", end="", flush=True)
            time.sleep(1)
        proc.wait()
        scan_stdout.seek(0)
        scan_stderr.seek(0)
        stdout=scan_stdout.read()
        stderr=scan_stderr.read()
        returncode=proc.returncode
    except KeyboardInterrupt:
        terminate_process_group(proc)
        print("\nAUTO detenido. Regresando al menú.")
        return 130
    finally:
        process_signal.signal(process_signal.SIGTERM, previous_sigterm)
        for stream in (scan_stdout, scan_stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
    print("\nAUTO [70%] mercado recibido; validando señal...", flush=True)
    if returncode:
        detail=stderr.strip().splitlines()[-1][:240] if stderr.strip() else "sin detalle"
        print(f"AUTO: escáner no disponible (código {returncode}): {detail}")
        log_event("scanner_unavailable", returncode=returncode, detail=detail)
        print("AUTO: no se opera en este ciclo")
        return
    try: report=json.loads(stdout)
    except Exception:
        print('AUTO: respuesta inválida; no se opera')
        log_event('invalid_scanner_response')
        return
    print_ai_response(report)
    log_event("ai_response", ai=report.get('ai',{}), universe=report.get('universe',[]))
    ai_data=report.get('ai',{}) if isinstance(report.get('ai'),dict) else {}
    items={x.get('symbol'):x for x in report.get('universe',[]) if x.get('symbol')}
    opps=report.get('ai',{}).get('opportunities',[])
    preselected_authorized=None
    ai_error=bool(ai_data.get('error'))
    ai_partial_error=bool(ai_data.get('partial_errors'))
    require_ai=os.getenv("REQUIRE_AI_FOR_EXECUTION","YES").upper()=="YES"
    requested_provider=os.getenv("AI_PROVIDER", "groq").strip().lower()
    provider_used=str(ai_data.get("provider_used", "")).strip().lower()
    model_used=str(ai_data.get("model_used", "")).strip().lower()
    remote_ai_ok=provider_used == requested_provider and model_used not in {"", "technical"}
    if require_ai and (ai_error or ai_partial_error or not remote_ai_ok):
        if ai_error:
            block_reason=f"IA reporto error: {ai_data.get('error')}"
        elif ai_partial_error:
            block_reason="IA con errores parciales; ejecucion bloqueada por REQUIRE_AI_FOR_EXECUTION=YES"
        elif provider_used != requested_provider:
            block_reason=f"Proveedor IA invalido: recibido '{provider_used or 'vacio'}', requerido '{requested_provider}'"
        else:
            block_reason=f"Modelo IA invalido o ausente: recibido '{model_used or 'vacio'}'"
        print(f"AUTO: {block_reason}")
        log_event(
            "ai_execution_block",
            reason=block_reason,
            provider_used=provider_used or None,
            requested_provider=requested_provider,
            model_used=model_used or None,
            require_ai=require_ai,
            ai=ai_data,
        )
        authorized_symbol, authorized_signal, authorized_amount=pick_authorized_order(items)
        if authorized_symbol and authorized_signal and authorized_amount > 0:
            preselected_authorized=(authorized_symbol, authorized_signal, authorized_amount)
            print("AUTO: IA falló, pero se mantiene orden autorizada por app bajo risk-gate")
            log_event(
                "ai_execution_block_bypassed_for_authorized_order",
                reason=block_reason,
                symbol=authorized_symbol,
                amount=authorized_amount,
                signal=authorized_signal,
            )
        else:
            return 0
    for opportunity in ai_data.get("opportunities", []):
        if isinstance(opportunity, dict):
            opportunity["origin"] = provider_used or "unknown"
            opportunity["ai_provider"] = provider_used or None
            opportunity["ai_model"] = ai_data.get("model_used")
    if os.getenv("AUTO_FULL_SCAN_NOW","NO").upper()=="YES":
        universe_recommendation=recommend_universe(report, perf)
        if universe_recommendation.get("changed"):
            applied=apply_universe_recommendation(universe_recommendation)
            log_event("universe_autopilot", recommendation=universe_recommendation, applied=applied)
            if applied.get("applied"):
                print(f"AUTO: universo actualizado · include={applied.get('trade_include','')} exclude={applied.get('trade_exclude','')}")
    try:
        cap=capital_status()
        print(f"CAPITAL USDC: total={cap['total']:.4f} reserva={cap['reserved']:.4f} libre={cap['free']:.4f} ({cap['reserve_ratio']*100:.0f}%)")
        log_event("capital_status", **cap)
    except Exception as exc:
        print(f"CAPITAL USDC: no disponible ({exc})")
        log_event("capital_status_error", error=str(exc))
    if preselected_authorized:
        symbol, signal, amount = preselected_authorized
    else:
        symbol, signal, amount = pick_signal(opps, items, perf, adaptive)
    # pick_signal refreshes/logs the portfolio snapshot. Recalculate risk from
    # that fresh equity before any decision can reach the exchange.
    adaptive=adaptive_status()
    log_event("adaptive_risk", **adaptive)
    if adaptive.get("hard_loss_halt"):
        loss=float(adaptive.get("loss_usd",0) or 0)
        baseline=float(adaptive.get("baseline_equity",0) or 0)
        resume=float((adaptive.get("thresholds") or {}).get("resume_loss_usd",0.25) or 0.25)
        pstate=signal.get("profit_state", {}) if isinstance(signal, dict) else {}
        recovery_sells_enabled=os.getenv("RECOVERY_SELLS_DURING_HALT", "1").strip().upper() in {"1", "YES", "TRUE", "ON"}
        recovery_sell=(
            recovery_sells_enabled
            and isinstance(signal, dict)
            and signal.get("action") == "SELL"
            and signal.get("strategy") != "DUST_SWEEP"
            and (pstate.get("profit_ok") or pstate.get("stop_loss") or signal.get("strategy") == "REBALANCE")
        )
        if not recovery_sell:
            hold_signal={
                "action":"HOLD",
                "confidence":1.0,
                "strategy":"MAX_DRAWDOWN_USD",
                "origin":"risk_engine",
                "reason":f"Freno de cartera activo: perdida USD {loss:.2f} desde maximo USD {baseline:.2f}; sin compras ni polvo; solo ventas defensivas hasta quedar a USD {resume:.2f} o menos del maximo.",
            }
            print(f"AUTO: HOLD DE SEGURIDAD · {hold_signal['reason']}")
            log_event("decision_hold", signal=hold_signal, adaptive_risk=adaptive)
            return 0
        signal["reason"] = str(signal.get("reason", "")) + "; venta permitida en modo recuperacion/mitigacion"
        log_event("recovery_sell_allowed", symbol=symbol, amount=amount, signal=signal, adaptive_risk=adaptive)
    if not symbol or signal.get('action')=='HOLD':
        print(f"AUTO: HOLD · {signal.get('reason','sin oportunidad')}")
        log_event("decision_hold", signal=signal)
        return
    gate=validate(signal,items.get(symbol,{}),amount,float(os.getenv('DAILY_LOSS_USDC','0')))
    print("AUTO [90%] risk gate validado")
    decision={'mode':'AUTO_EXECUTE' if os.getenv('AUTO_EXECUTE')=='YES' else 'ARMED','symbol':symbol,'amount':amount,'signal':signal,'risk_gate':gate,'adaptive_risk':adaptive}
    print(json.dumps(decision,indent=2))
    log_event("decision", **decision)
    if not gate['approved']:
        print('AUTO [100%]: bloqueado por risk gate')
        log_event('risk_gate_block', symbol=symbol, amount=amount, signal=signal, risk_gate=gate)
        return
    risk_reducing_sell=(signal.get("action")=="SELL" and adaptive.get("allow_risk_reducing_sells") and (signal.get("strategy")=="REBALANCE" or signal.get("profit_state",{}).get("stop_loss")))
    app_authorized_order=(
        signal.get("strategy")=="FORCED_LOSS_SELL"
        or (signal.get("origin")=="conditional_order" and bool(signal.get("conditional_order_id")))
    )
    if frequency_blocked and not risk_reducing_sell and not app_authorized_order:
        print('AUTO: senal evaluada; ejecucion bloqueada por cooldown/frecuencia')
        log_event('frequency_execution_block', symbol=symbol, amount=amount, signal=signal, risk_gate=gate, frequency_gate=freq)
        return
    if cycle_trades >= max_cycle:
        print('AUTO: bloqueado por MAX_TRADES_PER_CYCLE')
        log_event('cycle_trade_limit_block', max_trades_per_cycle=max_cycle)
        return
    if os.getenv('AUTO_EXECUTE')!='YES':
        print('AUTO: aprobado y armado; sin ejecución.')
        log_event('armed_no_execution', symbol=symbol, amount=amount, signal=signal)
        return
    force_loss_sell=signal.get("strategy")=="FORCED_LOSS_SELL" and signal.get("allow_loss_sell")
    if require_ai and not (force_loss_sell or app_authorized_order) and (ai_error or ai_partial_error or not remote_ai_ok):
        print('AUTO: bloqueo final de seguridad: IA fallida, no se cotiza ni confirma orden')
        log_event('ai_final_execution_block', symbol=symbol, amount=amount, signal=signal)
        return
    mode_allowed, mode_reason=cloud_execution_mode_allows(signal)
    if not mode_allowed:
        print(f"AUTO: ejecución bloqueada por autoridad actual: {mode_reason}")
        log_event('execution_mode_block', symbol=symbol, amount=amount, signal=signal, reason=mode_reason)
        return
    execution_lock = acquire_execution_lock()
    if execution_lock is None:
        owner=_read_lock_metadata(EXECUTION_LOCK_PATH)
        owner_text=f"pid={owner.get('pid','?')}" if owner else "otra instancia activa"
        print(f"AUTO: ejecución omitida; lock de orden ocupado ({owner_text})", flush=True)
        log_event("execution_skipped", reason="execution_lock_active", owner=owner)
        return 0
    try:
        if signal['action']=='BUY':
            q=quote_buy(symbol, amount, signal.get('source','USDC'))
        elif signal['action']=='SELL':
            q=quote_sell(symbol, amount)
        else:
            print('AUTO: HOLD; sin ejecución')
            return 0
        result=confirm(q['id'])
        safe_result=redact(result)
        status=str(result.get("status","")).lower() if isinstance(result,dict) else ""
        if status not in {"done","completed","success"}:
            print(f"AUTO: confirmación de orden no concluyente (status={status or 'missing'})")
            log_event("trade_confirmation_uncertain", symbol=symbol, amount=amount, signal=signal, status=status or "missing", result=safe_result)
            record_trade()
            return 0
        print(json.dumps(safe_result,indent=2))
        log_event("trade_executed", symbol=symbol, amount=amount, signal=signal, result=safe_result)
        if signal.get("strategy") == "FORCED_LOSS_SELL":
            log_event("forced_loss_sell_executed", force_sell_once_id=signal.get("force_sell_once_id"), symbol=symbol, amount=amount, signal=signal, result=safe_result)
        if signal.get("strategy") == "CONDITIONAL_ORDER" and signal.get("conditional_order_id"):
            log_event("conditional_order_executed", conditional_order_id=signal.get("conditional_order_id"), symbol=symbol, amount=amount, signal=signal, result=safe_result)
        try:
            record_cost_basis(result)
        except Exception as exc:
            log_event("cost_basis_update_error", error=str(exc), symbol=symbol)
        record_trade()
    except SystemExit as exc:
        print(f"AUTO: ejecución no realizada: {exc}")
        if 'signal' in locals() and isinstance(signal, dict) and signal.get("strategy") == "DUST_SWEEP":
            log_event("dust_sweep_error", error=str(exc), symbol=symbol if 'symbol' in locals() else None, amount=amount if 'amount' in locals() else None, signal=signal)
        else:
            log_event("execution_skipped", error=str(exc), symbol=symbol if 'symbol' in locals() else None)
        return 0
    finally:
        try:
            execution_lock.close()
        except Exception:
            pass
        try:
            instance_lock.close()
        except Exception:
            pass
        try:
            if os.path.exists(INSTANCE_LOCK_PATH):
                os.unlink(INSTANCE_LOCK_PATH)
        except Exception:
            pass
if __name__=='__main__': main()

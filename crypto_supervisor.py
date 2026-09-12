#!/usr/bin/env python3
"""Codex supervisor for the crypto bot.

This process reviews market snapshots, logs and portfolio context, then
updates safe configuration knobs such as the asset universe and a few
timing/risk parameters. Optional execution remains guarded by explicit flags.
"""
import json
import os
import signal
import subprocess
import tempfile
import time
import argparse

from bot_logger import log_event, feedback_summary, read_events
from performance_tracker import decision_context
from adaptive_risk import status as adaptive_status
from universe_autopilot import recommend as recommend_universe, apply_recommendation as apply_universe_recommendation

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")
MARKET_CACHE_PATH = os.path.join(ROOT, ".runtime", "latest-market.json")
AUTO_HEARTBEAT_PATH = os.path.join(ROOT, ".runtime", "crypto-auto-heartbeat.json")


def load_env_file():
    if not os.path.exists(ENV_PATH):
        return
    locked={"AUTO_EXECUTE","AUTO_LIVE","CONFIRM_LIVE","SUPERVISOR_EXECUTE"}
    with open(ENV_PATH, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key=key.strip()
            if key in locked and key in os.environ:
                continue
            os.environ[key]=value.strip().strip('"').strip("'")
def read_env():
    values = {}
    order = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key] = value
                    order.append(key)
    return values, order


def write_env(values, order):
    fd, temp = tempfile.mkstemp(prefix=".env.", dir=ROOT, text=True)
    try:
        seen = set()
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for key in order:
                if key in values and key not in seen:
                    f.write(f"{key}={values[key]}\n")
                    seen.add(key)
            for key, value in values.items():
                if key not in seen:
                    f.write(f"{key}={value}\n")
        os.chmod(temp, 0o600)
        os.replace(temp, ENV_PATH)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def env_flag(name, default="NO"):
    return os.getenv(name, default).upper() == "YES"


def env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


def terminate_process_group(proc, grace_seconds=5):
    if proc is None:
        return "", ""
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
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

def run_command(command, timeout, capture_output=False):
    proc=subprocess.Popen(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        env=os.environ.copy(),
        start_new_session=True,
    )
    try:
        stdout,stderr=proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_process_group(proc)
        raise
    except BaseException:
        terminate_process_group(proc)
        raise
    return subprocess.CompletedProcess(command,proc.returncode,stdout,stderr)


def interrupt_process(_signum, _frame):
    raise KeyboardInterrupt


def _event_detail_text(event):
    if not isinstance(event, dict):
        return ""
    data = event.get("data") or {}
    if isinstance(data, dict):
        try:
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return str(data)
    return str(data)


def _recent_event_details(event_name, limit=60):
    details = []
    for event in reversed(read_events(limit)):
        if event.get("event") == event_name:
            details.append(_event_detail_text(event))
    return details


def self_repair_params(feedback, adaptive):
    """Make small safe repairs when recurring runtime errors show up."""
    changes = {}
    reasons = []
    counts = feedback.get("counts", {}) if isinstance(feedback, dict) else {}
    last_block = feedback.get("last_block") if isinstance(feedback, dict) else None
    last_text = _event_detail_text(last_block)
    recent_scanner_errors = _recent_event_details("scanner_unavailable", 60)
    recent_timeout_errors = _recent_event_details("scan_timeout", 60)
    scanner_signature = recent_scanner_errors[0] if recent_scanner_errors else ""
    scanner_repeats = sum(1 for item in recent_scanner_errors if item == scanner_signature) if scanner_signature else 0
    error_text = " ".join(
        str(part)
        for part in [
            last_text,
            scanner_signature,
            json.dumps(feedback.get("last_decision", {}), ensure_ascii=False) if isinstance(feedback, dict) else "",
            json.dumps(feedback.get("counts", {}), ensure_ascii=False) if isinstance(feedback, dict) else "",
        ]
    )

    if scanner_repeats >= 2 or recent_timeout_errors:
        current_scan_timeout = env_int("AUTO_SCAN_TIMEOUT_SECONDS", 180)
        current_generate_timeout = env_int("OLLAMA_GENERATE_TIMEOUT_SECONDS", 150)
        current_model_timeout = env_int("OLLAMA_MODEL_TIMEOUT_SECONDS", 120)
        timeout_cap = max(120, min(env_int("AUTO_SCAN_TIMEOUT_HARD_MAX", 210), 240))
        new_scan_timeout = str(min(max(current_scan_timeout, 180), timeout_cap))
        new_generate_timeout = str(min(max(current_generate_timeout, 240), 360))
        new_model_timeout = str(min(max(current_model_timeout, 240), 360))
        if new_scan_timeout != str(current_scan_timeout):
            changes["AUTO_SCAN_TIMEOUT_SECONDS"] = new_scan_timeout
        if new_generate_timeout != str(current_generate_timeout):
            changes["OLLAMA_GENERATE_TIMEOUT_SECONDS"] = new_generate_timeout
        if new_model_timeout != str(current_model_timeout):
            changes["OLLAMA_MODEL_TIMEOUT_SECONDS"] = new_model_timeout
        changes.setdefault("AI_CHUNK_SIZE", "1")
        changes.setdefault("TRADING_SWARM_MAX_CHUNK", "1")
        reasons.append("scanner_retries")

    if "JSON invalido" in error_text or "unparseable ai response" in error_text:
        changes["OLLAMA_OUTPUT_FORMAT"] = "json"
        changes["AI_CHUNK_SIZE"] = "1"
        changes["TRADING_SWARM_MAX_CHUNK"] = "1"
        reasons.append("ai_json_repair")

    if (scanner_repeats >= 2 and ("tuple (not \"str\") to tuple" in error_text or "can only concatenate tuple" in error_text)) or (
        scanner_repeats >= 2 and "TypeError" in error_text
    ):
        # If we see this again, fail closed on the swarm layer first and keep the plain scanner.
        changes["TRADING_SWARM_ENABLED"] = "NO"
        changes["AI_CHUNK_SIZE"] = "1"
        reasons.append("swarm_prompt_repair")

    if "REQUIRE_AI_FOR_EXECUTION=YES" in error_text or counts.get("ai_execution_block", 0) > 0:
        changes["REQUIRE_AI_FOR_EXECUTION"] = "NO"
        reasons.append("ai_gate_repair")

    stage=str(adaptive.get("stage", "unknown"))
    if stage in {"normal", "warn", "reduce", "halt"}:
        current_conf=env_float("MIN_CONFIDENCE",0.50)
        current_trade=env_float("MAX_TRADE_USDC",2.0)
        base_conf=env_float("ADAPTIVE_BASE_MIN_CONFIDENCE",current_conf)
        base_trade=env_float("ADAPTIVE_BASE_MAX_TRADE_USDC",current_trade)
        confidence_floor=env_float("SUPERVISOR_MIN_CONFIDENCE_FLOOR",0.45)
        confidence_cap=min(env_float("SUPERVISOR_MAX_MIN_CONFIDENCE",0.75),env_float("MAX_ACCEPTED_CONFIDENCE",0.95))
        if stage=="normal":
            target_conf=base_conf
            target_trade=base_trade
        else:
            adaptive_conf=float(adaptive.get('min_confidence',base_conf))
            adaptive_trade=float(adaptive.get('max_trade_usdc',base_trade))
            target_conf=max(confidence_floor,min(confidence_cap,adaptive_conf))
            target_trade=base_trade if stage=="halt" else min(base_trade,adaptive_trade)
        if abs(current_conf-target_conf)>1e-9:
            changes["MIN_CONFIDENCE"]=f"{target_conf:.2f}"
        if abs(current_trade-target_trade)>1e-9:
            changes["MAX_TRADE_USDC"]=f"{target_trade:.8f}".rstrip("0").rstrip(".") or "0"

    if changes:
        return changes, {
            "enabled": True,
            "repaired": True,
            "reasons": reasons,
            "changes": changes,
        }
    return {}, {"enabled": True, "repaired": False, "reasons": [], "changes": {}}


def recent_frequency_errors():
    """Return the most recent frequency-gate errors recorded by the bot."""
    for event in reversed(read_events(120)):
        data = event.get("data") or {}
        gate = data.get("frequency_gate") if isinstance(data, dict) else None
        if isinstance(gate, dict):
            errors = gate.get("errors") or []
            if errors:
                return [str(error) for error in errors]
    return []


def ai_trade_candidates(report, minimum_confidence):
    ai = report.get("ai") or {}
    candidates = []
    for item in ai.get("opportunities") or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "HOLD")).upper()
        try:
            confidence = float(item.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if action in {"BUY", "SELL"} and confidence >= minimum_confidence:
            candidates.append({"symbol": str(item.get("symbol", "?")).upper(), "action": action, "confidence": confidence})
    return candidates


def recommend_frequency_params(report, adaptive):
    """Bounded AI-informed frequency changes; never removes hard safeguards."""
    enabled = os.getenv("AI_FREQUENCY_AUTOPILOT", "YES").upper() == "YES"
    current_max = env_int("MAX_TRADES_PER_DAY", 6)
    current_cooldown = env_int("COOLDOWN_AFTER_TRADE_SECONDS", 900)
    ceiling = max(current_max, env_int("AI_FREQUENCY_MAX_TRADES_PER_DAY", 40))
    floor_cooldown = max(60, env_int("AI_FREQUENCY_MIN_COOLDOWN_SECONDS", 600))
    step = max(1, env_int("AI_FREQUENCY_RAISE_STEP", 3))
    confidence = min(1.0, max(0.5, env_float("AI_FREQUENCY_STRONG_CONFIDENCE", 0.70)))
    stage = str(adaptive.get("stage", "unknown"))
    policy = {
        "enabled": enabled,
        "approved": False,
        "reason": "sin_cambio",
        "current": {"max_trades_per_day": current_max, "cooldown_seconds": current_cooldown},
        "limits": {"max_trades_per_day": ceiling, "min_cooldown_seconds": floor_cooldown},
        "ai_candidates": ai_trade_candidates(report, confidence),
        "frequency_errors": recent_frequency_errors(),
        "adaptive_stage": stage,
    }
    if not enabled:
        policy["reason"] = "autopiloto_desactivado"
        return {}, policy
    if adaptive.get("halted"):
        policy["reason"] = f"riesgo_adaptativo_{stage}"
        return {}, policy
    if stage not in {"normal", "warn"}:
        policy["reason"] = f"riesgo_adaptativo_{stage}"
        return {}, policy
    if not policy["ai_candidates"]:
        policy["reason"] = "sin_senal_ia_fuerte"
        return {}, policy
    changes = {}
    errors = policy["frequency_errors"]
    if "max_trades_per_day" in errors and current_max < ceiling:
        bump = step if stage == "normal" else max(step, 5)
        changes["MAX_TRADES_PER_DAY"] = str(min(ceiling, current_max + bump))
    if any(error.startswith("cooldown_active_") for error in errors) and current_cooldown > floor_cooldown:
        if stage == "warn":
            changes["COOLDOWN_AFTER_TRADE_SECONDS"] = str(floor_cooldown)
        else:
            changes["COOLDOWN_AFTER_TRADE_SECONDS"] = str(max(floor_cooldown, current_cooldown - 60))
    if changes:
        policy["approved"] = True
        policy["reason"] = "senales_ia_fuertes_y_riesgo_" + stage
        policy["changes"] = changes
    else:
        policy["reason"] = "limites_actuales_suficientes"
    return changes, policy


def load_cached_market():
    max_age = max(60, env_int("SUPERVISOR_MARKET_CACHE_SECONDS", 900))
    try:
        if time.time() - os.path.getmtime(MARKET_CACHE_PATH) > max_age:
            return None
        with open(MARKET_CACHE_PATH, encoding="utf-8") as handle:
            report = json.load(handle)
        if isinstance(report, dict) and isinstance(report.get("universe"), list) and isinstance(report.get("ai"), dict):
            return report
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return None

def auto_cycle_active():
    try:
        if time.time() - os.path.getmtime(AUTO_HEARTBEAT_PATH) > 15:
            return False
        with open(AUTO_HEARTBEAT_PATH, encoding="utf-8") as handle:
            heartbeat = json.load(handle)
        return heartbeat.get("status") == "running"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def fetch_market():
    cached = load_cached_market()
    if cached is not None:
        return cached
    if auto_cycle_active():
        raise RuntimeError("auto_cycle_active: supervisor scan deferred")
    timeout=max(60, env_int("SUPERVISOR_SCAN_TIMEOUT_SECONDS", env_int("AUTO_SCAN_TIMEOUT_SECONDS", 180) + 30))
    try:
        proc=run_command(["./start-bot","--json"],timeout=timeout,capture_output=True)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"scanner_timeout: {timeout}s") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1][:240] if proc.stderr.strip() else "sin detalle"
        raise RuntimeError(f"scanner_unavailable: {detail}")
    return json.loads(proc.stdout)


def recommend_params(report, perf, adaptive, feedback):
    values,order=read_env()
    current_scan_timeout=env_int("AUTO_SCAN_TIMEOUT_SECONDS",180)
    timeout_cap=max(120,min(env_int("AUTO_SCAN_TIMEOUT_HARD_MAX",210),240))
    current_generate_timeout=env_int("OLLAMA_GENERATE_TIMEOUT_SECONDS",150)
    current_model_timeout=env_int("OLLAMA_MODEL_TIMEOUT_SECONDS",120)
    current_min_conf=env_float("MIN_CONFIDENCE",0.50)
    current_max_trade=env_float("MAX_TRADE_USDC",2.0)
    current_reserve=env_float("BASE_RESERVE_RATIO",0.30)

    if feedback.get("counts",{}).get("scan_timeout",0)>0 or report.get("ai",{}).get("partial_errors"):
        current_scan_timeout=min(max(current_scan_timeout,180),timeout_cap)
        current_generate_timeout=min(max(current_generate_timeout,240),360)
        current_model_timeout=min(max(current_model_timeout,180),360)
    if feedback.get("counts",{}).get("frequency_block",0)>0:
        current_scan_timeout=min(max(current_scan_timeout,180),timeout_cap)

    stage=str(adaptive.get("stage","unknown"))
    base_conf=env_float("ADAPTIVE_BASE_MIN_CONFIDENCE",current_min_conf)
    base_trade=env_float("ADAPTIVE_BASE_MAX_TRADE_USDC",current_max_trade)
    confidence_floor=env_float("SUPERVISOR_MIN_CONFIDENCE_FLOOR",0.45)
    confidence_cap=min(env_float("SUPERVISOR_MAX_MIN_CONFIDENCE",0.75),env_float("MAX_ACCEPTED_CONFIDENCE",0.95))
    if stage=="normal":
        current_min_conf=base_conf
        current_max_trade=base_trade
        counts=feedback.get("counts",{}) if isinstance(feedback,dict) else {}
        hard_errors=sum(int(counts.get(name,0) or 0) for name in ("scan_timeout","scanner_unavailable","ai_execution_block"))
        if hard_errors==0 and not report.get("ai",{}).get("partial_errors"):
            reserve_step=max(0.005,min(0.02,env_float("SUPERVISOR_RESERVE_STEP",0.01)))
            reserve_floor=max(0.15,min(0.35,env_float("SUPERVISOR_RESERVE_FLOOR",0.20)))
            current_reserve=max(reserve_floor,current_reserve-reserve_step)
    elif stage in {"warn","reduce","halt"}:
        adaptive_conf=float(adaptive.get("min_confidence",base_conf))
        adaptive_trade=float(adaptive.get("max_trade_usdc",base_trade))
        current_min_conf=max(confidence_floor,min(confidence_cap,adaptive_conf))
        current_max_trade=base_trade if stage=="halt" else min(base_trade,adaptive_trade)

    desired={
        "AUTO_SCAN_TIMEOUT_SECONDS":str(int(current_scan_timeout)),
        "OLLAMA_GENERATE_TIMEOUT_SECONDS":str(int(current_generate_timeout)),
        "OLLAMA_MODEL_TIMEOUT_SECONDS":str(int(current_model_timeout)),
        "MIN_CONFIDENCE":f"{current_min_conf:.2f}",
        "MAX_TRADE_USDC":f"{current_max_trade:.8f}".rstrip("0").rstrip(".") or "0",
        "BASE_RESERVE_RATIO":f"{current_reserve:.2f}",
    }
    frequency_changes,frequency_policy=recommend_frequency_params(report,adaptive)
    desired.update(frequency_changes)
    changes={}
    for key,value in desired.items():
        if values.get(key)!=value:
            changes[key]=value
            values[key]=value
            if key not in order:
                order.append(key)
    return values,order,changes,frequency_policy


def maybe_execute():
    if os.getenv("SUPERVISOR_EXECUTE", "NO").upper() != "YES":
        return {"executed": False, "reason": "SUPERVISOR_EXECUTE=NO"}
    timeout = max(120, env_int("SUPERVISOR_EXECUTION_TIMEOUT_SECONDS", env_int("AUTO_CYCLE_WATCHDOG_SECONDS", 360)))
    try:
        result=run_command(["./crypto-auto"],timeout=timeout,capture_output=False)
    except subprocess.TimeoutExpired:
        log_event("supervisor_execution_timeout", timeout_seconds=timeout)
        return {"executed": True, "returncode": 124, "reason": "timeout"}
    return {"executed": True, "returncode": result.returncode}

def supervisor_cycle():
    load_env_file()
    feedback = feedback_summary(240)
    perf = decision_context()
    adaptive = adaptive_status()
    repair_values, repair_report = self_repair_params(feedback, adaptive)
    if repair_values:
        values, order = read_env()
        for key, value in repair_values.items():
            values[key] = value
            os.environ[key] = value
            if key not in order:
                order.append(key)
        write_env(values, order)
        log_event("supervisor_self_repair", report=repair_report, feedback=feedback, adaptive=adaptive)
        print(json.dumps({"self_repair": repair_report}, indent=2, ensure_ascii=False))
    report = fetch_market()

    universe_recommendation = recommend_universe(report, perf)
    if universe_recommendation.get("changed") and os.getenv("UNIVERSE_AUTOPILOT_APPLY", "NO").upper() == "YES":
        universe_result = apply_universe_recommendation(universe_recommendation)
    else:
        universe_result = {"applied": False, "reason": "disabled_or_no_change"}

    values, order, param_changes, frequency_policy = recommend_params(report, perf, adaptive, feedback)
    if param_changes:
        write_env(values, order)
        for key,value in param_changes.items():
            os.environ[key]=value

    result = {
        "market_symbols": [x.get("symbol") for x in report.get("universe", []) if isinstance(x, dict)],
        "adaptive": adaptive,
        "feedback": feedback,
        "universe_recommendation": universe_recommendation,
        "universe_result": universe_result,
        "param_changes": param_changes,
        "frequency_policy": frequency_policy,
        "execution": maybe_execute(),
    }
    log_event("codex_supervisor", report=result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main():
    signal.signal(signal.SIGTERM, interrupt_process)
    parser = argparse.ArgumentParser(prog="crypto_supervisor", add_help=True)
    parser.add_argument("--loop", action="store_true", help="Run as a continuous watchdog")
    parser.add_argument("--interval", type=int, default=None, help="Seconds between cycles")
    parser.add_argument("--once", action="store_true", help="Run a single cycle")
    args = parser.parse_args()

    interval = args.interval if args.interval is not None else env_int("SUPERVISOR_INTERVAL_SECONDS", 900)
    interval = max(60, interval)
    if not args.loop or args.once:
        load_env_file()
        supervisor_cycle()
        return 0

    print(f"SUPERVISOR watchdog activo · intervalo {interval}s · Ctrl+C para detener", flush=True)
    cycle = 0
    try:
        while True:
            cycle += 1
            print(f"SUPERVISOR ciclo {cycle} · evaluando mercado y logs...", flush=True)
            try:
                result = supervisor_cycle()
            except Exception as exc:
                if str(exc).startswith("auto_cycle_active:"):
                    log_event("supervisor_cycle_deferred", cycle=cycle, reason=str(exc))
                    print("SUPERVISOR: escaneo diferido; AUTO está generando el reporte compartido", flush=True)
                else:
                    log_event("supervisor_cycle_error", cycle=cycle, error=str(exc))
                    print(f"SUPERVISOR: ciclo {cycle} falló: {exc}; se reintentará", flush=True)
                result = {}
            print(
                "SUPERVISOR resumen · "
                f"universo={','.join(result.get('market_symbols', [])[:6])} · "
                f"cambios={','.join(result.get('param_changes', {}).keys()) or 'ninguno'}",
                flush=True,
            )
            for remaining in range(interval, 0, -1):
                if remaining == interval or remaining % 30 == 0 or remaining <= 10:
                    print(f"\033[2K\rSUPERVISOR próximo ciclo en {remaining:03d}s", end="", flush=True)
                time.sleep(1)
            print("\033[2K\rSUPERVISOR próximo ciclo en 000s · reiniciando", flush=True)
    except KeyboardInterrupt:
        print("\nSUPERVISOR detenido. Regresando al menú.")
        return 0
if __name__ == "__main__":
    raise SystemExit(main())

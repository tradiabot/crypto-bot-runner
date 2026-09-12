#!/usr/bin/env python3
import os

DEFAULT_ROLES = ["SCOUT", "CHALLENGER", "RISK", "ALLOCATOR", "AUDITOR"]

def enabled():
    return os.getenv("TRADING_SWARM_ENABLED", "NO").upper() == "YES"

def mode():
    return os.getenv("TRADING_SWARM_MODE", "ROTATE").upper()

def roles():
    raw = os.getenv("TRADING_SWARM_ROLES", ",".join(DEFAULT_ROLES))
    parsed = [x.strip().upper() for x in raw.split(",") if x.strip()]
    return parsed or DEFAULT_ROLES

def cycle_index():
    try:
        return int(os.getenv("AUTO_CYCLE_INDEX", "0"))
    except ValueError:
        return 0

def cycle_stride():
    try:
        stride = int(os.getenv("TRADING_SWARM_CYCLE_STRIDE", "1"))
        return max(1, stride)
    except ValueError:
        return 1

def role_for_cycle(index=None):
    pool = roles()
    if not pool:
        return "SCOUT"
    idx = cycle_index() if index is None else index
    return pool[(idx // cycle_stride()) % len(pool)]

def role_guidance(role):
    role = (role or "SCOUT").upper()
    guidance = {
        "SCOUT": "Prioriza descubrir oportunidades con tendencia y liquidez; devuelve solo JSON válido.",
        "CHALLENGER": "Busca el mejor contrasentido de alta calidad; cuestiona entradas débiles y devuelve solo JSON válido.",
        "RISK": "Enfócate en preservación de capital, stops y señales de salida; devuelve solo JSON válido.",
        "ALLOCATOR": "Optimiza distribución y rotación del portafolio; devuelve solo JSON válido.",
        "AUDITOR": "Valida consistencia, lot sizes y errores operativos; devuelve solo JSON válido.",
    }
    return guidance.get(role, guidance["SCOUT"])

def prompt_prefix(role, items, rows, feedback=""):
    role = (role or "SCOUT").upper()
    symbols = ", ".join(x.get("symbol", "?") for x in items)
    prefix = (
        f"You are the {role} agent in a trading swarm. {role_guidance(role)} "
        "Return ONLY valid JSON. Do not add markdown, commentary, or code fences. "
        "Each opportunity must use symbol, action, confidence, reason. "
        "Use only the supplied Market metrics; never invent news, companies, shares, CEOs, earnings, or insider transactions. "
        "Action must be BUY, SELL, or HOLD. Confidence must be a number from 0 to 1.\n"
        f"Universe: {symbols}\n"
        f"Market: {rows}"
    )
    if feedback:
        prefix += "\nRecent bot feedback (context only): " + feedback[:1800]
    return prefix

def attach_swarm(report, role=None):
    if not isinstance(report, dict):
        return report
    report["swarm"] = {
        "enabled": enabled(),
        "mode": mode(),
        "role": (role or role_for_cycle()).upper(),
        "roles": roles(),
        "cycle_index": cycle_index(),
        "cycle_stride": cycle_stride(),
    }
    return report

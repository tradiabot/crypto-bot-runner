#!/usr/bin/env python3
"""Deterministic safety gate for AI trading signals."""
import os

def env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def stables_protegidas():
    """Stablecoins que nunca se pueden vender.

    USDC es la moneda de financiacion y queda protegida siempre. USDT solo
    estaba protegido por una lista fija que contradecia a HELD_STABLE_EXCLUDE:
    ese interruptor libera el USDT para ordenes condicionadas, pero la orden
    se disparaba y aqui moria con 'invalid_source', dejando el saldo inmovil.
    Ahora ambos leen la misma fuente.
    """
    crudo = os.getenv("HELD_STABLE_EXCLUDE", "USDC,USDT")
    prot = {x.strip().upper() for x in crudo.split(",") if x.strip()} or {"USDC"}
    prot.add("USDC")
    return prot


def _price(market):
    try:
        return float(market.get("price_usd", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def validate(signal, market, amount, daily_loss=0.0):
    errors = []
    action = signal.get("action")
    source = signal.get("source", "USDC")
    confidence = signal.get("confidence")
    forced_loss_sell = (
        action == "SELL"
        and signal.get("strategy") == "FORCED_LOSS_SELL"
        and bool(signal.get("allow_loss_sell"))
    )
    price = _price(market)
    if action == "BUY" and source in {"USDC", "USDT"}:
        trade_value_usd = float(amount or 0)
    else:
        trade_value_usd = amount * price if price > 0 else 0.0

    if action not in {"BUY", "SELL", "HOLD"}: errors.append("invalid_action")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        errors.append("invalid_confidence")
    elif action in {"BUY", "SELL"}:
        threshold=env_float("MIN_CONFIDENCE", "0.48") if action=="BUY" else env_float("MIN_SELL_CONFIDENCE", "0.58")
        if confidence < threshold: errors.append("confidence_below_threshold")
        elif confidence > env_float("MAX_ACCEPTED_CONFIDENCE", "0.95"): errors.append("confidence_not_calibrated")
    if action in {"BUY", "SELL"} and str(market.get("tradable", "false")).lower() != "true": errors.append("asset_not_tradable")
    if action in {"BUY", "SELL"} and price <= 0: errors.append("invalid_price")
    if action == "BUY" and source not in {"USDC", "USDT", "BTC"}: errors.append("invalid_source")
    if action == "SELL" and source in stables_protegidas(): errors.append("invalid_source")
    if amount <= 0: errors.append("invalid_amount")
    max_trade_usdc = env_float("MAX_TRADE_USDC", "8")
    min_trade_usdc = env_float("MIN_TRADE_USDC", "5")
    max_sell_native_usd = env_float("MAX_SELL_NATIVE_USD", "8")
    min_sell_native_usd = env_float("MIN_SELL_NATIVE_USD", "5")
    max_daily_loss_usdc = env_float("MAX_DAILY_LOSS_USDC", "1")
    if action == "BUY" and source in {"USDC", "USDT"} and amount < min_trade_usdc: errors.append("trade_below_minimum_cost_guard")
    if action == "BUY" and source in {"USDC", "USDT"} and amount > max_trade_usdc: errors.append("trade_limit_exceeded")
    if action == "SELL" and trade_value_usd < min_sell_native_usd and not forced_loss_sell: errors.append("sell_below_minimum_cost_guard")
    if action == "SELL" and trade_value_usd > max_sell_native_usd: errors.append("sell_limit_exceeded")
    if action == "BUY" and daily_loss >= max_daily_loss_usdc: errors.append("daily_loss_limit")
    return {"approved": not errors, "errors": errors, "trade_value_usd": trade_value_usd, "limits": {"max_trade_usdc": max_trade_usdc, "min_trade_usdc": min_trade_usdc, "max_sell_native_usd": max_sell_native_usd, "min_sell_native_usd": min_sell_native_usd, "max_daily_loss_usdc": max_daily_loss_usdc}}

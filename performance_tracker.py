#!/usr/bin/env python3
import json
from bot_logger import read_events
from cost_basis import load_cost_basis


def _float_amount(value):
    try:
        if isinstance(value, dict):
            value=value.get("amount", 0)
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _trade_from_event(event):
    data=event.get("data", {})
    result=data.get("result", {})
    signal=data.get("signal", {})
    symbol=data.get("symbol") or signal.get("source")
    action=signal.get("action")
    amount=data.get("amount")
    from_amount=result.get("amount", {})
    to_amount=result.get("to_amount", {})
    native=result.get("native_amount", {})
    description=result.get("description", "")
    return {
        "ts": event.get("ts"),
        "symbol": symbol,
        "action": action,
        "source": signal.get("source"),
        "strategy": signal.get("strategy"),
        "confidence": signal.get("confidence"),
        "reason": signal.get("reason"),
        "requested_amount": amount,
        "from_currency": from_amount.get("currency"),
        "from_amount": _float_amount(from_amount),
        "to_currency": to_amount.get("currency"),
        "to_amount": _float_amount(to_amount),
        "native_usd": _float_amount(native),
        "status": result.get("status"),
        "description": description,
    }


def performance_summary(limit=1000):
    events=read_events(limit)
    trades=[_trade_from_event(e) for e in events if e.get("event") == "trade_executed"]
    positions={}
    realized=[]
    for trade in trades:
        if str(trade.get("status", "")).lower() not in {"done", "completed", "success"}:
            continue
        src=trade.get("from_currency")
        dst=trade.get("to_currency")
        src_amt=abs(trade.get("from_amount", 0.0))
        dst_amt=trade.get("to_amount", 0.0)
        native=trade.get("native_usd", 0.0)
        if dst and dst not in {"USDC", "USDT"} and dst_amt > 0:
            pos=positions.setdefault(dst, {"amount":0.0,"cost_usd":0.0,"buys":0,"sells":0})
            pos["amount"] += dst_amt
            pos["cost_usd"] += native
            pos["buys"] += 1
        if src and src not in {"USDC", "USDT"} and src_amt > 0:
            pos=positions.setdefault(src, {"amount":0.0,"cost_usd":0.0,"buys":0,"sells":0})
            avg_cost=(pos["cost_usd"] / pos["amount"]) if pos["amount"] > 0 else 0.0
            sold_cost=min(pos["cost_usd"], avg_cost * src_amt) if avg_cost else 0.0
            pos["amount"] = max(0.0, pos["amount"] - src_amt)
            pos["cost_usd"] = max(0.0, pos["cost_usd"] - sold_cost)
            pos["sells"] += 1
            if native:
                realized.append({"symbol":src,"sold_amount":src_amt,"proceeds_usd":native,"estimated_cost_usd":sold_cost,"estimated_pnl_usd":native-sold_cost,"ts":trade.get("ts")})
    basis=load_cost_basis().get("positions", {})
    for sym,bpos in basis.items():
        if bpos.get("amount",0) > 0 and bpos.get("cost_usd",0) > 0:
            pos=positions.setdefault(sym, {"amount":0.0,"cost_usd":0.0,"buys":0,"sells":0})
            if pos.get("amount",0) <= 0 or pos.get("cost_usd",0) <= 0:
                pos["amount"]=float(bpos.get("amount",0) or 0)
                pos["cost_usd"]=float(bpos.get("cost_usd",0) or 0)
                pos["estimated"]=bool(bpos.get("estimated", False))
                pos["source"]=bpos.get("source", "cost_basis")
    for sym,pos in positions.items():
        pos["avg_cost_usd"]=(pos["cost_usd"] / pos["amount"]) if pos["amount"] > 0 else 0.0
    blocks=[e for e in events if e.get("event") in {"risk_gate_block","frequency_block","scan_timeout","scanner_unavailable","execution_skipped"}]
    holds=[e for e in events if e.get("event") == "decision_hold"]
    return {"trades_count":len(trades),"positions":positions,"realized":realized[-20:],"recent_blocks":blocks[-10:],"recent_holds":holds[-10:]}


def decision_context(limit=1000):
    summary=performance_summary(limit)
    positions=summary.get("positions", {})
    # Re-entry is controlled by PER_ASSET_COOLDOWN_SECONDS in crypto_auto.py.
    # A permanent avoid list made the bot miss valid trend re-entries after taking profit/rebalance.
    weak=[]
    return {"positions":positions,"recent_realized":summary.get("realized", [])[-5:],"recent_blocks_count":len(summary.get("recent_blocks", [])),"recent_holds_count":len(summary.get("recent_holds", [])),"avoid_rebuy_symbols":weak}


def print_performance():
    print(json.dumps(performance_summary(), indent=2, ensure_ascii=False))

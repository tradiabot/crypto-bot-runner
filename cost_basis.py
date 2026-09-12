#!/usr/bin/env python3
import json, os, time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PATH=ROOT/'.runtime'/'cost_basis.json'

STABLE={'USDC','USDT','USD'}

def load_cost_basis():
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return {'positions':{}, 'updated_at':0}

def save_cost_basis(data):
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        data['updated_at']=time.time()
        tmp=PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(PATH)
    except OSError:
        return False
    return True

def apply_trade(data, src, src_amount, dst, dst_amount, native_usd, txid=None, ts=None):
    positions=data.setdefault('positions', {})
    processed=data.setdefault('processed_ids', [])
    if txid and txid in processed:
        return data
    src=(src or '').upper(); dst=(dst or '').upper()
    src_amount=abs(float(src_amount or 0)); dst_amount=abs(float(dst_amount or 0)); native_usd=abs(float(native_usd or 0))
    if dst and dst not in STABLE and dst_amount>0 and native_usd>0:
        p=positions.setdefault(dst, {'amount':0.0,'cost_usd':0.0,'realized_pnl_usd':0.0,'estimated':False,'source':'trades'})
        p['amount']+=dst_amount
        p['cost_usd']+=native_usd
        p['source']='trades'
    if src and src not in STABLE and src_amount>0:
        p=positions.setdefault(src, {'amount':0.0,'cost_usd':0.0,'realized_pnl_usd':0.0,'estimated':True,'source':'partial'})
        avg=(p.get('cost_usd',0.0)/p.get('amount',0.0)) if p.get('amount',0.0)>0 else 0.0
        sold_cost=min(p.get('cost_usd',0.0), avg*src_amount) if avg else 0.0
        p['amount']=max(0.0, p.get('amount',0.0)-src_amount)
        p['cost_usd']=max(0.0, p.get('cost_usd',0.0)-sold_cost)
        if native_usd and sold_cost:
            p['realized_pnl_usd']=p.get('realized_pnl_usd',0.0)+(native_usd-sold_cost)
    if txid:
        processed.append(txid)
        data['processed_ids']=processed[-1000:]
    data['last_trade_ts']=ts or time.time()
    return data

def seed_position(data, symbol, amount, native_usd, estimated=True):
    symbol=symbol.upper(); amount=float(amount or 0); native_usd=float(native_usd or 0)
    if symbol in STABLE or amount<=0 or native_usd<=0:
        return data
    positions=data.setdefault('positions', {})
    p=positions.get(symbol)
    if p and p.get('amount',0)>0 and p.get('cost_usd',0)>0:
        tracked=float(p.get('amount',0) or 0)
        if amount > tracked:
            missing=amount-tracked
            unit_mark=native_usd/amount if amount>0 else 0.0
            p['amount']=amount
            p['cost_usd']=float(p.get('cost_usd',0) or 0)+(missing*unit_mark)
            p['estimated']=True
            p['source']='trades+balance_seed'
        elif amount < tracked:
            avg_cost=float(p.get("cost_usd",0) or 0) / tracked
            p["amount"]=amount
            p["cost_usd"]=avg_cost * amount
            p["estimated"]=True
            p["source"]="trades+balance_reconcile"
        return data
    positions[symbol]={
        'amount':amount,
        'cost_usd':native_usd,
        'realized_pnl_usd':0.0,
        'estimated':bool(estimated),
        'source':'balance_seed'
    }
    return data

def avg_cost(symbol):
    data=load_cost_basis()
    p=data.get('positions',{}).get(symbol.upper(),{})
    amount=float(p.get('amount',0) or 0); cost=float(p.get('cost_usd',0) or 0)
    return (cost/amount) if amount>0 and cost>0 else 0.0

def position(symbol):
    data=load_cost_basis()
    p=data.get('positions',{}).get(symbol.upper(),{}).copy()
    amount=float(p.get('amount',0) or 0); cost=float(p.get('cost_usd',0) or 0)
    p['avg_cost_usd']=(cost/amount) if amount>0 and cost>0 else 0.0
    return p

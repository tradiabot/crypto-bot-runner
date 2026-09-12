#!/usr/bin/env python3
import json, subprocess, os
from pathlib import Path
from bot_logger import read_events
from cost_basis import load_cost_basis, save_cost_basis, apply_trade, seed_position
from exchange_adapter import balance

ROOT=Path(__file__).resolve().parent
SKILL='/root/.agents/skills/crypto-com-app/scripts'

def f(v):
    try:
        if isinstance(v, dict): v=v.get('amount',0)
        return float(v or 0)
    except Exception:
        return 0.0

def load_env():
    p=ROOT/'.env'
    if p.exists():
        for line in p.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1); os.environ[k]=v.strip().strip('"').strip("'")
    os.environ['CDC_API_KEY']=os.getenv('CDC_API_KEY',os.getenv('CRYPTO_COM_API_KEY',''))
    os.environ['CDC_API_SECRET']=os.getenv('CDC_API_SECRET',os.getenv('CRYPTO_COM_API_SECRET',''))

def import_local_events(data):
    for e in read_events(2000):
        if e.get('event')!='trade_executed':
            continue
        d=e.get('data',{}); r=d.get('result',{}) if isinstance(d.get('result'),dict) else {}
        txid=str(r.get('id') or '')
        src=r.get('amount',{}).get('currency'); dst=r.get('to_amount',{}).get('currency')
        data=apply_trade(data, src, f(r.get('amount')), dst, f(r.get('to_amount')), f(r.get('native_amount')), txid=txid if txid else None, ts=e.get('epoch'))
    return data

def import_recent_history(data):
    load_env()
    p=subprocess.run(['npx','tsx',f'{SKILL}/trade.ts','history'], cwd=ROOT, text=True, capture_output=True, env=os.environ.copy(), timeout=45)
    raw=p.stdout[p.stdout.find('{'):].strip() if '{' in p.stdout else p.stdout
    out=json.loads(raw)
    if not out.get('ok'):
        raise SystemExit(out.get('error_message') or out.get('error'))
    for r in reversed(out.get('data',[])):
        txid=str(r.get('id') or '')
        src=r.get('amount',{}).get('currency'); dst=r.get('to_amount',{}).get('currency')
        data=apply_trade(data, src, f(r.get('amount')), dst, f(r.get('to_amount')), f(r.get('native_amount')), txid=txid, ts=r.get('created_at'))
    return data

def seed_current_balances(data):
    b=balance()
    wallets=b.get('crypto',{}).get('wallets',[]) if isinstance(b,dict) else []
    for w in wallets:
        sym=w.get('currency')
        amount=f(w.get('available') or w.get('balance'))
        native=f(w.get('native_available') or w.get('native_balance'))
        data=seed_position(data, sym, amount, native, estimated=True)
    return data

def main():
    data=load_cost_basis()
    data=import_local_events(data)
    try:
        data=import_recent_history(data)
    except Exception as exc:
        data.setdefault('warnings',[]).append(f'history_import_failed: {exc}')
    data=seed_current_balances(data)
    save_cost_basis(data)
    print(json.dumps(data, indent=2, ensure_ascii=False))

if __name__=='__main__': main()

#!/usr/bin/env python3
"""Adaptive risk controls derived from local bot history."""
import json, os
from statistics import median
from collections import Counter, defaultdict

ROOT=os.path.dirname(os.path.abspath(__file__))
RUNTIME=os.path.join(ROOT,'.runtime')
STATE_PATH=os.path.join(RUNTIME,'adaptive_risk_state.json')

def _float_env(name, default):
    try: return float(os.getenv(name, default))
    except (TypeError, ValueError): return float(default)

def _events(limit=3000):
    rows=[]
    for path in (os.path.join(RUNTIME,'crypto-bot.log'),os.path.join(RUNTIME,'crypto-bot.log.1'),os.path.join(RUNTIME,'crypto-bot.log.1.old')):
        if not os.path.exists(path): continue
        try:
            with open(path,encoding='utf-8') as f:
                for line in f:
                    try: rows.append(json.loads(line))
                    except json.JSONDecodeError: pass
        except OSError: pass
    rows.sort(key=lambda r:r.get('epoch',0) or 0)
    return rows[-limit:]

def _load_state():
    try:
        with open(STATE_PATH,encoding='utf-8') as f:
            v=json.load(f); return v if isinstance(v,dict) else {}
    except (OSError,json.JSONDecodeError): return {}

def _save_state(state):
    os.makedirs(RUNTIME,exist_ok=True); tmp=STATE_PATH+'.tmp'
    with open(tmp,'w',encoding='utf-8') as f: json.dump(state,f,indent=2)
    os.replace(tmp,STATE_PATH)

def _portfolio_values(events):
    out=[]
    for e in events:
        if e.get('event')!='portfolio_snapshot': continue
        try: value=float((e.get('data') or {}).get('total_usd',0) or 0)
        except (TypeError,ValueError): value=0
        if value>0: out.append(value)
    return out

def status(events=None):
    if os.getenv('ADAPTIVE_RISK_ENABLED','YES').upper()!='YES':
        return {'enabled':False,'stage':'disabled','halted':False,'max_trade_usdc':_float_env('MAX_TRADE_USDC',1.0),'min_confidence':_float_env('MIN_CONFIDENCE',.48),'drawdown_pct':0}
    events=events if events is not None else _events()
    values=_portfolio_values(events)
    current=median(values[-3:]) if values else 0
    mode=os.getenv('ADAPTIVE_BASELINE_MODE','RECENT_HIGH').upper()
    state=_load_state()
    configured=_float_env('ADAPTIVE_BASELINE_USD',0)
    if configured>0:
        baseline,source=configured,'ADAPTIVE_BASELINE_USD'
    elif state.get('baseline_equity',0):
        baseline,source=float(state['baseline_equity']),'persisted'
    elif values:
        baseline=max(values) if mode=='RECENT_HIGH' else values[0]
        source='recent_high' if baseline==max(values) else 'first_snapshot'
    else:
        baseline,source=current,'current_snapshot'
    persisted=float(state.get('baseline_equity',0) or 0)
    reset_multiplier=_float_env('ADAPTIVE_STALE_BASELINE_RESET_MULTIPLIER',0)
    if configured<=0 and persisted>0 and current>0 and reset_multiplier>1 and persisted/current>=reset_multiplier:
        baseline=current
        source='stale_reset'
        state.update({'baseline_equity':baseline,'baseline_source':source})
        _save_state(state)
    elif baseline>0 and configured<=0 and (persisted<=0 or (mode=='RECENT_HIGH' and current>persisted)):
        baseline=max(baseline,current)
        source='recent_high' if mode=='RECENT_HIGH' else source
        state.update({'baseline_equity':baseline,'baseline_source':source})
        _save_state(state)
    drawdown=max(0,(baseline-current)/baseline) if baseline>0 and current>0 else 0
    warn=_float_env('ADAPTIVE_WARN_LOSS_PCT',.05)
    reduce=_float_env('ADAPTIVE_REDUCE_LOSS_PCT',.10)
    halt=_float_env('ADAPTIVE_HALT_LOSS_PCT',.20)
    if drawdown>=halt:
        stage,mult='halt',0
    elif drawdown>=reduce:
        stage,mult='reduce',_float_env('ADAPTIVE_REDUCE_TRADE_MULTIPLIER',.5)
    elif drawdown>=warn:
        stage,mult='warn',_float_env('ADAPTIVE_WARN_TRADE_MULTIPLIER',.75)
    else:
        stage,mult='normal',1
    base_trade=_float_env('ADAPTIVE_BASE_MAX_TRADE_USDC',_float_env('MAX_TRADE_USDC',1.0))
    base_conf=_float_env('ADAPTIVE_BASE_MIN_CONFIDENCE',_float_env('MIN_CONFIDENCE',.48))
    adjusted_trade=max(0,base_trade*mult)
    minimum_trade=_float_env('MIN_TRADE_USDC',0)
    if stage!='halt' and base_trade>=minimum_trade>0:
        adjusted_trade=max(minimum_trade,adjusted_trade)
    add=_float_env('ADAPTIVE_MIN_CONFIDENCE_ADD',.02) if stage in {'reduce','halt'} else 0
    return {'enabled':True,'stage':stage,'halted':stage=='halt','baseline_equity':round(baseline,6),'current_equity':round(current,6),'drawdown_pct':round(drawdown,6),'baseline_source':source,'thresholds':{'warn':warn,'reduce':reduce,'halt':halt},'base_max_trade_usdc':base_trade,'base_min_confidence':base_conf,'max_trade_usdc':round(adjusted_trade,8),'min_confidence':min(1,base_conf+add),'allow_risk_reducing_sells':os.getenv('ADAPTIVE_ALLOW_RISK_REDUCING_SELLS','YES').upper()=='YES'}

def feedback_context(events=None):
    events=events if events is not None else _events(); counts=Counter(e.get('event','unknown') for e in events); trades=[e for e in events if e.get('event')=='trade_executed']; by=defaultdict(Counter)
    for e in trades:
        d=e.get('data') or {}; s=d.get('signal') or {}; by[str(d.get('symbol','?')).upper()][str(s.get('action','?')).upper()]+=1
    return {'recent_events':len(events),'trade_count':len(trades),'trades_by_asset':{k:dict(v) for k,v in by.items()},'blocks':{k:counts[k] for k in ('frequency_block','allocation_block','asset_cooldown_block','risk_gate_block','scan_timeout') if counts[k]},'repeated_trade_warning':any(sum(v.values())>=8 and v.get('BUY',0)>=3 and v.get('SELL',0)>=3 for v in by.values()),'adaptive_risk':status(events)}

"""Contexto de mercado para las decisiones de la IA (como el «Mercado global» de Kumo β).

- Mercado global: BTC, ETH, SOL, HYPE, S&P 500, Nasdaq 100, oro y petroleo en
  1 h / 24 h / 48 h (velas publicas de 15 min de Hyperliquid), con tono
  (apetito o aversion al riesgo), refugio (oro sube y bolsa baja) y turbulencia
  (la ultima hora se mueve mucho mas de lo normal).
- Noticias de las ultimas 24 h: titulares de RSS publicos y gratuitos
  (Cointelegraph, Decrypt y Google Noticias de mercados/economia), sin clave.

Se calcula en el runner (GitHub Actions) y no en el Worker: el plan gratis de
Cloudflare solo da 10 ms de CPU y parsear varios RSS no cabe. Se guarda en
.runtime/mercado-contexto.json para que el escaner y el motor compartan el mismo
resultado dentro del ciclo.
"""
import json
import math
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(ROOT, ".runtime", "mercado-contexto.json")
HL_INFO = "https://api.hyperliquid.xyz/info"
UA = "Mozilla/5.0 (crypto-bot)"

GLOBALES = [
    ("BTC", "BTC"), ("ETH", "ETH"), ("SOL", "SOL"), ("HYPE", "HYPE"),
    ("xyz:SP500", "S&P 500"), ("xyz:XYZ100", "Nasdaq 100"), ("xyz:GOLD", "Oro"), ("xyz:CL", "Petróleo WTI"),
]
FEEDS = [
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("Google Noticias", "https://news.google.com/rss/search?q=stock+market+OR+federal+reserve+OR+economy+OR+bitcoin+when:1d&hl=en-US&gl=US&ceid=US:en"),
]
CRIPTO = {"BTC", "ETH", "SOL", "HYPE"}


def _post(body, timeout=15):
    req = urllib.request.Request(HL_INFO, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _sd(xs):
    r = [math.log(b / a) for a, b in zip(xs, xs[1:]) if a > 0 and b > 0]
    if len(r) < 2:
        return 0.0
    me = sum(r) / len(r)
    return math.sqrt(sum((x - me) ** 2 for x in r) / (len(r) - 1))


def volatilidad(coin, ahora_ms=None):
    """Cambio y volatilidad por hora con velas de 15 min de las ultimas 49 h."""
    ahora_ms = ahora_ms or int(time.time() * 1000)
    velas = _post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "15m", "startTime": ahora_ms - 49 * 3_600_000, "endTime": ahora_ms}})
    v = [(float(x["c"]), float(x["h"]), float(x["l"])) for x in velas if float(x.get("c", 0)) > 0 and float(x.get("h", 0)) > 0 and float(x.get("l", 0)) > 0]
    if len(v) < 12:
        return None
    c = [x[0] for x in v]
    n = len(c) - 1
    s24, s48 = _sd(c[-97:]) * 2, _sd(c) * 2
    park = math.sqrt(sum(math.log(h / l) ** 2 for _, h, l in v[-4:]) / 4 / (4 * math.log(2))) * 2
    s1 = max(park, s24 * 0.5)
    cambio = lambda k: round((c[n] / c[n - k] - 1) * 100, 2) if n >= k else None
    ratio = s1 / s48 if s48 > 0 else 1
    return {"precio": c[n], "cambio_1h_pct": cambio(4), "cambio_24h_pct": cambio(96), "cambio_48h_pct": cambio(min(n, 192)),
            "regimen": "turbulento" if ratio >= 1.8 else "calmo" if ratio <= 0.5 else "normal"}


def mercado_global():
    with ThreadPoolExecutor(max_workers=8) as pool:
        res = list(pool.map(lambda g: (g, _seguro(volatilidad, g[0])), GLOBALES))
    activos = [{"sub": sub, "nombre": nombre, **v} for (sub, nombre), v in res if v]

    def prom(subs):
        xs = [a["cambio_24h_pct"] for a in activos if a["sub"] in subs and a.get("cambio_24h_pct") is not None]
        return round(sum(xs) / len(xs), 2) if xs else None

    cripto, bolsa = prom(CRIPTO), prom({"xyz:SP500", "xyz:XYZ100"})
    oro, petroleo = prom({"xyz:GOLD"}), prom({"xyz:CL"})
    riesgo = [x for x in (cripto, bolsa) if x is not None]
    tono = "mixto"
    if riesgo and all(x >= 0.5 for x in riesgo):
        tono = "apetito de riesgo"
    elif riesgo and all(x <= -0.5 for x in riesgo):
        tono = "aversión al riesgo"
    refugio = oro is not None and oro >= 0.5 and (bolsa or 0) < 0
    turb = [a for a in activos if a["regimen"] == "turbulento"]
    turbulencia = bool(turb) and (len(turb) * 3 >= max(1, len(activos)) or any(a["sub"] in ("BTC", "xyz:SP500") for a in turb))
    f = lambda x: "—" if x is None else f"{'+' if x > 0 else ''}{x}%"
    resumen = (f"24 h: cripto {f(cripto)}, bolsa {f(bolsa)}, oro {f(oro)}, petróleo {f(petroleo)}. "
               + ("Apetito por riesgo (todo sube)." if tono == "apetito de riesgo" else "Aversión al riesgo (cripto y bolsa caen)." if tono == "aversión al riesgo" else "Tono mixto.")
               + (" El oro sube mientras la bolsa baja: buscan refugio." if refugio else "")
               + (f" Turbulencia: la última hora se mueve mucho más que lo normal ({', '.join(a['nombre'] for a in turb)})." if turbulencia else " Volatilidad normal."))
    return {"activos": activos, "cripto_24h_pct": cripto, "bolsa_24h_pct": bolsa, "oro_24h_pct": oro, "petroleo_24h_pct": petroleo,
            "tono": tono, "refugio": refugio, "turbulencia": turbulencia, "resumen": resumen}


def _texto(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def noticias_24h(maximo=14):
    limite = time.time() - 24 * 3600
    vistos, out = set(), []

    def leer(feed):
        fuente, url = feed
        items = []
        root = ET.fromstring(_get(url))
        for it in root.iter("item"):
            titulo = re.sub(r"\s+", " ", _texto(it, "title"))
            fecha = _texto(it, "pubDate")
            try:
                ts = parsedate_to_datetime(fecha).timestamp()
            except (TypeError, ValueError):
                continue
            if not titulo or ts < limite:
                continue
            src = _texto(it, "source") or fuente
            if fuente == "Google Noticias" and " - " in titulo:
                titulo, src = titulo.rsplit(" - ", 1)
            items.append({"titulo": titulo[:180], "fuente": src[:40], "ts": int(ts)})
        return items[:25]

    with ThreadPoolExecutor(max_workers=3) as pool:
        for items in pool.map(lambda f: _seguro(leer, f) or [], FEEDS):
            for n in items:
                clave = re.sub(r"[^a-z0-9]", "", n["titulo"].lower())[:60]
                if clave in vistos:
                    continue
                vistos.add(clave)
                out.append(n)
    out.sort(key=lambda n: -n["ts"])
    return out[:maximo]


def _seguro(fn, *a):
    try:
        return fn(*a)
    except Exception as exc:  # una fuente caida no debe tumbar el ciclo
        print(f"[contexto] {getattr(fn, '__name__', 'fuente')} {a[:1]}: {str(exc)[:120]}", file=sys.stderr)
        return None


def contexto(max_edad=540):
    """Contexto del ciclo (cache de 9 min compartida por escaner y motor)."""
    try:
        with open(CACHE_PATH) as fh:
            c = json.load(fh)
        if time.time() - c.get("ts", 0) < max_edad:
            return c
    except (OSError, ValueError):
        pass
    c = {"ts": int(time.time()), "global": _seguro(mercado_global) or {}, "noticias": _seguro(noticias_24h) or []}
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w") as fh:
            json.dump(c, fh, ensure_ascii=False)
    except OSError:
        pass
    return c


def texto_para_ia(c=None, noticias=10):
    """Bloque corto para el prompt de la IA."""
    c = c or contexto()
    g = c.get("global") or {}
    partes = []
    if g.get("resumen"):
        filas = "; ".join(f"{a['nombre']}: 1h {a.get('cambio_1h_pct')}% 24h {a.get('cambio_24h_pct')}% 48h {a.get('cambio_48h_pct')}%"
                          + (" (turbulento)" if a.get("regimen") == "turbulento" else "") for a in g.get("activos", []))
        partes.append(f"Global market now: {g['resumen']} Detail: {filas}.")
    tit = [f"- {n['titulo']} ({n['fuente']})" for n in (c.get("noticias") or [])[:noticias]]
    if tit:
        partes.append("News headlines from the last 24 h (only these; do not invent news):\n" + "\n".join(tit))
    if not partes:
        return ""
    return ("Context: " + "\n".join(partes) + "\nUse this context: with risk-off tone, turbulence or clearly negative news be more "
            "demanding before BUY (lower confidence); do not chase spikes driven by a single headline.")


if __name__ == "__main__":
    c = contexto(max_edad=0)
    print(json.dumps(c, ensure_ascii=False, indent=1)[:3000])
    print(texto_para_ia(c))

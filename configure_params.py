#!/usr/bin/env python3
import os
import tempfile

ROOT=os.path.dirname(os.path.abspath(__file__))
PATH=os.path.join(ROOT,".env")

PARAMS=[
    ("AI_PROVIDER","groq","Proveedor IA: groq, ollama o technical"),
    ("AI_FALLBACK_PROVIDERS","ollama,technical","Fallbacks IA en orden"),
    ("GROQ_MODEL","openai/gpt-oss-20b","Modelo remoto Groq"),
    ("GROQ_TIMEOUT_SECONDS","30","Timeout API Groq"),
    ("OLLAMA_MODEL","qwen3:0.6b","Modelo IA Ollama"),
    ("OLLAMA_FALLBACK_MODEL","qwen3:1.7b","Modelo IA fallback"),
    ("OLLAMA_OUTPUT_FORMAT","json","Formato de salida Ollama"),
    ("OLLAMA_GENERATE_TIMEOUT_SECONDS","150","Timeout de respuesta IA"),
    ("OLLAMA_MODEL_TIMEOUT_SECONDS","120","Timeout por intento de modelo IA"),
    ("TRADE_UNIVERSE","BTC,ETH,SOL,POL","Activos a escanear"),
    ("TRADE_INCLUDE","","Activos extra a incluir"),
    ("TRADE_EXCLUDE","","Activos a excluir"),
    ("AI_CHUNK_SIZE","1","Activos por bloque de IA"),
    ("AI_PREFILTER_CANDIDATES","2","Candidatos prefiltrados"),
    ("MAX_TRADE_USDC","1.5","Maximo por compra con USDC"),
    ("MAX_SELL_NATIVE_USD","10","Maximo valor USD por venta"),
    ("BASE_RESERVE_RATIO","0.18","Reserva base USDC/BTC"),
    ("MIN_CONFIDENCE","0.43","Confianza minima"),
    ("MAX_ACCEPTED_CONFIDENCE","0.95","Confianza maxima aceptada"),
    ("REQUIRE_AI_FOR_EXECUTION","NO","Bloquear ejecucion si falla IA"),
    ("MAX_ASSET_WEIGHT","0.20","Peso maximo general por activo"),
    ("CRO_MAX_WEIGHT","0.03","Peso maximo para CRO"),
    ("CRO_TARGET_WEIGHT","0.02","Peso objetivo para CRO"),
    ("BTC_MIN_WEIGHT","0.10","Peso minimo reservado en BTC"),
    ("USDC_MIN_WEIGHT","0.10","Peso minimo objetivo en USDC"),
    ("MIN_PROFIT_TO_SELL_PCT","0.003","Ganancia minima para vender"),
    ("STOP_LOSS_PCT","0.04","Stop loss por activo"),
    ("TRAILING_STOP_PCT","0.025","Trailing stop objetivo"),
    ("MAX_TRADES_PER_DAY","30","Maximo trades por dia"),
    ("MAX_TRADES_PER_CYCLE","3","Maximo trades por ciclo"),
    ("COOLDOWN_AFTER_TRADE_SECONDS","180","Cooldown tras trade real"),
    ("AI_FREQUENCY_AUTOPILOT","YES","Permitir ajuste IA de frecuencia con limites"),
    ("AI_FREQUENCY_MAX_TRADES_PER_DAY","40","Tope duro de trades diarios para IA"),
    ("AI_FREQUENCY_MIN_COOLDOWN_SECONDS","600","Cooldown minimo que IA puede establecer"),
    ("AI_FREQUENCY_RAISE_STEP","3","Aumento maximo por ajuste IA"),
    ("AI_FREQUENCY_STRONG_CONFIDENCE","0.75","Confianza IA requerida para ajustar frecuencia"),
    ("PER_ASSET_COOLDOWN_SECONDS","300","Cooldown por activo y accion"),
    ("ALLOW_TECHNICAL_FALLBACK_SIGNALS","NO","Permitir senales tecnicas si falla IA"),
    ("CRO_BUY_DISABLED","YES","Bloquear compras nuevas de CRO"),
    ("AUTO_INTERVAL_SECONDS","45","Espera entre ciclos"),
    ("AUTO_SCAN_TIMEOUT_SECONDS","180","Timeout del ciclo de escaneo"),
    ("AUTO_FULL_SCAN_EVERY_N_CYCLES","1","Ciclos entre escaneos completos"),
    ("AUTO_FULL_SCAN_EVERY_N_SECONDS","0","Segundos entre escaneos completos"),
    ("AUTO_CYCLE_WATCHDOG_SECONDS","360","Timeout maximo de un ciclo completo"),
    ("AUTO_IDLE_WATCHDOG_SECONDS","120","Timeout sin nueva salida del proceso"),
    ("BOT_LOG_MAX_BYTES","1048576","Tamano maximo de log"),
    ("CDC_API_TIMEOUT_SECONDS","45","Timeout API Crypto.com"),
    ("CDC_BALANCE_CACHE_SECONDS","20","Cache de balance Crypto.com"),
    ("CRO_LOT_STEP","0.1","Lote minimo CRO"),
    ("BTC_LOT_STEP","0.0000001","Lote minimo BTC"),
    ("ETH_LOT_STEP","0.000001","Lote minimo ETH"),
    ("SOL_LOT_STEP","0.000001","Lote minimo SOL"),
    ("LINK_LOT_STEP","0.000001","Lote minimo LINK"),
    ("POL_LOT_STEP","0.000001","Lote minimo POL"),
    ("ADA_LOT_STEP","0.01","Lote minimo ADA"),
    ("ADAPTIVE_RISK_ENABLED","YES","Activar freno adaptativo por logs"),
    ("ADAPTIVE_BASELINE_MODE","RECENT_HIGH","Base de comparacion del patrimonio"),
    ("ADAPTIVE_BASELINE_USD","0","Base fija de comparacion"),
    ("ADAPTIVE_WARN_LOSS_PCT","0.05","Caida para advertencia"),
    ("ADAPTIVE_REDUCE_LOSS_PCT","0.10","Caida para reducir riesgo"),
    ("ADAPTIVE_HALT_LOSS_PCT","0.20","Caida para bloquear compras"),
    ("ADAPTIVE_WARN_TRADE_MULTIPLIER","1.00","Multiplicador de compra en advertencia"),
    ("ADAPTIVE_REDUCE_TRADE_MULTIPLIER","0.75","Multiplicador de compra en reduccion"),
    ("ADAPTIVE_MIN_CONFIDENCE_ADD","0.01","Ajuste de confianza minima"),
    ("ADAPTIVE_ALLOW_RISK_REDUCING_SELLS","YES","Permitir ventas defensivas"),
    ("AUTO_REPAIR_LOT_STEPS","YES","Reparar precision de lotes al detectar rechazos"),
    ("SUPERVISOR_RESERVE_STEP","0.01","Paso de reduccion de reserva del supervisor"),
    ("SUPERVISOR_RESERVE_FLOOR","0.20","Piso de reserva del supervisor"),
    ("SUPERVISOR_CONFIDENCE_RAISE_STEP","0.02","Paso de ajuste de confianza del supervisor"),
    ("SUPERVISOR_RESERVE_RAISE_STEP","0.00","Paso de ajuste al alza de reserva del supervisor"),
    ("TRADING_SWARM_ENABLED","NO","Activar swarm de IA"),
    ("TRADING_SWARM_MODE","ROTATE","Modo del swarm"),
    ("TRADING_SWARM_ROLES","SCOUT,CHALLENGER,RISK,ALLOCATOR,AUDITOR","Roles del swarm"),
    ("TRADING_SWARM_CYCLE_STRIDE","1","Ciclos por rol"),
    ("TRADING_SWARM_MAX_CHUNK","1","Tamano maximo de bloque por rol"),
    ("UNIVERSE_AUTOPILOT_ENABLED","YES","Activar autopiloto de universo"),
    ("UNIVERSE_AUTOPILOT_APPLY","YES","Aplicar cambios de universo"),
    ("UNIVERSE_AUTOPILOT_CORE","BTC,ETH","Activos nucleo siempre protegidos"),
    ("UNIVERSE_AUTOPILOT_MAX_INCLUDE","4","Maximo de activos extra a incluir"),
    ("UNIVERSE_AUTOPILOT_MIN_SCORE","0.05","Puntaje minimo para incluir"),
    ("UNIVERSE_AUTOPILOT_MIN_BUY_CONFIDENCE","0.60","Confianza minima para incluir"),
    ("UNIVERSE_AUTOPILOT_EXCLUDE_FLOOR","-0.25","Puntaje para excluir extras"),
    ("UNIVERSE_AUTOPILOT_KEEP_HOLDINGS","YES","No excluir activos con tenencia"),
    ("UNIVERSE_AUTOPILOT_REFRESH_EVERY_N_CYCLES","3","Revisar universo cada N ciclos"),
    ("UNIVERSE_AUTOPILOT_REFRESH_EVERY_N_SECONDS","900","Revisar universo cada N segundos"),
    ("SUPERVISOR_EXECUTE","NO","Permitir ejecucion desde supervisor"),
    ("SUPERVISOR_INTERVAL_SECONDS","900","Intervalo del supervisor"),
]


def read_existing():
    values={}
    if os.path.exists(PATH):
        for line in open(PATH,encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k,v=line.rstrip("\n").split("=",1)
                values[k]=v
    return values


def write_values(values):
    fd,temp=tempfile.mkstemp(prefix=".env.",dir=ROOT,text=True)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            for k,v in values.items():
                f.write(f"{k}={v}\n")
        os.chmod(temp,0o600)
        os.replace(temp,PATH)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    values=read_existing()
    print("Configurar parametros. Enter conserva el valor entre corchetes.\n")
    for key,default,label in PARAMS:
        current=values.get(key,default)
        new=input(f"{label} · {key} [{current}]: ").strip()
        if new:
            values[key]=new
        else:
            values.setdefault(key,current)
    write_values(values)
    print("Parametros guardados. Claves/API no modificadas.")

if __name__=="__main__":
    main()

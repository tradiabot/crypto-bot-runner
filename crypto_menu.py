#!/usr/bin/env python3
import os, subprocess
from ollama_runtime import cleanup_loaded_models, ensure_server
ROOT=os.path.dirname(os.path.abspath(__file__))

def read_env_value(key, default=""):
    path=os.path.join(ROOT,".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith(key+"="):
                    return line.rstrip("\n").split("=",1)[1]
    except FileNotFoundError:
        pass
    return os.getenv(key, default)

def ensure_ollama():
    if read_env_value("AI_PROVIDER","ollama").strip().lower() != "ollama":
        print(f"Proveedor IA: {read_env_value('AI_PROVIDER','ollama')} (Ollama queda como fallback opcional)",flush=True)
        return True
    print("Verificando servidor Ollama...", flush=True)
    cleanup_loaded_models()
    ok, detail = ensure_server(wait_seconds=20)
    if ok:
        print(f"Ollama servidor disponible: {detail}", flush=True)
        return True
    print(f"Ollama no esta listo: {detail}", flush=True)
    print("El menu abrira, pero las opciones con IA pueden fallar hasta que Ollama responda.", flush=True)
    return False

def run(cmd):
    print("⏳ Progreso 0% · iniciando proceso...", flush=True)
    try:
        result=subprocess.run(cmd,cwd=ROOT,env=os.environ.copy())
    except KeyboardInterrupt:
        print("\n⏹ Proceso detenido. Regresando al menú.")
        return
    print("✅ Progreso 100% · proceso terminado." if result.returncode == 0 else f"⚠ Proceso terminado con código {result.returncode}.")

def run_live():
    print("\033[91mTRADING REAL: las operaciones pueden generar pérdidas.\033[0m")
    print(f"Exchange seleccionado: {os.getenv('EXCHANGE','crypto_com')} · límite por operación: 2")
    if input("Escribe ACTIVAR REAL para iniciar: ").strip() != "ACTIVAR REAL":
        print("Activación cancelada.")
        return
    print("\033[91mTRADING REAL ACTIVADO para esta sesión. Ctrl+C vuelve al menú.\033[0m")
    try:
        subprocess.run(["./crypto-live"],cwd=ROOT,env=os.environ.copy())
    except KeyboardInterrupt:
        print("\nTrading real detenido. Regresando al menú.")

def show_termux_persistence():
    print("""
Persistencia Termux
1) Instala la app Termux:Boot.
2) En Termux ejecuta:
   cd ~/projects/crypto-bot
   crypto-bot install
3) Inicia el bot con:
   crypto-bot start
4) Verifica estado con:
   crypto-bot status
5) Deténlo con:
   crypto-bot stop
6) Desactiva optimización de batería para Termux y Termux:Boot.
7) Reinicia el teléfono y confirma que existe ~/.termux/boot/crypto-bot.sh
""".strip())
    input("Pulsa Enter para continuar...")


def menu():
    print("\033[2J\033[H\033[96m╭────────────────────────────────────────────╮")
    print("│              CRYPTO.COM · AI BOT           │")
    print("╰────────────────────────────────────────────╯\033[0m")
    print("1) Estado y balance")
    print("2) Escanear oportunidades BTC/ETH/SOL")
    print("3) Escanear activos personalizados")
    print("4) Cotizar y confirmar operación")
    print("5) Configuración actual")
    print("6) Trading automático (ARMED)")
    print("7) Trading automático continuo (ARMED)")
    print("8) Activar trading automático REAL")
    print("9) Configurar exchange y API")
    print("10) Ver logs y feedback")
    print("11) Ver rendimiento")
    print("12) Configurar parámetros de trading")
    print("13) Configurar proveedor y modelo IA")
    print("14) Supervisor Codex")
    print("15) Supervisor Codex continuo")
    print("16) Persistencia Termux")
    print("17) Guía Estado Termux")
    return input("\nSelecciona una opción: ").strip()

def main():
    ensure_ollama()
    while True:
        try: choice=menu()
        except (EOFError, KeyboardInterrupt): print("\nSaliendo."); return
        if choice=="1": run(["./crypto-bot","status"])
        elif choice=="2": run(["./start-bot","--plain"])
        elif choice=="3":
            assets=input("Activos separados por coma (ej. BTC,ETH,SOL): ").upper().replace(" ","")
            run(["./start-bot","--plain",*[x for x in assets.split(",") if x]])
        elif choice=="4":
            asset=input("Activo destino [BTC]: ").strip().upper() or "BTC"
            amount=input("Importe USDC [2]: ").strip() or "2"
            run(["./crypto-trade",asset,amount])
        elif choice=="5":
            print(f"Exchange configurado: {read_env_value('EXCHANGE','crypto_com')}")
            print(f"Modelo IA: {read_env_value('OLLAMA_MODEL','qwen3:0.6b')} | Fuente: USDC | Max compra Crypto.com: {read_env_value('CRYPTO_COM_MAX_TRADE_USDC','2')} USDC")
            print(f"Universo base: {read_env_value('TRADE_UNIVERSE','BTC,ETH,SOL,CRO,LINK,POL')}")
            print(f"Incluir extra: {read_env_value('TRADE_INCLUDE','(vacío)')} | Excluir: {read_env_value('TRADE_EXCLUDE','(vacío)')}")
            print(f"Estrategias: DCA, TREND, PRICE_TRACKER | auto-ejecución: {read_env_value('AUTO_EXECUTE','NO')} | modo real: {read_env_value('AUTO_LIVE','NO')}")
            print(f"Intervalo continuo: {read_env_value('AUTO_INTERVAL_SECONDS','300')}s | escaneo completo cada: {read_env_value('AUTO_FULL_SCAN_EVERY_N_CYCLES','1')} ciclo(s)")
            print(f"Autopiloto universo: {read_env_value('UNIVERSE_AUTOPILOT_ENABLED','NO')} | aplicar cambios: {read_env_value('UNIVERSE_AUTOPILOT_APPLY','NO')} | núcleo: {read_env_value('UNIVERSE_AUTOPILOT_CORE','BTC,ETH')}")
            input("Pulsa Enter para continuar...")
        elif choice=="6": run(["./crypto-auto"])
        elif choice=="7": run(["./crypto-auto-loop"])
        elif choice=="8": run_live()
        elif choice=="9": run(["./configure-env"])
        elif choice=="10": run(["python3","-c","from bot_logger import print_feedback; print_feedback()"] )
        elif choice=="11": run(["python3","-c","from performance_tracker import print_performance; print_performance()"] )
        elif choice=="12": run(["./configure-params"])
        elif choice=="13": run(["./configure-ai"])
        elif choice=="14": run(["./crypto-supervisor"])
        elif choice=="15": run(["python3","./crypto_supervisor.py","--loop"])
        elif choice=="16": show_termux_persistence()
        elif choice=="17": show_termux_persistence()
        elif choice=="0": return
        else: print("Opción no válida")
        try: input("\nPulsa Enter para volver al menú...")
        except (EOFError, KeyboardInterrupt): print("\nSaliendo."); return
if __name__=="__main__": main()

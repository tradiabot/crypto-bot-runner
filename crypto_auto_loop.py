#!/usr/bin/env python3
import os, subprocess, time, json, fcntl, signal
from bot_logger import log_event
ROOT=os.path.dirname(os.path.abspath(__file__))
STATE_DIR=os.path.join(ROOT, ".runtime")
LOOP_LOCK_PATH=os.path.join(STATE_DIR, "crypto-auto-loop.lock")
HEARTBEAT_PATH=os.path.join(STATE_DIR, "crypto-auto-heartbeat.json")

def acquire_loop_lock():
    os.makedirs(STATE_DIR, exist_ok=True)
    lock=open(LOOP_LOCK_PATH, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.seek(0)
        owner=lock.read().strip() or "propietario desconocido"
        lock.close()
        print(f"AUTO LOOP no iniciado: ya existe otra instancia activa ({owner})", flush=True)
        log_event("auto_loop_instance_skip", owner=owner)
        return None
    lock.seek(0); lock.truncate()
    lock.write(json.dumps({"pid":os.getpid(),"ts":time.time()})); lock.flush()
    return lock

def load_env_file():
    path=os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    locked={"AUTO_EXECUTE","AUTO_LIVE","CONFIRM_LIVE","SUPERVISOR_EXECUTE"}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line=raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value=line.split("=", 1)
            key=key.strip()
            if key in locked and key in os.environ:
                continue
            os.environ[key]=value.strip().strip('"').strip("'")
def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)

def write_heartbeat(status, cycle, **extra):
    payload={"pid":os.getpid(),"status":status,"cycle":cycle,"epoch":time.time(),**extra}
    tmp=HEARTBEAT_PATH+".tmp"
    try:
        with open(tmp,"w",encoding="utf-8") as handle:
            json.dump(payload,handle)
        os.replace(tmp,HEARTBEAT_PATH)
    except OSError:
        pass

def terminate_cycle(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=5)
        return
    except OSError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            proc.wait()
        except OSError:
            return

def request_shutdown(_signum, _frame):
    raise KeyboardInterrupt

def run_cycle(cycle):
    scan_timeout=env_int("AUTO_SCAN_TIMEOUT_SECONDS", 180)
    configured_timeout=env_int("AUTO_CYCLE_WATCHDOG_SECONDS", 360)
    timeout=max(120, configured_timeout, scan_timeout+60)
    proc=subprocess.Popen(["./crypto-auto"],cwd=ROOT,env=os.environ.copy(),start_new_session=True)
    started=time.monotonic()
    deadline=started+timeout
    try:
        while True:
            returncode=proc.poll()
            if returncode is not None:
                write_heartbeat("idle",cycle,returncode=returncode)
                return returncode
            now=time.monotonic()
            if now >= deadline:
                terminate_cycle(proc)
                log_event("auto_cycle_watchdog_timeout",cycle=cycle,timeout_seconds=timeout)
                write_heartbeat("timeout",cycle,timeout_seconds=timeout)
                print(f"AUTO LOOP: ciclo {cycle} excedió {timeout}s; terminado y se reintentará",flush=True)
                return 124
            write_heartbeat(
                "running",
                cycle,
                child_pid=proc.pid,
                timeout_seconds=timeout,
                elapsed_seconds=int(now-started),
            )
            time.sleep(min(5.0,max(0.1,deadline-now)))
    except KeyboardInterrupt:
        terminate_cycle(proc)
        raise

def main():
    signal.signal(signal.SIGTERM, request_shutdown)
    load_env_file()
    loop_lock=acquire_loop_lock()
    if loop_lock is None:
        return 0
    if os.getenv("AUTO_LIVE", "NO") == "YES":
        os.environ["AUTO_EXECUTE"] = "YES"
        os.environ["CONFIRM_LIVE"] = "YES"
    try:
        interval=int(os.getenv('AUTO_INTERVAL_SECONDS','300'))
    except ValueError:
        interval=300
    interval=max(60, interval)
    try:
        periodic_scan_cycles=max(1, int(os.getenv("AUTO_FULL_SCAN_EVERY_N_CYCLES","1")))
    except ValueError:
        periodic_scan_cycles=1
    try:
        periodic_scan_seconds=float(os.getenv("AUTO_FULL_SCAN_EVERY_N_SECONDS","0") or 0)
    except ValueError:
        periodic_scan_seconds=0.0
    print(f'AUTO LOOP activo · modo {"REAL" if os.getenv("AUTO_EXECUTE") == "YES" else "ARMED"} · intervalo {interval}s · Ctrl+C para volver al menú', flush=True)
    log_event('auto_loop_start', interval_seconds=interval)
    try:
        cycle=0
        last_full_scan=time.time()
        while True:
            load_env_file()
            cycle += 1
            os.environ["AUTO_CYCLE_INDEX"]=str(cycle)
            full_scan_by_cycle=(cycle % periodic_scan_cycles == 0)
            full_scan_by_time=bool(periodic_scan_seconds > 0 and (time.time() - last_full_scan) >= periodic_scan_seconds)
            full_scan_now=full_scan_by_cycle or full_scan_by_time
            os.environ["AUTO_FULL_SCAN_NOW"]="YES" if full_scan_now else "NO"
            if full_scan_now:
                last_full_scan=time.time()
            returncode=run_cycle(cycle)
            log_event("auto_loop_cycle_end", returncode=returncode)
            if returncode not in (0,130): print("Ciclo terminado con error; se reintentará en el siguiente intervalo")
            print()
            try:
                for remaining in range(interval,0,-1):
                    if remaining == interval or remaining % 30 == 0 or remaining <= 10:
                        write_heartbeat("idle",cycle,next_cycle_seconds=remaining)
                        print(f"\033[2K\rPróximo ciclo en {remaining:03d}s", end="", flush=True)
                    time.sleep(1)
                print("\033[2K\rPróximo ciclo en 000s · iniciando nuevo ciclo", flush=True)
            except KeyboardInterrupt:
                raise
    except KeyboardInterrupt:
        log_event('auto_loop_stop')
        print('\nAUTO LOOP detenido. Regresando al menú.')
        return 0
    finally:
        try:
            fcntl.flock(loop_lock, fcntl.LOCK_UN)
            loop_lock.close()
            if os.path.exists(LOOP_LOCK_PATH): os.unlink(LOOP_LOCK_PATH)
        except Exception:
            pass
if __name__=='__main__': main()

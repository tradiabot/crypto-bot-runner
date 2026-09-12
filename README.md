# Crypto.com OLMo Trading CLI

CLI de trading con Groq y ejecución spot mediante un adaptador de Crypto.com.

También admite GroqCloud como proveedor remoto, con fallback local y técnico:

```bash
AI_PROVIDER=groq
AI_FALLBACK_PROVIDERS=ollama,technical
GROQ_API_KEY=tu_clave
GROQ_MODEL=openai/gpt-oss-20b
```

En modo final cloud-only, Groq y Crypto.com se guardan como secretos del runner remoto,
no dentro del teléfono ni en la APK. El archivo `.env` local queda solo para pruebas manuales.
Configura el proveedor local, si lo necesitas, con:

```bash
./configure-ai
```

## Seguridad

No se incluyen claves en el repositorio. Revoca las credenciales expuestas y crea unas nuevas con permisos mínimos de lectura/trading, sin retiros. Carga las nuevas claves en `.env` local.

## Runner cloud-only

El teléfono no ejecuta el motor. Cloudflare guarda el estado deseado y el runner remoto
ejecuta un solo ciclo cuando el estado es `running`.

Opción gratis simple: GitHub Actions. Configura estos secretos en el repo del bot:

```text
CLOUDFLARE_API_URL=https://crypto-bot-api.TU_SUBDOMINIO.workers.dev
RUNNER_TOKEN=un-token-largo-aleatorio
GROQ_API_KEY=...
CDC_API_KEY=...
CDC_API_SECRET=...
```

El workflow `.github/workflows/cloudflare-runner.yml` corre cada 5 minutos o manualmente.
Si Cloudflare está en `paused`, reporta estado y no toca Crypto.com. Si está en `running`,
ejecuta `cloudflare_runner.py`, que a su vez corre un ciclo de `crypto_auto.py`, aplica los
límites recibidos desde D1 y reporta logs/recibos de vuelta al Worker.

Si tienes `GH_TOKEN` o `GITHUB_TOKEN`, el alta se puede automatizar:

```bash
GROQ_API_KEY=... GH_TOKEN=... ./publish_github_runner.sh
```

## Preparación local manual

```bash
ollama pull olmo-3:7b-instruct
cp .env.example .env
python3 crypto_bot.py
```

El motor de riesgo valida las señales antes de enviar órdenes. `Ctrl+C` detiene el proceso.

## Termux legado

Para mantener el bot vivo en Android usa:

```bash
crypto-bot boot
crypto-bot start
```

Para ver estado:

```bash
crypto-bot status
```

Para seguir ambos logs o abrir las ventanas persistentes:

```bash
crypto-bot logs
tmux attach -t crypto-termux
```

Para detenerlo:

```bash
crypto-bot stop
```

El arranque crea una ventana `watchdog` y otra `logs`. El estado interno se valida con `.runtime/crypto-auto-heartbeat.json`; si deja de actualizarse, el watchdog reinicia el proceso completo.

Si instalaste `Termux:Boot`, el archivo `~/.termux/boot/crypto-bot.sh` lanzara el arranque automatico tras reiniciar el telefono.

#!/usr/bin/env bash
set -euo pipefail

repo_name="${GITHUB_REPO_NAME:-crypto-bot-runner}"
repo_visibility="${GITHUB_REPO_VISIBILITY:-private}"
cloudflare_url="${CLOUDFLARE_API_URL:-https://crypto-bot-api.crypto-bot-desk.workers.dev}"
runner_token_file="${RUNNER_TOKEN_FILE:-/root/projects/crypto-bot-app/.runtime/runner_token}"

if [[ -z "${GH_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  echo "Falta GH_TOKEN o GITHUB_TOKEN con permisos repo/actions secrets" >&2
  exit 2
fi

export GH_TOKEN="${GH_TOKEN:-$GITHUB_TOKEN}"

if ! command -v gh >/dev/null 2>&1; then
  echo "Falta gh. Instala GitHub CLI primero." >&2
  exit 2
fi

cd "$(dirname "$0")"

read_env() {
  local key="$1"
  python3 - "$key" <<'PY'
import sys
from pathlib import Path
key=sys.argv[1]
path=Path(".env")
if not path.exists():
    raise SystemExit(1)
for raw in path.read_text(encoding="utf-8").splitlines():
    if "=" not in raw or raw.lstrip().startswith("#"):
        continue
    k,v=raw.split("=",1)
    if k.strip()==key:
        print(v.strip().strip('"').strip("'"))
        raise SystemExit(0)
raise SystemExit(1)
PY
}

runner_token="$(cat "$runner_token_file")"
groq_key="${GROQ_API_KEY:-}"
if [[ -z "$groq_key" ]]; then
  echo "Falta GROQ_API_KEY en el entorno para cargarlo en GitHub" >&2
  exit 2
fi

cdc_key="${CDC_API_KEY:-$(read_env CDC_API_KEY 2>/dev/null || read_env CRYPTO_COM_API_KEY)}"
cdc_secret="${CDC_API_SECRET:-$(read_env CDC_API_SECRET 2>/dev/null || read_env CRYPTO_COM_API_SECRET)}"

if [[ -z "$cdc_key" || -z "$cdc_secret" ]]; then
  echo "Faltan CDC_API_KEY/CDC_API_SECRET" >&2
  exit 2
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git init
fi

git status --short

if ! gh repo view "$repo_name" >/dev/null 2>&1; then
  gh repo create "$repo_name" "--${repo_visibility}" --source=. --remote=origin --push
else
  if ! git remote get-url origin >/dev/null 2>&1; then
    owner="$(gh api user --jq .login)"
    git remote add origin "https://github.com/${owner}/${repo_name}.git"
  fi
  git push -u origin HEAD
fi

repo="$(gh repo view "$repo_name" --json nameWithOwner --jq .nameWithOwner)"

printf '%s' "$cloudflare_url" | gh secret set CLOUDFLARE_API_URL -R "$repo"
printf '%s' "$runner_token" | gh secret set RUNNER_TOKEN -R "$repo"
printf '%s' "$groq_key" | gh secret set GROQ_API_KEY -R "$repo"
printf '%s' "$cdc_key" | gh secret set CDC_API_KEY -R "$repo"
printf '%s' "$cdc_secret" | gh secret set CDC_API_SECRET -R "$repo"

gh workflow run "Cloudflare Crypto Runner" -R "$repo"
echo "Runner GitHub preparado en $repo"

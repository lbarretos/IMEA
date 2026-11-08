#!/bin/bash
# Atualização da base IMEA em dias úteis (modo incremental).
# Pensado para o launchd (instalado por scripts/install_schedule.sh, seg–sex 19:00),
# mas pode ser executado manualmente a qualquer momento:
#   bash scripts/update_daily.sh
#
# Configuração lida do .env do projeto:
#   IMEA_DATASETS_DIR    onde gravar (obrigatório aqui: sem ele não há como saber se o disco sumiu)
# Variáveis opcionais:
#   ESPERA_REDE_SEG=300  quanto esperar a rede subir (ex. logo após o Mac acordar)

set -u
set -o pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ESPERA_REDE_SEG="${ESPERA_REDE_SEG:-300}"

if [ -z "${IMEA_DATASETS_DIR:-}" ] && [ -f "$PROJECT_DIR/.env" ]; then
  IMEA_DATASETS_DIR="$(sed -n 's/^IMEA_DATASETS_DIR=//p' "$PROJECT_DIR/.env" | tail -1 | tr -d '"'"'")"
fi
DATASETS_DIR="${IMEA_DATASETS_DIR:-}"

STAMP_FILE="$HOME/Library/Logs/imea/.ultimo_sucesso"
MODO=sempre
[ "${1:-}" = "--se-vencido" ] && MODO=se_vencido

# Última janela esperada: o 19:00 de dia útil mais recente que já passou
ultima_janela() {
  local now t dow
  now=$(date +%s)
  for d in 0 1 2 3 4 5 6 7; do
    t=$(date -v-"${d}"d -v19H -v0M -v0S +%s)
    dow=$(date -r "$t" +%u)
    [ "$dow" -le 5 ] && [ "$t" -le "$now" ] && { echo "$t"; return; }
  done
}

if [ "$MODO" = se_vencido ]; then
  ultimo=$(head -1 "$STAMP_FILE" 2>/dev/null | cut -d' ' -f1)
  [[ "$ultimo" =~ ^[0-9]+$ ]] || ultimo=0
  janela=$(ultima_janela)
  if [ "$ultimo" -ge "$janela" ]; then exit 0; fi   # em dia: sai em silêncio
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') recuperando janela perdida de $(date -r "$janela" '+%F %T')"
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') início"

if [ -z "$DATASETS_DIR" ]; then
  echo "ERRO: defina IMEA_DATASETS_DIR no .env. Nada foi gravado."
  exit 1
fi

# Sem o disco, o extrator gravaria em outro lugar e a base se dividiria em duas
if [ ! -d "$DATASETS_DIR" ]; then
  echo "ERRO: $DATASETS_DIR não está acessível (disco desmontado ou sem permissão). Nada foi gravado."
  exit 1
fi

waited=0
until /usr/bin/nc -z -G 5 api1.imea.com.br 443 >/dev/null 2>&1; do
  if [ "$waited" -ge "$ESPERA_REDE_SEG" ]; then
    echo "ERRO: api1.imea.com.br inacessível após ${ESPERA_REDE_SEG}s."
    exit 1
  fi
  sleep 10
  waited=$((waited + 10))
done

cd "$PROJECT_DIR" || exit 1
"$PROJECT_DIR/.venv/bin/python" imea_extractor.py --mode incremental --output-dir "$DATASETS_DIR"
status=$?

[ "$status" -eq 0 ] && { mkdir -p "$(dirname "$STAMP_FILE")"; echo "$(date +%s) $(date '+%F %T')" >"$STAMP_FILE"; }
echo "=== $(date '+%Y-%m-%d %H:%M:%S') fim (exit $status)"
exit "$status"

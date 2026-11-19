#!/usr/bin/env bash
# Prepara um PostgreSQL local SOMENTE PARA TESTES: cria os papéis (init-roles.sql) e o banco.
# Uso: scripts/test_db_setup.sh   (superusuário `postgres`; variáveis PGHOST/PGPORT/PGUSER opcionais)
set -euo pipefail

cd "$(dirname "$0")/.."
export PGHOST="${PGHOST:-127.0.0.1}" PGPORT="${PGPORT:-5432}" PGUSER="${PGUSER:-postgres}"
export POSTGRES_DB="${POSTGRES_DB:-freelance_agent}"
export FA_OWNER_PASSWORD="${FA_OWNER_PASSWORD:-senha-teste-owner}"
export FA_APP_PASSWORD="${FA_APP_PASSWORD:-senha-teste-app}"

existe() { psql -qtAX -d postgres -c "$1" | grep -q 1; }

if ! existe "SELECT 1 FROM pg_database WHERE datname = '${POSTGRES_DB}'"; then
  psql -qX -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"${POSTGRES_DB}\""
fi

if ! existe "SELECT 1 FROM pg_roles WHERE rolname = 'fa_owner'"; then
  psql -qX -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 -f docker/db/init-roles.sql
fi

# Só para testes: o pytest-django cria o banco de teste. Em produção fa_owner não tem CREATEDB.
psql -qX -d postgres -v ON_ERROR_STOP=1 -c "ALTER ROLE fa_owner CREATEDB"

echo "Pronto. Exporte:"
echo "  DATABASE_URL=postgres://fa_owner:${FA_OWNER_PASSWORD}@${PGHOST}:${PGPORT}/${POSTGRES_DB}"
echo "  DATABASE_URL_APP=postgres://fa_app:${FA_APP_PASSWORD}@${PGHOST}:${PGPORT}/${POSTGRES_DB}"

-- Criação dos papéis no primeiro boot do PostgreSQL (executado pela imagem oficial como superusuário).
-- fa_owner: dono do banco e do schema; usado só pelo `migrate`. Sem CREATEDB em produção.
-- fa_app: usado por web, worker e bot; sem posse, sem DDL, sem privilégios administrativos.
-- Senhas lidas do ambiente (psql 15+); nunca gravadas aqui.
\set ON_ERROR_STOP on
\getenv fa_owner_password FA_OWNER_PASSWORD
\getenv fa_app_password FA_APP_PASSWORD
\getenv fa_database POSTGRES_DB

CREATE ROLE fa_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'fa_owner_password';
CREATE ROLE fa_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT PASSWORD :'fa_app_password';

ALTER DATABASE :"fa_database" OWNER TO fa_owner;
GRANT CONNECT ON DATABASE :"fa_database" TO fa_app;

\connect :"fa_database"
ALTER SCHEMA public OWNER TO fa_owner;

"""Privilégios do papel de aplicação (fa_app) sobre objetos criados pelo dono (quem roda `migrate`).

O dono é o usuário conectado (fa_owner); o papel de aplicação vem de `settings.DB_APP_ROLE`.
Os privilégios padrão valem só para objetos futuros criados pelo dono no schema public.
"""

from django.conf import settings
from django.db import migrations
from psycopg import sql


def conceder_privilegios(apps, schema_editor):
    app_role = getattr(settings, "DB_APP_ROLE", "fa_app")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        dono = cursor.fetchone()[0]
        for comando in (
            "GRANT USAGE ON SCHEMA public TO {app}",
            "ALTER DEFAULT PRIVILEGES FOR ROLE {dono} IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app}",
            "ALTER DEFAULT PRIVILEGES FOR ROLE {dono} IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO {app}",
        ):
            cursor.execute(
                sql.SQL(comando).format(app=sql.Identifier(app_role), dono=sql.Identifier(dono))
            )


def revogar_privilegios(apps, schema_editor):
    app_role = getattr(settings, "DB_APP_ROLE", "fa_app")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        dono = cursor.fetchone()[0]
        for comando in (
            "ALTER DEFAULT PRIVILEGES FOR ROLE {dono} IN SCHEMA public "
            "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {app}",
            "ALTER DEFAULT PRIVILEGES FOR ROLE {dono} IN SCHEMA public "
            "REVOKE USAGE, SELECT ON SEQUENCES FROM {app}",
            "REVOKE USAGE ON SCHEMA public FROM {app}",
        ):
            cursor.execute(
                sql.SQL(comando).format(app=sql.Identifier(app_role), dono=sql.Identifier(dono))
            )


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    # Os privilégios padrão precisam existir ANTES de qualquer tabela criada pelo `migrate`.
    run_before = [
        ("contenttypes", "0001_initial"),
        ("auth", "0001_initial"),
        ("procrastinate", "0001_initial"),
    ]
    operations = [migrations.RunPython(conceder_privilegios, revogar_privilegios)]

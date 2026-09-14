"""Copy production (Railway) data into the local dev database.

Local equivalent of the prod -> stage DB sync
(`.github/workflows/sync-stage.yml`), as a single command:

    python manage.py copy_prod_to_local

Reads PG_* from `.env` (production) and writes into the database configured
in `.env.local`. Truncates local tables first, then copies every row and
fixes sequences. Prod is only ever READ.

Requires:
- local Postgres running (see README, "Base de datos local PostgreSQL")
- `.env` (prod) and `.env.local` (local) present
- the local role to be SUPERUSER (`moctezuma_dev` already is)

Options:
    --yes          skip the confirmation prompt (it truncates the local DB)
    --skip-migrate skip `migrate` before copying (schema already current)
"""

import os
import subprocess
import sys

import psycopg2
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from dotenv import dotenv_values

COPY_SCRIPT = "scripts/copy_prod_to_local.py"


class Command(BaseCommand):
    help = (
        "Copy current production data into the local dev database "
        "(prod is never written, only read)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the confirmation prompt (the local DB gets truncated).",
        )
        parser.add_argument(
            "--skip-migrate",
            action="store_true",
            help="Skip `migrate` before copying (use if the local schema is already current).",
        )

    def handle(self, *args, **options):
        self._check_env_files()
        local_env = self._connect_local()
        self._check_local_superuser(local_env)

        if not options["skip_migrate"]:
            self.stdout.write(self.style.MIGRATE_HEADING("Migrando el esquema local…"))
            call_command("migrate", interactive=False, verbosity=self.verbosity)

        self._confirm(options["yes"])
        self._run_copy_script()

    def _check_env_files(self):
        for name in (".env", ".env.local"):
            if not os.path.isfile(name):
                raise CommandError(
                    f"Falta el archivo {name}. Crea uno siguiendo el README: "
                    "`.env` con las credenciales de producción y `.env.local` "
                    "con las locales (PG_HOST/PORT/USER/PASSWORD/DB)."
                )

    def _connect_local(self):
        local_env = dotenv_values(".env.local")
        missing = [k for k in ("PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DB")
                   if not local_env.get(k)]
        if missing:
            raise CommandError(
                f".env.local no define {' , '.join(missing)} (ver README)."
            )
        try:
            conn = psycopg2.connect(
                dbname=local_env["PG_DB"],
                host=local_env["PG_HOST"],
                port=local_env["PG_PORT"],
                user=local_env["PG_USER"],
                password=local_env["PG_PASSWORD"],
            )
        except psycopg2.OperationalError as exc:
            raise CommandError(
                "No se pudo conectar al Postgres local (" + str(exc) + ").\n"
                "Arranca el servidor primero:\n"
                "  macOS: brew services start postgresql@14\n"
                "  Linux: sudo systemctl start postgresql\n"
                "Verifica con: pg_isready -h 127.0.0.1 -p 5432"
            ) from exc
        return conn

    def _check_local_superuser(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            is_super = cur.fetchone()[0]
        conn.close()
        if not is_super:
            self.stderr.write(self.style.WARNING(
                "Aviso: el rol local no es SUPERUSER. El script usa "
                "session_replication_role = replica, así que la copia puede "
                "fallar por el orden de las FK."
            ))

    def _confirm(self, yes):
        if yes:
            return
        answer = input(
            "Esto VACÍA la base local y la reemplaza con datos de producción. "
            "¿Continuar? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            self.stdout.write("Abortado.")
            raise SystemExit(0)

    def _run_copy_script(self):
        script = os.path.join(settings.BASE_DIR, COPY_SCRIPT)
        self.stdout.write(f"Copiando datos de producción a local ({COPY_SCRIPT})…")
        rc = subprocess.call([sys.executable, script], cwd=settings.BASE_DIR)
        if rc != 0:
            raise CommandError("La copia falló — revisa la salida del script.")
        self.stdout.write(self.style.SUCCESS("Listo: la base local es un espejo de producción."))
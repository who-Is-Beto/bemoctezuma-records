# 💿 Moctezuma Records Backend 🛜

Este es el repositorio de la web de Moctezuma Records, basado en Python y Django.
A continuación tendrás que seguir pasos para hacer correr este repo en tu local.

## Antes de correr, necesitarás ‼️

Generar ambiente de desarrollo con python 🐍

```bash
python -m venv ecommerceEnv
```

Activar el ambiente de desarrollo 💻

```bash
source ecommerceEnv/bin/activate
```

Para detener el ambiente de desarrollo ✋

```bash
deactivate
```

## Correr proyecto 🏃🏻

Instala dependencias primero ⬇️ (No olvides estar en el hambiente de desarrollo de Python)

```bash
  pip install -r requirements.txt
```

### Variables de entorno 🌐

El proyecto carga variables de `.env` (producción) y de `.env.local` (desarrollo local, **no versionado** — está en `.gitignore`). Los valores de `.env.local` tienen prioridad.

- `DB=true` + `PG_*` → usa **PostgreSQL**. Si `DB` está vacío/ausente → usa SQLite (`db.sqlite3`).
- `REQUIRE_EMAIL_VERIFICATION=true` → obliga a que el usuario verifique su correo antes de hacer login (ver sección de verificación de email abajo).
- `FRONTEND_URL` → base URL del frontend (usada para construir el enlace de verificación de email).
- `DEBUG`, `DJANGO_SECRET_KEY`, `STRIPE_*`, `EMAIL_*` → como en producción.

### Base de datos local PostgreSQL 🗄️

1. Asegúrate de que Postgres esté corriendo:

   **macOS (Homebrew):**

   ```bash
   brew services start postgresql@14
   ```

   **Linux (systemd — Ubuntu, Debian, Fedora, Arch):**

   ```bash
   sudo systemctl start postgresql
   sudo systemctl enable postgresql   # opcional: auto-inicio al arrancar
   ```

   **Linux (Debian/Ubuntu con `postgresql-14` / init script):**

   ```bash
   sudo service postgresql start
   # o por clúster específico:
   sudo pg_ctlcluster 14 main start
   ```

   **Docker (alternativa):**

   ```bash
   docker start <nombre-del-contenedor>   # o: docker compose up -d postgres
   ```

   Verifica que acepte conexiones antes de seguir:

   ```bash
   pg_isready -h 127.0.0.1 -p 5432   # → "accepting connections"
   ```

2. Crea el rol y la base de datos (si no existen):

   ```bash
   createuser --login --createdb moctezuma_dev    # si no existe
   createdb --owner moctezuma_dev moctezuma_records_dev
   ```

3. En `.env.local` pon:

   ```
   DB=true
   PG_HOST=127.0.0.1
   PG_PORT=5432
   PG_USER=moctezuma_dev
   PG_PASSWORD=<tu contraseña local>
   PG_DB=moctezuma_records_dev
   ```

4. Copia los datos de producción a tu base local (lectura **solo lectura** desde Railway, nunca escribe en prod) — el equivalente local del sync prod → stage:

   ```bash
   python manage.py copy_prod_to_local
   ```

   El comando verifica que Postgres local esté arriba, corre `migrate` sobre el esquema local y luego copia todo (truncando antes las tablas locales). Pide confirmación antes de vaciar la base — usa `--yes` para saltarla y `--skip-migrate` si el esquema ya está al día.

   Alternativa directa (mismo mecanismo; conecta con las credenciales de `.env` — prod — y restaura en `.env.local`):

   ```bash
   python scripts/copy_prod_to_local.py
   ```

### Cómo funciona la copia 🔍

La copia es un **snapshot de la base de datos**: trae las filas de todas las tablas de producción y reemplaza por completo el contenido de tu base local, conservando tu esquema (columnas/migraciones). Producción jamás se toca: la conexión a prod solo hace `SELECT`s.

**Qué se copia y qué no:**

- ✅ Todas las tablas de prod (usuarios, discos, órdenes, carrito, reviews, bazares…).
- ✅ Solo las columnas **en común** entre prod y local. Si tu esquema local tiene columnas que prod aún no (un cambio sin desplegar), se rellenan con su default; `email_verified` se fuerza a `true` para que la copia no te bloquee el login con el gate de verificación.
- ❌ **No** se copia `django_migrations`: se conserva el historial de migraciones **local** (el estado aplicado lo decide tu `migrate`, no el de prod).
- ❌ **No** se copian los archivos de `media/` (imágenes de discos, flyers de bazares…): solo filas de tablas. Los `ImageField`/`FileField` apuntan a rutas locales; si los archivos no existen localmente, las imágenes se ven rotas (los originales viven en Railway/R2).

**Paso a paso del comando `copy_prod_to_local`:**

1. Verifica que existan `.env` (prod) y `.env.local` (local); si falta alguno, aborta con instrucciones.
2. Intenta conectar al Postgres local con las `PG_*` de `.env.local`. Si está caído, te dice cómo arrancarlo (`brew services start postgresql@14` en macOS, `systemctl` en Linux).
3. Revisa que el rol local sea `SUPERUSER` (necesario porque el script usa `session_replication_role`); si no lo es, te avisa antes de fallar.
4. Corre `python manage.py migrate` sobre la base local para que el esquema exista/esté al día — sin esto la copia falla en una base recién creada (no hay tablas que truncar ni dónde insertar). Sáltalo con `--skip-migrate` si tu esquema ya es correcto.
5. Pide confirmación (salvo `--yes`), porque el siguiente paso **vacía** tu base local.
6. Ejecuta `scripts/copy_prod_to_local.py` (abajo).

**Paso a paso de `scripts/copy_prod_to_local.py`:**

1. Se conecta a prod (`.env`) y a local (`.env.local`) con `psycopg2`, ambas en `autocommit` (prod solo para leer).
2. En local ejecuta `SET session_replication_role = replica`: desactiva temporalmente la validación de llaves foráneas para poder insertar en cualquier orden — esto requiere `SUPERUSER`. Si la copia fallara por FKs, la causa sería un rol local sin privilegios.
3. Lista las tablas de local y hace `TRUNCATE ... CASCADE` de todas menos `django_migrations` (el `CASCADE` arrastra dependencias).
4. Para cada tabla de prod (orden alfabético): lee sus columnas, hace `SELECT` de todas las filas, corta a las columnas que local comparte e inserta con `executemany` (los valores tipo JSON/array se serializan con el adaptador `Json` de psycopg2).
5. Reajusta las **secuencias** (`setval` al `MAX(id)` de cada tabla que tenga `id`), para que los próximos inserts no colisionen con los IDs recién copiados.
6. Regresa la conexión a `session_replication_role = origin` y te imprime el conteo de filas por tabla.

**Casos de uso clásicos:**

- **Base recién creada** (acabas de instalar Postgres): corre los pasos 1–3 y luego `python manage.py copy_prod_to_local` — el comando hace `migrate` y copia en un solo paso.
- **Refrescar datos viejos** (reproduciste un bug de prod en local): vuelve a correr el mismo comando; trunca y copia de nuevo sin tocar tu esquema.
- **Solo quieres el esquema, sin data:** `python manage.py migrate` (sin el copy).
- **Los datos de prod son sensibles:** la copia vive solo en tu máquina (`.env.local` está en `.gitignore`); prod siempre queda intacta.

Migra las bases de datos 💾

```bash
    python manage.py makemigrations
    python manage.py migrate
```

Corre el proyecto 🚀

```bash
    python manage.py runserver 8008
```

## Crear super usuario

Si quieres acceder al panel de adminostrador en desarrollo.

```bash
  python manage.py createsuperuser
```

### Asignar rol de admin (desarrollo)

En el shell de Django:

```bash
python manage.py shell -c "
from apiApp.models import User
u = User.objects.get(username='tu_usuario')
u.role = 'ADMIN'
u.save()
print(f'{u.username} ahora es ADMIN')
"
```

### Asignar rol de admin (producción en Railway)

```bash
railway run python manage.py shell -c "
from apiApp.models import User
u = User.objects.get(email='tu@email.com')
u.role = 'ADMIN'
u.save()
print(f'{u.username} ahora es ADMIN')
"
```

Alternativamente, puedes acceder a `/admin/` en Railway (crea un superuser primero si no tienes uno con `railway run python manage.py createsuperuser`), buscar el usuario, y cambiar el campo **Role** directamente desde el panel de Django admin.

## Verificación de email ✉️ (2FA por email)

Al registrarse, el backend envía un correo con un enlace de verificación firmado (expira a las 24 h). Si `REQUIRE_EMAIL_VERIFICATION=true`, el usuario **no puede hacer login** hasta confirmar su correo.

### Endpoints

| Método | URL | Descripción |
|--------|-----|-------------|
| `POST` | `/api/auth/register/` | Registro; envía email de bienvenida + enlace de verificación. Respuesta incluye `email_verified`. |
| `POST` | `/api/auth/login/` | Login; si no está verificado y `REQUIRE_EMAIL_VERIFICATION=true` → `403` con código `email_not_verified`. |
| `POST` | `/api/auth/verify-email/` | Body: `{ "uid": "...", "token": "..." }` (los que llegan por query string del enlace). Idempotente. |
| `POST` | `/api/auth/verify-email/resend/` | Reenvía el enlace (rate-limited a **5/hora** por scope `email_verify`; respuesta genérica para no filtrar si el email existe). |

### Flujo

1. `register` → crea usuario con `email_verified=False`, envía welcome + verify.
2. El enlace apunta a `{FRONTEND_URL}/verificar-correo?uid=...&token=...`.
3. El frontend llama a `POST /api/auth/verify-email/` con uid/token.
4. El token se valida con `default_token_generator`; tokens forjados o expirados → `400` con `{ token: ["Invalid or expired verification link"] }`.
5. `login` verifica `email_verified` cuando el flag está activo.

### Gate de compra (cart / checkout / órdenes)

Cuando `REQUIRE_EMAIL_VERIFICATION=true`, el backend también bloquea (además del login) los endpoints de carrito, checkout y órdenes para usuarios autenticados pero sin verificar, devolviendo `403` con `code: "email_not_verified"`:

- `GET /carts/`, `GET /carts/<cart_code>/`, `GET /cart-items/`
- `POST /cart/add/`, `PUT /cart/update/`, `DELETE /cart/remove/`, `DELETE /cart/remove-all/`, `DELETE /cart/delete/`
- `POST /create-checkout-session/`, `POST /checkout/complete/`
- `GET /orders/`

Esto cierra la posibilidad de saltarse el bloqueo de la UI llamando la API directamente. El helper `_require_email_verified(request)` en `apiApp/views.py` aplica el guard.

En desarrollo local, `.env.local` tiene `REQUIRE_EMAIL_VERIFICATION=true` activo, así que el flujo completo (registro → verificar → comprar) se puede probar de punta a punta.

### Archivos clave

- `apiApp/models.py` → campo `User.email_verified`.
- `apiApp/views.py` → `verify_email`, `resend_verification_email`, `_build_verification_link`.
- `apiApp/serilizers.py` → `VerifyEmailSerializer`, `email_verified` en `UserSerializer`.
- `apiApp/emails.py` → `send_verification_email` + `apiApp/templates/emails/verify_email.html`.
- `bemoctezuna_recordsAPI/settings.py` → flag `REQUIRE_EMAIL_VERIFICATION`, throttle `email_verify`.

### Migraciones

- `0036_user_email_verified.py` → agrega `email_verified` a `User`.
- `0037_mark_existing_users_email_verified.py` → data migration que marca a los usuarios existentes como verificados (para que el flag no los bloquee al activarse).

### Tests

```bash
python3 -m pytest apiApp/tests/ -q
```

Cobertura de la verificación en `apiApp/tests/test_emails.py` (token forjado, uid inválido, idempotencia, reenvío, throttle 429 y login bloqueado/permitido), del gate de compra en `apiApp/tests/test_verification_gate.py` (carrito, checkout y órdenes bloqueados cuando el usuario no está verificado), de envíos/admin en `test_shipping.py`, búsqueda en `test_search.py`, slugs en `test_slug_generation.py` y bazares en `test_bazares.py`. Suite completa: **167 passed** (+2 flakes conocidos de throttle por aislamiento de caché).

## Bazares 🎪 (recoger en bazar)

La tienda participa en bazares/tianguis de discos. El modelo `Bazar`
(`apiApp/models.py`) guarda nombre, fecha, horario, dirección, link de Google
Maps e imagen del flyer (se guarda en `media/bazares/`). El slug se autogenera
como el resto de los modelos slugeados, pero es **no único** a propósito: los
eventos recurrentes repiten nombre.

### Endpoints

| Método | URL | Auth | Descripción |
|--------|-----|------|-------------|
| `GET` | `/bazares/` | Público | Próximos eventos (`date >= hoy`), ordenados por fecha ascendente. |
| `GET` | `/bazares/all/` | Admin | Lista completa (incluye pasados). |
| `POST` | `/bazares/create/` | Admin | Crear (multipart/form-data, imagen opcional). |
| `PATCH` | `/bazares/<id>/update/` | Admin | Actualizar campos (permite corregir fechas pasadas). |
| `DELETE` | `/bazares/<id>/delete/` | Admin | Eliminar bazar. |

### Checkout con recoger en bazar

- Al pagar, si `shipped_to='bazar'` el backend exige un `bazar_id` válido y no
  pasado; si falta o es inválido responde `missing_bazar`, `invalid_bazar` o
  `bazar_in_past`.
- No se cobra envío ni se genera etiqueta para esta modalidad.
- El bazar elegido viaja como **metadata** de la sesión de Stripe y al
  confirmarse el pago la orden queda ligada (`Order.pickup_bazar`). Si el
  bazar se elimina después, la orden conserva su historial (`SET_NULL`).
- Las órdenes exponen `pickup_bazar` anidado y los correos de pedido incluyen
  el bloque "Recoger en bazar".

### Migraciones

- `0044_bazar.py` → modelo `Bazar`.
- `0045_order_pickup_bazar.py` → `Order.pickup_bazar` (FK nullable).

## Panel de admin / Inventario 🛠️

El frontend incluye un panel de administración (`/admin`) con tres pestañas:

- **Agregar disco**: crear/editar registros con vista previa en vivo, búsqueda por Discogs, cálculo de precio de venta y ganancia.
- **Discos**: lista paginada con búsqueda, modal de venta (descuenta stock + registra `final_sale_price`), botón Editar.
- **Usuarios**: listar, cambiar rol (ADMIN/CUSTOMER), eliminar.

### Endpoints admin (requieren `role=ADMIN`)

| Método | URL | Descripción |
|--------|-----|-------------|
| `GET` | `/auth/users/` | Lista de todos los usuarios. |
| `PATCH` | `/auth/users/<id>/` | Actualizar rol/estado de un usuario. |
| `DELETE` | `/auth/users/<id>/delete/` | Eliminar usuario. |
| `PATCH` | `/records/<id>/update/` | Actualizar stock, precio, descuento, etc. |
| `POST` | `/artists/create/` | Crear artista (retorna existente si el nombre coincide). |
| `POST` | `/generes/create/` | Crear género (retorna existente si el nombre coincide). |

### Sistema de precios con descuento

- `Record.price` = precio de lista (nunca cambia).
- `Record.discount_porcentage` = porcentaje de descuento.
- `Record.sell_price` = auto-calculado en `Record.save()` con `Decimal` precision: `price × (1 − discount%/100)`.
- El frontend calcula precios con `getEffectivePrice(record)` en `album.ts` — resuelve desde `price` + `discount_porcentage` directamente para no depender de un `sell_price` potencialmente stale.

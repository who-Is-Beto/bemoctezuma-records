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

   ```bash
   brew services start postgresql@14
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

4. Copia los datos de producción a tu base local (lectura **solo lectura** desde Railway, nunca escribe en prod). Guarda el script como `scripts/copy_prod_to_local.py` o úsalo desde `apiApp`:

   ```bash
   python scripts/copy_prod_to_local.py
   ```

   El script conecta con las credenciales de `.env` (prod) y restaura en las de `.env.local` (local), truncando antes las tablas locales. Requiere que el rol local tenga `SUPERUSER` (o que copies por orden de dependencias de FKs).

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
python -m pytest apiApp/tests/ -q
```

Incluyen cobertura de la verificación en `apiApp/tests/test_emails.py` (token forjado, uid inválido, idempotencia, reenvío, throttle 429 y login bloqueado/permitido) y del gate de compra en `apiApp/tests/test_verification_gate.py` (carrito, checkout y órdenes bloqueados cuando el usuario no está verificado).

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

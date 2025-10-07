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

"""Crea el archivo .env con una SECRET_KEY nueva y aleatoria.

Uso:  python crear_env.py
No sobrescribe un .env existente. El .env nunca se sube a GitHub (.gitignore).
"""
import secrets
import string
from pathlib import Path

ARCHIVO = Path(__file__).resolve().parent / '.env'

if ARCHIVO.exists():
    raise SystemExit(f'Ya existe {ARCHIVO.name}; no se modificó. Bórralo a mano si quieres generar otro.')

caracteres = string.ascii_letters + string.digits + '!@#%^&*(-_=+)'
clave = ''.join(secrets.choice(caracteres) for _ in range(64))

ARCHIVO.write_text(
    '# Configuración local. NO subir a GitHub.\n'
    f'SECRET_KEY={clave}\n'
    '# True solo en desarrollo; en el computador del local debe ser False.\n'
    'DEBUG=True\n'
    'ALLOWED_HOSTS=127.0.0.1,localhost\n'
    'SESION_MINUTOS=15\n',
    encoding='utf-8',
)
print(f'Listo: se creó {ARCHIVO.name} con una SECRET_KEY nueva de 64 caracteres.')

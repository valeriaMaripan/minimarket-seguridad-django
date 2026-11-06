"""Fase 5: bloqueo del inicio de sesión tras intentos fallidos.

Reglas (configurables en settings):
- BLOQUEO_INTENTOS_USUARIO fallos seguidos con un mismo usuario lo bloquean.
- BLOQUEO_INTENTOS_IP fallos desde un mismo equipo lo bloquean, aunque cambie
  de usuario (evita probar una contraseña común contra muchas cuentas).
- El bloqueo dura BLOQUEO_MINUTOS desde el último fallo, o hasta que un
  administrador o el dueño lo desbloquee. Un inicio exitoso reinicia la cuenta.
- Mientras está bloqueado, la contraseña ni siquiera se revisa: un atacante
  no puede seguir probando aunque acierte.
- El mensaje es el mismo exista o no el usuario, para no revelar qué cuentas hay.
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from .models import IntentoAcceso, RegistroAuditoria

MENSAJE_BLOQUEO = ('Demasiados intentos fallidos. Por seguridad, el acceso quedó bloqueado '
                   'por {minutos} minutos. Si necesitas entrar antes, pide a un administrador '
                   'que desbloquee la cuenta.')


def normalizar(usuario):
    return (usuario or '').strip().lower()[:150]


def ip_cliente(request):
    # Se usa la IP de la conexión. No se confía en X-Forwarded-For, que el
    # cliente puede inventar para esquivar el bloqueo por equipo.
    return request.META.get('REMOTE_ADDR') if request is not None else None


def _inicio_ventana():
    return timezone.now() - timedelta(minutes=settings.BLOQUEO_MINUTOS)


def _fallos_usuario(usuario):
    """Fallos del usuario en la ventana, posteriores a su último éxito o desbloqueo."""
    desde = _inicio_ventana()
    reinicio = (IntentoAcceso.objects
                .filter(usuario=usuario, resultado__in=['EXITOSO', 'DESBLOQUEO'])
                .aggregate(f=Max('fecha'))['f'])
    if reinicio and reinicio > desde:
        desde = reinicio
    return IntentoAcceso.objects.filter(usuario=usuario, resultado='FALLIDO', fecha__gt=desde).count()


def _fallos_ip(ip):
    if not ip:
        return 0
    desde = _inicio_ventana()
    reinicio = (IntentoAcceso.objects
                .filter(ip=ip, resultado='DESBLOQUEO')
                .aggregate(f=Max('fecha'))['f'])
    if reinicio and reinicio > desde:
        desde = reinicio
    return IntentoAcceso.objects.filter(ip=ip, resultado='FALLIDO', fecha__gt=desde).count()


def esta_bloqueado(usuario, ip):
    return (_fallos_usuario(normalizar(usuario)) >= settings.BLOQUEO_INTENTOS_USUARIO
            or _fallos_ip(ip) >= settings.BLOQUEO_INTENTOS_IP)


def mensaje_bloqueo():
    return MENSAJE_BLOQUEO.format(minutos=settings.BLOQUEO_MINUTOS)


def registrar_intento(usuario, ip, exitoso):
    """Guarda el intento y, si con este fallo se alcanza el límite, lo deja en la auditoría."""
    usuario = normalizar(usuario)
    IntentoAcceso.objects.create(usuario=usuario, ip=ip,
                                 resultado='EXITOSO' if exitoso else 'FALLIDO')
    if exitoso:
        return
    if _fallos_usuario(usuario) == settings.BLOQUEO_INTENTOS_USUARIO:
        RegistroAuditoria.objects.create(
            realizado_por='sistema', accion='CUENTA_BLOQUEADA',
            valor_anterior=f'{settings.BLOQUEO_INTENTOS_USUARIO} intentos fallidos',
            valor_nuevo=f'usuario "{usuario}" bloqueado, IP {ip or "desconocida"}')
    if ip and _fallos_ip(ip) == settings.BLOQUEO_INTENTOS_IP:
        RegistroAuditoria.objects.create(
            realizado_por='sistema', accion='EQUIPO_BLOQUEADO',
            valor_anterior=f'{settings.BLOQUEO_INTENTOS_IP} intentos fallidos',
            valor_nuevo=f'IP {ip} bloqueada')


def bloqueos_activos():
    """Usuarios y equipos bloqueados en este momento, para la página de seguridad."""
    recientes = (IntentoAcceso.objects.filter(resultado='FALLIDO', fecha__gt=_inicio_ventana())
                 .values_list('usuario', 'ip').distinct())
    usuarios = sorted({u for u, _ in recientes
                       if _fallos_usuario(u) >= settings.BLOQUEO_INTENTOS_USUARIO})
    ips = sorted({ip for _, ip in recientes
                  if ip and _fallos_ip(ip) >= settings.BLOQUEO_INTENTOS_IP})
    return usuarios, ips


def desbloquear(usuario=None, ip=None, perfil=None, cuenta=''):
    """Desbloqueo manual de un usuario o de un equipo, auditado."""
    IntentoAcceso.objects.create(usuario=normalizar(usuario) if usuario else '',
                                 ip=ip or None, resultado='DESBLOQUEO')
    RegistroAuditoria.objects.create(
        usuario=perfil, realizado_por=cuenta,
        accion='CUENTA_DESBLOQUEADA' if usuario else 'EQUIPO_DESBLOQUEADO',
        valor_nuevo=f'usuario "{normalizar(usuario)}"' if usuario else f'IP {ip}')

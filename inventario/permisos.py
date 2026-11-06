"""Fase 5: matriz de permisos por rol.

Es la ÚNICA fuente de verdad: las vistas, el menú y la página de seguridad
leen de aquí, así que la documentación y el comportamiento no se pueden
desalinear. Cada acción tiene los roles que la pueden ejecutar.

Principios aplicados:
- Privilegio mínimo: cada rol tiene solo lo que necesita para su trabajo.
- Separación de funciones: el Dueño supervisa (ve costos, auditoría y
  accesos), pero no vende ni mueve stock; quien controla no opera.
"""
from django.core.exceptions import PermissionDenied

CAJERO, ADMIN, DUENO = 'CAJERO', 'ADMIN', 'DUENO'
ROLES_ORDEN = [(CAJERO, 'Cajero'), (ADMIN, 'Administrador'), (DUENO, 'Dueño')]

# acción: (descripción para la matriz, roles permitidos)
PERMISOS = {
    'ver_inventario':       ('Ver inventario, historial y productos', {CAJERO, ADMIN, DUENO}),
    'registrar_pedido':     ('Registrar pedidos de venta', {CAJERO, ADMIN}),
    'registrar_salida':     ('Registrar salidas de un lote', {CAJERO, ADMIN}),
    'registrar_ingreso':    ('Registrar ingresos y mermas', {ADMIN}),
    'ingresar_lote':        ('Ingresar lotes de mercadería', {ADMIN}),
    'gestionar_productos':  ('Crear y editar productos y precios', {ADMIN}),
    'ver_costos':           ('Ver costos de compra y márgenes', {ADMIN, DUENO}),
    'ver_auditoria':        ('Ver el registro de auditoría', {ADMIN, DUENO}),
    'ver_seguridad':        ('Ver accesos y desbloquear cuentas', {ADMIN, DUENO}),
}


def tiene_permiso(perfil, accion):
    if perfil is None:
        return False
    return perfil.rol in PERMISOS[accion][1]


def exigir(perfil, accion):
    """Lanza 403 si el perfil no tiene el permiso. Se usa en el servidor, siempre."""
    if not tiene_permiso(perfil, accion):
        raise PermissionDenied('Tu rol no tiene acceso a esta sección.')


def matriz():
    """Filas para mostrar la matriz: descripción y un sí/no por rol."""
    return [
        {'accion': accion, 'descripcion': desc,
         'roles': [rol in permitidos for rol, _ in ROLES_ORDEN]}
        for accion, (desc, permitidos) in PERMISOS.items()
    ]


def contexto(request):
    """Expone `puede.<accion>` a las plantillas para armar el menú según el rol."""
    perfil = getattr(getattr(request, 'user', None), 'perfil', None) \
        if getattr(request, 'user', None) and request.user.is_authenticated else None
    return {'puede': {accion: tiene_permiso(perfil, accion) for accion in PERMISOS}}

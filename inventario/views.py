from functools import wraps

from django.conf import settings

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from django.contrib.auth.views import PasswordChangeView
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from . import bloqueo
from .forms import LoteForm, MovimientoForm, PedidoItemFormSet, ProductoForm
from .models import IntentoAcceso, Lote, Movimiento, Producto, RegistroAuditoria
from .permisos import ROLES_ORDEN, exigir, matriz, tiene_permiso
from .templatetags.formato import pesos
from .servicios import (StockInsuficiente, guardar_producto, margen, productos_con_stock,
                        registrar_pedido)


def _perfil(request):
    """Perfil de inventario (rol) de la cuenta que inició sesión."""
    perfil = getattr(request.user, 'perfil', None)
    if perfil is None:
        # Cuenta sin perfil de inventario: no tiene rol asignado, no pasa.
        raise PermissionDenied('Tu cuenta no tiene un perfil de inventario asignado.')
    return perfil


def permiso_requerido(accion):
    """Solo deja pasar a quien inició sesión y tiene el permiso en la matriz (permisos.py).

    La comprobación se hace en el servidor: aunque alguien escriba la dirección
    a mano o envíe el formulario directamente, recibe 403 y no se ejecuta nada.
    """
    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            exigir(_perfil(request), accion)
            return vista(request, *args, **kwargs)
        return login_required(envoltura)
    return decorador


@permiso_requerido('ver_inventario')
def lotes(request):
    """Inventario por lotes, del que vence primero al que vence después."""
    lista = list(Lote.objects.select_related('producto').order_by('fecha_vencimiento'))
    resumen = {'VIGENTE': 0, 'POR_VENCER': 0, 'VENCIDO': 0}
    for lote in lista:
        resumen[lote.estado] += 1
    return render(request, 'inventario/lotes.html', {'lotes': lista, 'resumen': resumen})


@permiso_requerido('registrar_salida')
def movimiento_nuevo(request):
    perfil = _perfil(request)
    form = MovimientoForm(request.POST or None, usuario=perfil)
    if request.method == 'POST' and form.is_valid():
        try:
            form.save()
        except ValidationError as error:
            # Validación final con el lote bloqueado (ej.: otro usuario vendió antes).
            form.add_error(None, error)
        else:
            messages.success(request, 'Movimiento registrado correctamente.')
            return redirect('lotes')
    return render(request, 'inventario/movimiento_form.html', {'form': form, 'perfil': perfil})


@permiso_requerido('ver_inventario')
def movimientos(request):
    """Historial de movimientos (solo lectura)."""
    lista = Movimiento.objects.select_related('lote__producto', 'usuario').order_by('-fecha')[:100]
    return render(request, 'inventario/movimientos.html', {'movimientos': lista})


@permiso_requerido('registrar_pedido')
def pedido_nuevo(request):
    """Pedido de venta con varios productos. Si falta stock, se rechaza completo."""
    perfil = _perfil(request)
    formset = PedidoItemFormSet(request.POST or None)
    error_pedido = None
    if request.method == 'POST' and formset.is_valid():
        items = [
            (f.cleaned_data['producto'], f.cleaned_data['cantidad'])
            for f in formset.forms
            if f.cleaned_data.get('producto') and f.cleaned_data.get('cantidad')
        ]
        if not items:
            error_pedido = 'El pedido debe tener al menos un producto.'
        else:
            try:
                pedido = registrar_pedido(perfil, items)
            except ValueError as error:
                error_pedido = f'Pedido rechazado, no se descontó nada. {error}'
            except StockInsuficiente as error:
                # La transacción ya deshizo todo. Se deja constancia del intento.
                for producto, solicitado, disponible in error.faltantes:
                    RegistroAuditoria.objects.create(
                        usuario=perfil, realizado_por=request.user.get_username(),
                        producto=producto, accion='PEDIDO_RECHAZADO_SIN_STOCK',
                        valor_anterior=f'disponible: {disponible}',
                        valor_nuevo=f'solicitado: {solicitado}',
                    )
                error_pedido = f'Pedido rechazado, no se descontó nada. {error.mensaje()}'
            else:
                messages.success(request, f'Pedido #{pedido.pk} registrado correctamente.')
                return redirect('movimientos')
    return render(request, 'inventario/pedido_form.html', {'formset': formset, 'perfil': perfil,
                                                           'error_pedido': error_pedido})


@permiso_requerido('ingresar_lote')
def lote_nuevo(request):
    """Ingreso de mercadería (lote nuevo). Solo administradores."""
    perfil = _perfil(request)
    form = LoteForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        lote = form.save()
        RegistroAuditoria.objects.create(
            usuario=perfil, realizado_por=request.user.get_username(),
            producto=lote.producto, accion='INGRESO_LOTE',
            valor_nuevo=(f'lote {lote.codigo_lote}, {lote.cantidad} unidades, '
                         f'costo {lote.costo_unitario:.2f} c/u, vence {lote.fecha_vencimiento}'),
        )
        _avisar_margen(request, lote.producto.precio_actual, lote.costo_unitario)
        messages.success(request, f'Lote {lote.codigo_lote} ingresado correctamente.')
        return redirect('lotes')
    return render(request, 'inventario/lote_form.html', {'form': form, 'perfil': perfil})


@permiso_requerido('ver_auditoria')
def auditoria(request):
    """Registro de auditoría (solo lectura). Solo administradores."""
    lista = RegistroAuditoria.objects.select_related('producto', 'usuario').order_by('-fecha', '-pk')[:100]
    return render(request, 'inventario/auditoria.html', {'registros': lista})


# ---------------------------------------------------------------------------
# Fase 4: productos y costos
# ---------------------------------------------------------------------------

def _avisar_margen(request, precio, costo):
    """Advierte (sin bloquear) si el precio de venta no cubre el costo de compra."""
    if costo is not None and precio <= costo:
        messages.warning(request, f'Atención: el precio de venta ({pesos(precio)}) no supera el costo de '
                                  f'compra ({pesos(costo)}). Este producto se vende sin ganancia.')


@permiso_requerido('ver_inventario')
def productos(request):
    """Lista de productos. Los costos y márgenes solo los ve quien tiene 'ver_costos'."""
    perfil = _perfil(request)
    ve_costos = tiene_permiso(perfil, 'ver_costos')
    lista = list(productos_con_stock())
    for p in lista:
        if ve_costos:
            p.margen, p.margen_pct = margen(p.precio_actual, p.ultimo_costo)
        else:
            # El costo es información sensible: no se envía al navegador del cajero.
            p.ultimo_costo = p.margen = p.margen_pct = None
    return render(request, 'inventario/productos.html',
                  {'productos': lista, 've_costos': ve_costos,
                   'gestiona': tiene_permiso(perfil, 'gestionar_productos')})


@permiso_requerido('gestionar_productos')
def producto_nuevo(request):
    perfil = _perfil(request)
    form = ProductoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        producto = guardar_producto(form, perfil, request.user.get_username())
        messages.success(request, f'Producto {producto.sku} creado. Ahora puedes ingresar su primer lote.')
        return redirect('productos')
    return render(request, 'inventario/producto_form.html', {'form': form, 'perfil': perfil, 'nuevo': True})


@permiso_requerido('gestionar_productos')
def producto_editar(request, sku):
    perfil = _perfil(request)
    producto = get_object_or_404(Producto, pk=sku)
    form = ProductoForm(request.POST or None, instance=producto)
    if request.method == 'POST' and form.is_valid():
        if not form.has_changed():
            messages.info(request, 'No hubo cambios que guardar.')
            return redirect('productos')
        producto = guardar_producto(form, perfil, request.user.get_username())
        messages.success(request, f'Producto {producto.sku} actualizado.')
        ultimo = producto.lotes.filter(costo_unitario__isnull=False).order_by('-fecha_ingreso', '-pk').first()
        if ultimo:
            _avisar_margen(request, producto.precio_actual, ultimo.costo_unitario)
        return redirect('productos')
    return render(request, 'inventario/producto_form.html',
                  {'form': form, 'perfil': perfil, 'nuevo': False, 'producto': producto})


# ---------------------------------------------------------------------------
# Fase 5: seguridad de accesos
# ---------------------------------------------------------------------------

@permiso_requerido('ver_seguridad')
def seguridad(request):
    """Bloqueos activos, últimos intentos de acceso y matriz de permisos."""
    usuarios, ips = bloqueo.bloqueos_activos()
    return render(request, 'inventario/seguridad.html', {
        'usuarios_bloqueados': usuarios, 'ips_bloqueadas': ips,
        'intentos': IntentoAcceso.objects.all()[:50],
        'matriz': matriz(), 'roles': [nombre for _, nombre in ROLES_ORDEN],
        'limite_usuario': settings.BLOQUEO_INTENTOS_USUARIO,
        'limite_ip': settings.BLOQUEO_INTENTOS_IP, 'minutos': settings.BLOQUEO_MINUTOS,
    })


@require_POST
@permiso_requerido('ver_seguridad')
def desbloquear(request):
    """Desbloqueo manual (solo POST con token CSRF), auditado."""
    perfil = _perfil(request)
    usuario, ip = request.POST.get('usuario', '').strip(), request.POST.get('ip', '').strip()
    activos_usuarios, activos_ips = bloqueo.bloqueos_activos()
    if usuario and bloqueo.normalizar(usuario) in activos_usuarios:
        bloqueo.desbloquear(usuario=usuario, perfil=perfil, cuenta=request.user.get_username())
        messages.success(request, f'Se desbloqueó el usuario {bloqueo.normalizar(usuario)}.')
    elif ip and ip in activos_ips:
        bloqueo.desbloquear(ip=ip, perfil=perfil, cuenta=request.user.get_username())
        messages.success(request, f'Se desbloqueó el equipo {ip}.')
    else:
        messages.info(request, 'No hay un bloqueo activo con esos datos.')
    return redirect('seguridad')


class CambiarContrasena(PasswordChangeView):
    """Cambio de contraseña con la política de Fase 5 (validadores de settings), auditado."""
    template_name = 'registration/cambiar_contrasena.html'
    success_url = reverse_lazy('lotes')

    def form_valid(self, form):
        respuesta = super().form_valid(form)  # mantiene la sesión activa tras el cambio
        RegistroAuditoria.objects.create(
            usuario=getattr(self.request.user, 'perfil', None),
            realizado_por=self.request.user.get_username(), accion='CAMBIO_CONTRASENA',
            valor_nuevo='contraseña actualizada')  # nunca se guarda la contraseña
        messages.success(self.request, 'Tu contraseña se cambió correctamente.')
        return respuesta

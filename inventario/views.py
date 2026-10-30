from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from .forms import LoteForm, MovimientoForm, PedidoItemFormSet, ProductoForm
from .models import Lote, Movimiento, Producto, RegistroAuditoria
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


def rol_requerido(*roles):
    """Solo deja pasar a quien haya iniciado sesión y tenga uno de los roles.

    La comprobación se hace en el servidor: aunque un cajero escriba la
    dirección a mano, recibe 403 y no se ejecuta nada.
    """
    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            perfil = _perfil(request)
            if perfil.rol not in roles:
                raise PermissionDenied('Tu rol no tiene acceso a esta sección.')
            return vista(request, *args, **kwargs)
        return login_required(envoltura)
    return decorador


@login_required
def lotes(request):
    """Inventario por lotes, del que vence primero al que vence después."""
    _perfil(request)
    lista = list(Lote.objects.select_related('producto').order_by('fecha_vencimiento'))
    resumen = {'VIGENTE': 0, 'POR_VENCER': 0, 'VENCIDO': 0}
    for lote in lista:
        resumen[lote.estado] += 1
    return render(request, 'inventario/lotes.html', {'lotes': lista, 'resumen': resumen})


@login_required
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


@login_required
def movimientos(request):
    """Historial de movimientos (solo lectura)."""
    _perfil(request)
    lista = Movimiento.objects.select_related('lote__producto', 'usuario').order_by('-fecha')[:100]
    return render(request, 'inventario/movimientos.html', {'movimientos': lista})


@login_required
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


@rol_requerido('ADMIN')
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


@rol_requerido('ADMIN')
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


@login_required
def productos(request):
    """Lista de productos. Los costos y márgenes solo los ve el administrador."""
    perfil = _perfil(request)
    es_admin = perfil.rol == 'ADMIN'
    lista = list(productos_con_stock())
    for p in lista:
        if es_admin:
            p.margen, p.margen_pct = margen(p.precio_actual, p.ultimo_costo)
        else:
            # El costo es información sensible: no se envía al navegador del cajero.
            p.ultimo_costo = p.margen = p.margen_pct = None
    return render(request, 'inventario/productos.html', {'productos': lista, 'es_admin': es_admin})


@rol_requerido('ADMIN')
def producto_nuevo(request):
    perfil = _perfil(request)
    form = ProductoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        producto = guardar_producto(form, perfil, request.user.get_username())
        messages.success(request, f'Producto {producto.sku} creado. Ahora puedes ingresar su primer lote.')
        return redirect('productos')
    return render(request, 'inventario/producto_form.html', {'form': form, 'perfil': perfil, 'nuevo': True})


@rol_requerido('ADMIN')
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

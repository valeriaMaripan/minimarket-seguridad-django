from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render

from .forms import LoteForm, MovimientoForm, PedidoItemFormSet
from .models import Lote, Movimiento, RegistroAuditoria
from .servicios import StockInsuficiente, registrar_pedido


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
    lista = Lote.objects.select_related('producto').order_by('fecha_vencimiento')
    return render(request, 'inventario/lotes.html', {'lotes': lista})


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
            valor_nuevo=f'lote {lote.codigo_lote}, {lote.cantidad} unidades, vence {lote.fecha_vencimiento}',
        )
        messages.success(request, f'Lote {lote.codigo_lote} ingresado correctamente.')
        return redirect('lotes')
    return render(request, 'inventario/lote_form.html', {'form': form, 'perfil': perfil})


@rol_requerido('ADMIN')
def auditoria(request):
    """Registro de auditoría (solo lectura). Solo administradores."""
    lista = RegistroAuditoria.objects.select_related('producto', 'usuario').order_by('-fecha', '-pk')[:100]
    return render(request, 'inventario/auditoria.html', {'registros': lista})

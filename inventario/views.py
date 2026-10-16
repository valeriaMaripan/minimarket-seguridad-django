from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render

from .forms import MovimientoForm
from .models import Lote, Movimiento


def _perfil(request):
    """Perfil de inventario (rol) de la cuenta que inició sesión."""
    perfil = getattr(request.user, 'perfil', None)
    if perfil is None:
        # Cuenta sin perfil de inventario: no tiene rol asignado, no pasa.
        raise PermissionDenied('Tu cuenta no tiene un perfil de inventario asignado.')
    return perfil


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

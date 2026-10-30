"""Lógica de negocio que involucra varias tablas a la vez."""
from collections import OrderedDict

from django.db import transaction
from django.db.models import DecimalField, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Lote, Movimiento, Pedido, Producto, RegistroAuditoria


class StockInsuficiente(Exception):
    """El pedido no se puede completar. `faltantes` lista qué producto falla."""

    def __init__(self, faltantes):
        self.faltantes = faltantes  # [(producto, solicitado, disponible), ...]
        super().__init__(self.mensaje())

    def mensaje(self):
        return ' '.join(
            f'Stock insuficiente de {p.nombre}: hay {disponible} disponibles '
            f'(sin contar lotes vencidos) y el pedido pide {solicitado}.'
            for p, solicitado, disponible in self.faltantes
        )


def _agrupar(items):
    """Une las líneas repetidas de un mismo producto en una sola cantidad."""
    agrupado = OrderedDict()
    for producto, cantidad in items:
        agrupado[producto.pk] = (producto, agrupado.get(producto.pk, (producto, 0))[1] + cantidad)
    return list(agrupado.values())


def registrar_pedido(usuario, items, observacion=''):
    """Registra un pedido de venta. Es todo o nada.

    items: lista de (producto, cantidad).
    - Se descuenta del lote que vence primero (FEFO) y se pasa al siguiente
      si hace falta. Los lotes vencidos no se venden.
    - Si falta stock de cualquier producto, no se registra nada y se lanza
      StockInsuficiente con el detalle de lo que falta.
    """
    items = _agrupar(items)
    if not items:
        raise ValueError('El pedido no tiene productos.')
    for producto, _ in items:
        # Se revisa en el servidor aunque el formulario ya los oculte.
        if not producto.activo:
            raise ValueError(f'El producto {producto.nombre} está desactivado y no se puede vender.')

    hoy = timezone.localdate()
    with transaction.atomic():
        # Se procesa en orden de SKU para que dos pedidos simultáneos bloqueen
        # los lotes en el mismo orden y no se queden esperándose entre sí.
        items.sort(key=lambda par: par[0].pk)
        plan, faltantes = [], []
        for producto, solicitado in items:
            lotes = list(
                Lote.objects.select_for_update()
                .filter(producto=producto, cantidad__gt=0, fecha_vencimiento__gte=hoy)
                .order_by('fecha_vencimiento', 'pk')
            )
            disponible = sum(lote.cantidad for lote in lotes)
            if solicitado > disponible:
                faltantes.append((producto, solicitado, disponible))
                continue
            restante = solicitado
            for lote in lotes:
                if restante == 0:
                    break
                tomar = min(restante, lote.cantidad)
                plan.append((lote, tomar))
                restante -= tomar

        if faltantes:
            # Al lanzar la excepción dentro de atomic() no queda nada guardado.
            raise StockInsuficiente(faltantes)

        pedido = Pedido.objects.create(usuario=usuario, observacion=observacion)
        for lote, cantidad in plan:
            Movimiento.objects.create(
                lote=lote, usuario=usuario, tipo='SALIDA', cantidad=cantidad,
                pedido=pedido, observacion=f'Pedido #{pedido.pk}',
            )
        return pedido


# ---------------------------------------------------------------------------
# Fase 4: productos y costos
# ---------------------------------------------------------------------------

def productos_con_stock():
    """Productos con su stock vendible, su stock vencido y el costo de su último lote.

    El stock vendible solo cuenta lotes vigentes; el vencido es lo que hay que
    retirar de la góndola. El costo es el del lote ingresado más recientemente.
    """
    hoy = timezone.localdate()
    ultimo_costo = (
        Lote.objects.filter(producto=OuterRef('pk'), costo_unitario__isnull=False)
        .order_by('-fecha_ingreso', '-pk').values('costo_unitario')[:1]
    )
    cero = Value(0, output_field=IntegerField())
    return Producto.objects.annotate(
        stock_vendible=Coalesce(Sum('lotes__cantidad', filter=Q(lotes__fecha_vencimiento__gte=hoy)), cero),
        stock_vencido=Coalesce(Sum('lotes__cantidad', filter=Q(lotes__fecha_vencimiento__lt=hoy)), cero),
        ultimo_costo=Subquery(ultimo_costo, output_field=DecimalField(max_digits=10, decimal_places=2)),
    ).order_by('-activo', 'nombre')


def margen(precio, costo):
    """Margen en pesos y en porcentaje del precio. None si no hay costo."""
    if costo is None or not precio:
        return None, None
    diferencia = precio - costo
    return diferencia, round(diferencia * 100 / precio, 1)


def guardar_producto(form, perfil, cuenta):
    """Guarda un producto creado o editado y audita cada cambio por separado.

    `form` ya fue validado. Se audita la creación, el cambio de precio, el
    cambio de nombre y la activación o desactivación, con el valor anterior y
    el nuevo, en la misma transacción que el cambio.
    """
    creando = form.instance._state.adding
    anterior = None if creando else Producto.objects.get(pk=form.instance.pk)
    with transaction.atomic():
        producto = form.save()
        producto.refresh_from_db()  # valores tal como quedaron guardados (ej.: 1990.00)
        registro = dict(usuario=perfil, realizado_por=cuenta, producto=producto)
        if creando:
            RegistroAuditoria.objects.create(
                accion='PRODUCTO_CREADO',
                valor_nuevo=f'{producto.nombre}, precio {producto.precio_actual}', **registro)
            return producto
        if anterior.precio_actual != producto.precio_actual:
            RegistroAuditoria.objects.create(
                accion='CAMBIO_PRECIO', valor_anterior=str(anterior.precio_actual),
                valor_nuevo=str(producto.precio_actual), **registro)
        if anterior.nombre != producto.nombre:
            RegistroAuditoria.objects.create(
                accion='CAMBIO_NOMBRE', valor_anterior=anterior.nombre,
                valor_nuevo=producto.nombre, **registro)
        if anterior.activo != producto.activo:
            RegistroAuditoria.objects.create(
                accion='PRODUCTO_REACTIVADO' if producto.activo else 'PRODUCTO_DESACTIVADO',
                valor_anterior='activo' if anterior.activo else 'desactivado',
                valor_nuevo='activo' if producto.activo else 'desactivado', **registro)
    return producto

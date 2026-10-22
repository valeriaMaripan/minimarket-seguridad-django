"""Lógica de negocio que involucra varias tablas a la vez."""
from collections import OrderedDict

from django.db import transaction
from django.utils import timezone

from .models import Lote, Movimiento, Pedido


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

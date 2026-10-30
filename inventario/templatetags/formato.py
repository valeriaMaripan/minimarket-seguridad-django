"""Formato de montos en pesos chilenos para las plantillas."""
from decimal import ROUND_HALF_UP, Decimal

from django import template

register = template.Library()


@register.filter
def pesos(valor):
    """1850.00 -> '$1.850'; -100 -> '-$100'. Vacío si no hay valor."""
    if valor in (None, ''):
        return ''
    entero = int(Decimal(valor).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    texto = f'{abs(entero):,}'.replace(',', '.')
    return f'-${texto}' if entero < 0 else f'${texto}'

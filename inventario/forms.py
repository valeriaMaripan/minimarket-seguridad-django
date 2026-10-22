from django import forms
from django.core.exceptions import ValidationError

from .models import Lote, Movimiento, Producto


class MovimientoForm(forms.ModelForm):
    """Formulario de ingresos, salidas y mermas.

    El usuario que registra el movimiento NO se elige en el formulario: lo
    fija el servidor a partir de la sesión, para que nadie pueda registrar
    movimientos a nombre de otra persona.
    """

    class Meta:
        model = Movimiento
        fields = ['lote', 'tipo', 'cantidad', 'observacion']
        labels = {'observacion': 'Observación (opcional)'}
        widgets = {'cantidad': forms.NumberInput(attrs={'min': 1})}

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        self.instance.usuario = usuario
        self.fields['lote'].queryset = Lote.objects.select_related('producto')
        self.fields['lote'].empty_label = 'Elige un lote'
        if usuario.rol == 'CAJERO':
            # Un cajero solo puede registrar ventas (salidas).
            self.fields['tipo'].choices = [('SALIDA', 'Salida / Venta')]

    def clean_tipo(self):
        tipo = self.cleaned_data['tipo']
        # Se vuelve a verificar en el servidor aunque el formulario ya lo limite.
        if self.usuario.rol == 'CAJERO' and tipo != 'SALIDA':
            raise ValidationError('Tu rol solo permite registrar salidas.')
        return tipo


class LoteForm(forms.ModelForm):
    """Ingreso de un lote nuevo (solo administradores).

    Las reglas (por ejemplo, no ingresar un lote ya vencido) viven en el
    modelo, por lo que se aplican igual aquí y en el panel de administración.
    """

    class Meta:
        model = Lote
        fields = ['producto', 'codigo_lote', 'fecha_vencimiento', 'cantidad']
        labels = {'codigo_lote': 'Código de lote', 'fecha_vencimiento': 'Fecha de vencimiento'}
        widgets = {
            'fecha_vencimiento': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'cantidad': forms.NumberInput(attrs={'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].empty_label = 'Elige un producto'


class PedidoItemForm(forms.Form):
    """Una línea del pedido: producto y cantidad. Una línea vacía se ignora."""
    producto = forms.ModelChoiceField(queryset=Producto.objects.order_by('nombre'), required=False,
                                      empty_label='Elige un producto')
    cantidad = forms.IntegerField(min_value=1, required=False,
                                  widget=forms.NumberInput(attrs={'min': 1}))

    def clean(self):
        datos = super().clean()
        producto, cantidad = datos.get('producto'), datos.get('cantidad')
        if bool(producto) != bool(cantidad) and not self.errors:
            raise ValidationError('Completa el producto y la cantidad, o deja la línea vacía.')
        return datos


PedidoItemFormSet = forms.formset_factory(PedidoItemForm, extra=4)

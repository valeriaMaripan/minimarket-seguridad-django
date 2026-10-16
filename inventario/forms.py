from django import forms
from django.core.exceptions import ValidationError

from .models import Lote, Movimiento


class MovimientoForm(forms.ModelForm):
    """Formulario de ingresos, salidas y mermas.

    El usuario que registra el movimiento NO se elige en el formulario: lo
    fija el servidor a partir de la sesión, para que nadie pueda registrar
    movimientos a nombre de otra persona.
    """

    class Meta:
        model = Movimiento
        fields = ['lote', 'tipo', 'cantidad', 'observacion']
        widgets = {'cantidad': forms.NumberInput(attrs={'min': 1})}

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        self.instance.usuario = usuario
        self.fields['lote'].queryset = Lote.objects.select_related('producto')
        if usuario.rol == 'CAJERO':
            # Un cajero solo puede registrar ventas (salidas).
            self.fields['tipo'].choices = [('SALIDA', 'Salida / Venta')]

    def clean_tipo(self):
        tipo = self.cleaned_data['tipo']
        # Se vuelve a verificar en el servidor aunque el formulario ya lo limite.
        if self.usuario.rol == 'CAJERO' and tipo != 'SALIDA':
            raise ValidationError('Tu rol solo permite registrar salidas.')
        return tipo

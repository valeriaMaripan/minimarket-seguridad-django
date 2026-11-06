import re

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from . import bloqueo
from .models import Lote, Movimiento, Producto
from .permisos import tiene_permiso


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
        if not tiene_permiso(usuario, 'registrar_ingreso'):
            # Sin permiso de ingresos y mermas (cajero): solo ventas (salidas).
            self.fields['tipo'].choices = [('SALIDA', 'Salida / Venta')]

    def clean_tipo(self):
        tipo = self.cleaned_data['tipo']
        # Se vuelve a verificar en el servidor aunque el formulario ya lo limite.
        if tipo != 'SALIDA' and not tiene_permiso(self.usuario, 'registrar_ingreso'):
            raise ValidationError('Tu rol solo permite registrar salidas.')
        return tipo


class LoteForm(forms.ModelForm):
    """Ingreso de un lote nuevo (solo administradores).

    Las reglas (por ejemplo, no ingresar un lote ya vencido) viven en el
    modelo, por lo que se aplican igual aquí y en el panel de administración.
    """

    class Meta:
        model = Lote
        fields = ['producto', 'codigo_lote', 'fecha_vencimiento', 'cantidad', 'costo_unitario']
        labels = {'codigo_lote': 'Código de lote', 'fecha_vencimiento': 'Fecha de vencimiento',
                  'costo_unitario': 'Costo de compra por unidad ($)'}
        widgets = {
            'fecha_vencimiento': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'cantidad': forms.NumberInput(attrs={'min': 1}),
            'costo_unitario': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['producto'].queryset = Producto.objects.filter(activo=True).order_by('nombre')
        self.fields['producto'].empty_label = 'Elige un producto'
        # Obligatorio para lotes nuevos: sin costo no se puede calcular el margen.
        self.fields['costo_unitario'].required = True


SKU_VALIDO = re.compile(r'^[A-Z0-9][A-Z0-9-]{2,19}$')


class ProductoForm(forms.ModelForm):
    """Crear o editar un producto (solo administradores).

    El stock no aparece: solo cambia con lotes y movimientos. El SKU solo se
    elige al crear, porque es la llave del producto y lo usa su historial.
    """

    class Meta:
        model = Producto
        fields = ['sku', 'nombre', 'precio_actual', 'activo']
        labels = {'sku': 'SKU (código del producto)', 'nombre': 'Nombre',
                  'precio_actual': 'Precio de venta ($)', 'activo': 'Producto activo (se puede vender)'}
        widgets = {'precio_actual': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance._state.adding:
            # Un producto nuevo siempre parte activo.
            del self.fields['activo']
        else:
            # El SKU no se edita: el campo no existe, así que un valor enviado a mano se ignora.
            del self.fields['sku']

    def clean_sku(self):
        sku = self.cleaned_data['sku'].strip().upper()
        if not SKU_VALIDO.match(sku):
            raise ValidationError('Usa de 3 a 20 caracteres: letras, números y guiones. Ejemplo: ARR-001.')
        return sku

    def clean_nombre(self):
        nombre = ' '.join(self.cleaned_data['nombre'].split())
        if len(nombre) < 3:
            raise ValidationError('El nombre debe tener al menos 3 caracteres.')
        return nombre


class PedidoItemForm(forms.Form):
    """Una línea del pedido: producto y cantidad. Una línea vacía se ignora."""
    producto = forms.ModelChoiceField(queryset=Producto.objects.filter(activo=True).order_by('nombre'),
                                      required=False,
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


# ---------------------------------------------------------------------------
# Fase 5: inicio de sesión con bloqueo por intentos fallidos
# ---------------------------------------------------------------------------

class BloqueoMixin:
    """Revisa el bloqueo ANTES de comprobar la contraseña y registra cada intento."""

    bloqueado = False  # la plantilla lo usa para mostrar el mensaje correcto

    def _bloquear(self):
        self.bloqueado = True
        raise ValidationError(bloqueo.mensaje_bloqueo(), code='bloqueado')

    def clean(self):
        usuario = self.data.get('username', '')
        ip = bloqueo.ip_cliente(self.request)
        if bloqueo.esta_bloqueado(usuario, ip):
            self._bloquear()
        try:
            datos = super().clean()
        except ValidationError:
            bloqueo.registrar_intento(usuario, ip, exitoso=False)
            if bloqueo.esta_bloqueado(usuario, ip):
                self._bloquear()
            raise
        bloqueo.registrar_intento(usuario, ip, exitoso=True)
        return datos


class FormularioAcceso(BloqueoMixin, AuthenticationForm):
    """Inicio de sesión de la aplicación."""


class FormularioAccesoAdmin(BloqueoMixin, AdminAuthenticationForm):
    """Inicio de sesión del panel de Django: mismas reglas, para no dejar una puerta trasera."""

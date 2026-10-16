from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone

# Días antes del vencimiento en que un lote se marca como "por vencer".
DIAS_ALERTA_VENCIMIENTO = 30


class Usuario(models.Model):
    ROLES = (
        ('ADMIN', 'Administrador'),
        ('CAJERO', 'Cajero'),
    )
    nombre = models.CharField(max_length=100)
    rol = models.CharField(max_length=50, choices=ROLES, default='CAJERO')
    # Fase 3: vincula este perfil con una cuenta de acceso real (login).
    # Es opcional para no alterar los registros existentes.
    cuenta = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='perfil',
    )

    def __str__(self):
        return f"{self.nombre} - {self.rol}"

class Producto(models.Model):
    sku = models.CharField(max_length=50, primary_key=True)
    nombre = models.CharField(max_length=200)
    precio_actual = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    # El stock total es la suma de las cantidades de sus lotes; se actualiza
    # solo al ingresar lotes y registrar movimientos.
    stock_total = models.IntegerField(default=0, validators=[MinValueValidator(0)])

    def __str__(self):
        return f"[{self.sku}] {self.nombre}"


# ---------------------------------------------------------------------------
# Fase 2 (continuación): Lotes, Movimientos y Auditoría
# Fase 3: validaciones del servidor y control de vencimiento
# ---------------------------------------------------------------------------

class Lote(models.Model):
    """Cantidad física de un producto con su fecha de vencimiento."""
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name='lotes'
    )
    codigo_lote = models.CharField(max_length=50)
    fecha_vencimiento = models.DateField()
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    fecha_ingreso = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('producto', 'codigo_lote')
        ordering = ['fecha_vencimiento']  # primero en vencer, primero en salir

    def __str__(self):
        return f"{self.producto.sku} - Lote {self.codigo_lote} (vence {self.fecha_vencimiento})"

    # --- Control de vencimiento -------------------------------------------
    @property
    def dias_para_vencer(self):
        return (self.fecha_vencimiento - timezone.localdate()).days

    @property
    def esta_vencido(self):
        return self.dias_para_vencer < 0

    @property
    def estado(self):
        if self.esta_vencido:
            return 'VENCIDO'
        if self.dias_para_vencer <= DIAS_ALERTA_VENCIMIENTO:
            return 'POR_VENCER'
        return 'VIGENTE'

    def get_estado_label(self):
        return {'VENCIDO': 'Vencido', 'POR_VENCER': 'Por vencer', 'VIGENTE': 'Vigente'}[self.estado]

    # --- Validaciones del servidor -----------------------------------------
    def clean(self):
        # Solo al crear: no se puede ingresar un lote que ya está vencido.
        if self.pk is None and self.fecha_vencimiento \
                and self.fecha_vencimiento < timezone.localdate():
            raise ValidationError(
                {'fecha_vencimiento': 'No se puede ingresar un lote que ya está vencido.'}
            )

    def save(self, *args, **kwargs):
        creando = self.pk is None
        with transaction.atomic():
            super().save(*args, **kwargs)
            if creando:
                # El stock del producto crece con la cantidad del lote nuevo.
                Producto.objects.filter(pk=self.producto_id).update(
                    stock_total=F('stock_total') + self.cantidad
                )


class Movimiento(models.Model):
    """Registro de cada ingreso, salida o merma de un lote.

    Es un registro de solo adición: una vez creado no se puede modificar ni
    eliminar, porque cada movimiento ya afectó el stock.
    """
    TIPOS = (
        ('INGRESO', 'Ingreso'),
        ('SALIDA', 'Salida / Venta'),
        ('MERMA', 'Merma'),
    )
    lote = models.ForeignKey(
        Lote, on_delete=models.PROTECT, related_name='movimientos'
    )
    usuario = models.ForeignKey(
        Usuario, on_delete=models.PROTECT, related_name='movimientos'
    )
    tipo = models.CharField(max_length=10, choices=TIPOS)
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    fecha = models.DateTimeField(auto_now_add=True)
    observacion = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.tipo} x{self.cantidad} - {self.lote}"

    def clean(self):
        if self.pk is not None:
            raise ValidationError('Los movimientos no se pueden modificar.')
        if self.lote_id is None or not self.cantidad:
            return  # los errores de campo ya los reportan los validadores

        lote = self.lote
        if self.tipo in ('SALIDA', 'INGRESO') and lote.esta_vencido:
            accion = 'vender' if self.tipo == 'SALIDA' else 'ingresar stock a'
            raise ValidationError(
                f'No se puede {accion} un lote vencido '
                f'(venció el {lote.fecha_vencimiento}). '
                f'Un lote vencido solo admite registrar merma.'
            )
        if self.tipo in ('SALIDA', 'MERMA') and self.cantidad > lote.cantidad:
            raise ValidationError(
                f'Stock insuficiente en el lote {lote.codigo_lote}: '
                f'quedan {lote.cantidad} y se intentan retirar {self.cantidad}.'
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError('Los movimientos no se pueden modificar.')
        with transaction.atomic():
            # Se bloquea el lote para validar y descontar sobre el valor actual.
            lote = Lote.objects.select_for_update().get(pk=self.lote_id)
            self.lote = lote
            self.full_clean()
            delta = self.cantidad if self.tipo == 'INGRESO' else -self.cantidad
            lote.cantidad = F('cantidad') + delta
            lote.save(update_fields=['cantidad'])
            lote.refresh_from_db(fields=['cantidad'])
            Producto.objects.filter(pk=lote.producto_id).update(
                stock_total=F('stock_total') + delta
            )
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError('Los movimientos no se pueden eliminar.')


class RegistroAuditoria(models.Model):
    """Traza inmutable: una vez creado, no se puede modificar ni borrar."""
    usuario = models.ForeignKey(
        Usuario, on_delete=models.PROTECT, null=True, blank=True,
        related_name='auditorias',
    )
    # Cuenta con la que se inició sesión en el panel de Django (quien ejecutó el cambio)
    realizado_por = models.CharField(max_length=150)
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, null=True, blank=True,
        related_name='auditorias',
    )
    accion = models.CharField(max_length=50)  # ej: CAMBIO_PRECIO
    valor_anterior = models.CharField(max_length=255, blank=True)
    valor_nuevo = models.CharField(max_length=255, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("Los registros de auditoría son inmutables.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Los registros de auditoría no se pueden eliminar.")

    def __str__(self):
        return f"{self.fecha:%Y-%m-%d %H:%M} | {self.realizado_por} | {self.accion}"

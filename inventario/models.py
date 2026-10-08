from django.core.validators import MinValueValidator
from django.db import models

class Usuario(models.Model):
    ROLES = (
        ('ADMIN', 'Administrador'),
        ('CAJERO', 'Cajero'),
    )
    nombre = models.CharField(max_length=100)
    rol = models.CharField(max_length=50, choices=ROLES, default='CAJERO')

    def __str__(self):
        return f"{self.nombre} - {self.rol}"

class Producto(models.Model):
    sku = models.CharField(max_length=50, primary_key=True)
    nombre = models.CharField(max_length=200)
    precio_actual = models.DecimalField(max_digits=10, decimal_places=2)
    stock_total = models.IntegerField(default=0)

    def __str__(self):
        return f"[{self.sku}] {self.nombre}"


# ---------------------------------------------------------------------------
# Fase 2 (continuación): Lotes, Movimientos y Auditoría
# ---------------------------------------------------------------------------

class Lote(models.Model):
    """Cantidad física de un producto con su fecha de vencimiento."""
    producto = models.ForeignKey(
        Producto, on_delete=models.PROTECT, related_name='lotes'
    )
    codigo_lote = models.CharField(max_length=50)
    fecha_vencimiento = models.DateField()
    cantidad = models.PositiveIntegerField()
    fecha_ingreso = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('producto', 'codigo_lote')
        ordering = ['fecha_vencimiento']  # primero en vencer, primero en salir

    def __str__(self):
        return f"{self.producto.sku} - Lote {self.codigo_lote} (vence {self.fecha_vencimiento})"


class Movimiento(models.Model):
    """Registro de cada ingreso, salida o merma de un lote."""
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
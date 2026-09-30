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
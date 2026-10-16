from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Lote, Movimiento, Producto, RegistroAuditoria, Usuario


def hoy():
    return timezone.localdate()


class BaseDatos(TestCase):
    def setUp(self):
        self.producto = Producto.objects.create(
            sku='ARR-001', nombre='Arroz 1kg', precio_actual=Decimal('1850.00'), stock_total=0)
        self.lote = Lote.objects.create(
            producto=self.producto, codigo_lote='L1',
            fecha_vencimiento=hoy() + timedelta(days=200), cantidad=50)
        self.cajero = Usuario.objects.create(nombre='Carlos', rol='CAJERO')
        self.admin = Usuario.objects.create(nombre='Ana', rol='ADMIN')

    def stock(self):
        self.producto.refresh_from_db()
        self.lote.refresh_from_db()
        return self.lote.cantidad, self.producto.stock_total


class StockYMovimientos(BaseDatos):
    def test_lote_nuevo_suma_al_stock_del_producto(self):
        self.assertEqual(self.stock(), (50, 50))

    def test_salida_descuenta_lote_y_producto(self):
        Movimiento.objects.create(lote=self.lote, usuario=self.cajero, tipo='SALIDA', cantidad=2)
        self.assertEqual(self.stock(), (48, 48))

    def test_ingreso_suma(self):
        Movimiento.objects.create(lote=self.lote, usuario=self.admin, tipo='INGRESO', cantidad=10)
        self.assertEqual(self.stock(), (60, 60))

    def test_merma_descuenta(self):
        Movimiento.objects.create(lote=self.lote, usuario=self.admin, tipo='MERMA', cantidad=5)
        self.assertEqual(self.stock(), (45, 45))

    def test_no_permite_stock_negativo(self):
        with self.assertRaises(ValidationError):
            Movimiento.objects.create(lote=self.lote, usuario=self.cajero, tipo='SALIDA', cantidad=51)
        self.assertEqual(self.stock(), (50, 50))

    def test_cantidad_cero_rechazada(self):
        with self.assertRaises(ValidationError):
            Movimiento.objects.create(lote=self.lote, usuario=self.cajero, tipo='SALIDA', cantidad=0)

    def test_movimiento_no_se_modifica_ni_borra(self):
        m = Movimiento.objects.create(lote=self.lote, usuario=self.cajero, tipo='SALIDA', cantidad=1)
        m.cantidad = 30
        with self.assertRaises(PermissionError):
            m.save()
        with self.assertRaises(PermissionError):
            m.delete()
        self.assertEqual(self.stock(), (49, 49))


class Vencimiento(BaseDatos):
    def vencido(self):
        # Se crea vigente y luego se adelanta la fecha, como ocurre con el paso del tiempo.
        Lote.objects.filter(pk=self.lote.pk).update(fecha_vencimiento=hoy() - timedelta(days=1))
        self.lote.refresh_from_db()
        return self.lote

    def test_estados(self):
        self.assertEqual(self.lote.estado, 'VIGENTE')
        Lote.objects.filter(pk=self.lote.pk).update(fecha_vencimiento=hoy() + timedelta(days=10))
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.estado, 'POR_VENCER')
        self.assertEqual(self.vencido().estado, 'VENCIDO')

    def test_no_se_vende_lote_vencido(self):
        lote = self.vencido()
        with self.assertRaises(ValidationError):
            Movimiento.objects.create(lote=lote, usuario=self.cajero, tipo='SALIDA', cantidad=1)

    def test_no_se_ingresa_stock_a_lote_vencido(self):
        lote = self.vencido()
        with self.assertRaises(ValidationError):
            Movimiento.objects.create(lote=lote, usuario=self.admin, tipo='INGRESO', cantidad=1)

    def test_lote_vencido_admite_merma(self):
        lote = self.vencido()
        Movimiento.objects.create(lote=lote, usuario=self.admin, tipo='MERMA', cantidad=50)
        self.assertEqual(self.stock(), (0, 0))

    def test_no_se_crea_lote_ya_vencido(self):
        lote = Lote(producto=self.producto, codigo_lote='L2',
                    fecha_vencimiento=hoy() - timedelta(days=1), cantidad=5)
        with self.assertRaises(ValidationError):
            lote.full_clean()


class Validaciones(BaseDatos):
    def test_precio_debe_ser_positivo(self):
        p = Producto(sku='X-1', nombre='Prueba', precio_actual=Decimal('0'), stock_total=0)
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_stock_producto_no_negativo(self):
        p = Producto(sku='X-2', nombre='Prueba', precio_actual=Decimal('10'), stock_total=-1)
        with self.assertRaises(ValidationError):
            p.full_clean()

    def test_auditoria_inmutable(self):
        r = RegistroAuditoria.objects.create(
            realizado_por='valeria', producto=self.producto, accion='CAMBIO_PRECIO',
            valor_anterior='1', valor_nuevo='2')
        r.valor_nuevo = '3'
        with self.assertRaises(PermissionError):
            r.save()
        with self.assertRaises(PermissionError):
            r.delete()


class Vistas(BaseDatos):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.cuenta_cajero = User.objects.create_user('carlos', password='clave-de-prueba-1')
        self.cuenta_admin = User.objects.create_user('ana', password='clave-de-prueba-2')
        self.cuenta_sin_perfil = User.objects.create_user('intruso', password='clave-de-prueba-3')
        self.cajero.cuenta = self.cuenta_cajero
        self.cajero.save()
        self.admin.cuenta = self.cuenta_admin
        self.admin.save()

    def datos(self, tipo='SALIDA', cantidad=2):
        return {'lote': self.lote.pk, 'tipo': tipo, 'cantidad': cantidad, 'observacion': 'prueba'}

    def test_requiere_login(self):
        for nombre in ('lotes', 'movimientos', 'movimiento_nuevo'):
            r = self.client.get(reverse(nombre))
            self.assertEqual(r.status_code, 302, nombre)
            self.assertIn('/cuentas/login/', r['Location'])
        r = self.client.post(reverse('movimiento_nuevo'), self.datos())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Movimiento.objects.count(), 0)

    def test_cuenta_sin_perfil_recibe_403(self):
        self.client.force_login(self.cuenta_sin_perfil)
        self.assertEqual(self.client.get(reverse('lotes')).status_code, 403)
        self.assertEqual(self.client.post(reverse('movimiento_nuevo'), self.datos()).status_code, 403)
        self.assertEqual(Movimiento.objects.count(), 0)

    def test_cajero_registra_salida_a_su_nombre(self):
        self.client.force_login(self.cuenta_cajero)
        r = self.client.post(reverse('movimiento_nuevo'), self.datos())
        self.assertRedirects(r, reverse('lotes'))
        m = Movimiento.objects.get()
        self.assertEqual(m.usuario, self.cajero)
        self.assertEqual(self.stock(), (48, 48))

    def test_no_se_puede_suplantar_a_otro_usuario(self):
        self.client.force_login(self.cuenta_cajero)
        datos = self.datos()
        datos['usuario'] = self.admin.pk  # campo que el formulario ignora
        self.client.post(reverse('movimiento_nuevo'), datos)
        self.assertEqual(Movimiento.objects.get().usuario, self.cajero)

    def test_cajero_no_puede_registrar_ingreso_ni_merma(self):
        self.client.force_login(self.cuenta_cajero)
        for tipo in ('INGRESO', 'MERMA'):
            r = self.client.post(reverse('movimiento_nuevo'), self.datos(tipo=tipo))
            self.assertEqual(r.status_code, 200)  # vuelve al formulario con error
        self.assertEqual(Movimiento.objects.count(), 0)
        self.assertEqual(self.stock(), (50, 50))

    def test_admin_si_puede_registrar_ingreso(self):
        self.client.force_login(self.cuenta_admin)
        r = self.client.post(reverse('movimiento_nuevo'), self.datos(tipo='INGRESO', cantidad=5))
        self.assertRedirects(r, reverse('lotes'))
        self.assertEqual(self.stock(), (55, 55))

    def test_stock_insuficiente_muestra_error_y_no_descuenta(self):
        self.client.force_login(self.cuenta_cajero)
        r = self.client.post(reverse('movimiento_nuevo'), self.datos(cantidad=999))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Stock insuficiente')
        self.assertEqual(self.stock(), (50, 50))

    def test_lotes_muestra_estado(self):
        self.client.force_login(self.cuenta_cajero)
        r = self.client.get(reverse('lotes'))
        self.assertContains(r, 'Vigente')

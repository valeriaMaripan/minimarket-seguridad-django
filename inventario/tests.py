from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Lote, Movimiento, Pedido, Producto, RegistroAuditoria, Usuario
from .servicios import StockInsuficiente, registrar_pedido


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


class Pedidos(BaseDatos):
    """Pedido de varios productos: todo o nada, vendiendo primero el que vence antes."""

    def setUp(self):
        super().setUp()
        self.leche = Producto.objects.create(
            sku='LEC-001', nombre='Leche 1L', precio_actual=Decimal('1200.00'), stock_total=0)
        self.lote_leche = Lote.objects.create(
            producto=self.leche, codigo_lote='LE1',
            fecha_vencimiento=hoy() + timedelta(days=90), cantidad=10)

    def test_pedido_valido_descuenta_todo(self):
        pedido = registrar_pedido(self.cajero, [(self.producto, 5), (self.leche, 3)])
        self.assertEqual(pedido.movimientos.count(), 2)
        self.assertEqual(self.stock(), (45, 45))
        self.leche.refresh_from_db()
        self.assertEqual(self.leche.stock_total, 7)

    def test_sin_stock_rechaza_todo_el_pedido(self):
        # El arroz alcanza, la leche no: no se debe descontar NADA.
        with self.assertRaises(StockInsuficiente):
            registrar_pedido(self.cajero, [(self.producto, 5), (self.leche, 11)])
        self.assertEqual(self.stock(), (50, 50))
        self.leche.refresh_from_db()
        self.assertEqual(self.leche.stock_total, 10)
        self.assertEqual(Pedido.objects.count(), 0)
        self.assertEqual(Movimiento.objects.count(), 0)

    def test_el_error_indica_que_producto_falta(self):
        with self.assertRaises(StockInsuficiente) as ctx:
            registrar_pedido(self.cajero, [(self.leche, 11)])
        self.assertIn('Leche 1L', str(ctx.exception))
        self.assertIn('10', str(ctx.exception))

    def test_vende_primero_el_lote_que_vence_antes(self):
        antes = Lote.objects.create(
            producto=self.producto, codigo_lote='L0',
            fecha_vencimiento=hoy() + timedelta(days=20), cantidad=3)
        registrar_pedido(self.cajero, [(self.producto, 5)])
        antes.refresh_from_db()
        self.lote.refresh_from_db()
        self.assertEqual(antes.cantidad, 0)   # se agotó el que vence primero
        self.assertEqual(self.lote.cantidad, 48)  # y el resto salió del siguiente

    def test_lote_vencido_no_cuenta_como_stock(self):
        Lote.objects.filter(pk=self.lote.pk).update(fecha_vencimiento=hoy() - timedelta(days=1))
        with self.assertRaises(StockInsuficiente):
            registrar_pedido(self.cajero, [(self.producto, 1)])

    def test_lineas_repetidas_se_suman(self):
        with self.assertRaises(StockInsuficiente):
            registrar_pedido(self.cajero, [(self.producto, 30), (self.producto, 30)])
        self.assertEqual(self.stock(), (50, 50))

    def test_pedido_vacio_no_es_valido(self):
        with self.assertRaises(ValueError):
            registrar_pedido(self.cajero, [])

    def test_pedido_no_se_modifica_ni_borra(self):
        pedido = registrar_pedido(self.cajero, [(self.producto, 1)])
        pedido.observacion = 'otro'
        with self.assertRaises(PermissionError):
            pedido.save()
        with self.assertRaises(PermissionError):
            pedido.delete()


class RolesYAcceso(BaseDatos):
    """Pantallas de pedidos, ingreso de lotes y auditoría según el rol."""

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

    def datos_pedido(self, lineas):
        datos = {'form-TOTAL_FORMS': 4, 'form-INITIAL_FORMS': 0,
                 'form-MIN_NUM_FORMS': 0, 'form-MAX_NUM_FORMS': 1000}
        for i in range(4):
            sku, cant = lineas[i] if i < len(lineas) else ('', '')
            datos[f'form-{i}-producto'] = sku
            datos[f'form-{i}-cantidad'] = cant
        return datos

    def datos_lote(self, **cambios):
        datos = {'producto': self.producto.pk, 'codigo_lote': 'L9',
                 'fecha_vencimiento': (hoy() + timedelta(days=100)).isoformat(), 'cantidad': 20,
                 'costo_unitario': '1000'}
        datos.update(cambios)
        return datos

    def test_todas_las_pantallas_nuevas_exigen_login(self):
        for nombre in ('pedido_nuevo', 'lote_nuevo', 'auditoria'):
            r = self.client.get(reverse(nombre))
            self.assertEqual(r.status_code, 302, nombre)
            self.assertIn('/cuentas/login/', r['Location'])

    def test_cajero_no_entra_a_pantallas_de_administrador(self):
        self.client.force_login(self.cuenta_cajero)
        for nombre in ('lote_nuevo', 'auditoria'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, nombre)

    def test_cajero_no_puede_ingresar_lote_enviando_el_formulario_a_mano(self):
        self.client.force_login(self.cuenta_cajero)
        r = self.client.post(reverse('lote_nuevo'), self.datos_lote())
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Lote.objects.count(), 1)
        self.assertEqual(self.stock(), (50, 50))

    def test_cuenta_sin_perfil_no_entra_a_ninguna(self):
        self.client.force_login(self.cuenta_sin_perfil)
        for nombre in ('pedido_nuevo', 'lote_nuevo', 'auditoria'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, nombre)

    def test_admin_ingresa_lote_y_queda_en_auditoria(self):
        self.client.force_login(self.cuenta_admin)
        r = self.client.post(reverse('lote_nuevo'), self.datos_lote())
        self.assertRedirects(r, reverse('lotes'))
        self.assertEqual(self.stock()[1], 70)
        reg = RegistroAuditoria.objects.get(accion='INGRESO_LOTE')
        self.assertEqual(reg.realizado_por, 'ana')
        self.assertEqual(reg.usuario, self.admin)

    def test_admin_no_puede_ingresar_lote_vencido(self):
        self.client.force_login(self.cuenta_admin)
        vencido = (hoy() - timedelta(days=1)).isoformat()
        r = self.client.post(reverse('lote_nuevo'), self.datos_lote(fecha_vencimiento=vencido))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ya está vencido')
        self.assertEqual(Lote.objects.count(), 1)

    def test_admin_ve_la_auditoria(self):
        self.client.force_login(self.cuenta_admin)
        self.assertEqual(self.client.get(reverse('auditoria')).status_code, 200)

    def test_cajero_registra_pedido_a_su_nombre(self):
        self.client.force_login(self.cuenta_cajero)
        r = self.client.post(reverse('pedido_nuevo'),
                             self.datos_pedido([(self.producto.pk, 4)]))
        self.assertRedirects(r, reverse('movimientos'))
        pedido = Pedido.objects.get()
        self.assertEqual(pedido.usuario, self.cajero)
        self.assertEqual(self.stock(), (46, 46))

    def test_pedido_sin_stock_muestra_error_audita_y_no_descuenta(self):
        self.client.force_login(self.cuenta_cajero)
        r = self.client.post(reverse('pedido_nuevo'),
                             self.datos_pedido([(self.producto.pk, 999)]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Pedido rechazado')
        self.assertEqual(self.stock(), (50, 50))
        self.assertEqual(Pedido.objects.count(), 0)
        reg = RegistroAuditoria.objects.get(accion='PEDIDO_RECHAZADO_SIN_STOCK')
        self.assertEqual(reg.usuario, self.cajero)
        self.assertEqual(reg.valor_anterior, 'disponible: 50')
        self.assertEqual(reg.valor_nuevo, 'solicitado: 999')

    def test_pedido_vacio_se_rechaza(self):
        self.client.force_login(self.cuenta_cajero)
        r = self.client.post(reverse('pedido_nuevo'), self.datos_pedido([]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'al menos un producto')
        self.assertEqual(Pedido.objects.count(), 0)

    def test_linea_incompleta_se_rechaza(self):
        self.client.force_login(self.cuenta_cajero)
        r = self.client.post(reverse('pedido_nuevo'),
                             self.datos_pedido([(self.producto.pk, '')]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Pedido.objects.count(), 0)

    def test_cantidad_negativa_o_cero_se_rechaza(self):
        self.client.force_login(self.cuenta_cajero)
        for cantidad in (0, -3):
            r = self.client.post(reverse('pedido_nuevo'),
                                 self.datos_pedido([(self.producto.pk, cantidad)]))
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.stock(), (50, 50))

    def test_menu_del_cajero_no_muestra_opciones_de_administrador(self):
        self.client.force_login(self.cuenta_cajero)
        r = self.client.get(reverse('lotes'))
        self.assertNotContains(r, 'Ingresar lote')
        self.assertNotContains(r, 'Auditoría')
        self.client.force_login(self.cuenta_admin)
        r = self.client.get(reverse('lotes'))
        self.assertContains(r, 'Ingresar lote')


class ProductosYCostos(BaseDatos):
    """Fase 4: gestión de productos, costos de compra y márgenes según el rol."""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.cuenta_cajero = User.objects.create_user('carlos', password='clave-de-prueba-1')
        self.cuenta_admin = User.objects.create_user('ana', password='clave-de-prueba-2')
        self.cajero.cuenta = self.cuenta_cajero
        self.cajero.save()
        self.admin.cuenta = self.cuenta_admin
        self.admin.save()
        # Lote con costo conocido: precio 1850, costo 1234 -> margen 616 (33.3 %).
        Lote.objects.create(producto=self.producto, codigo_lote='L-COSTO',
                            fecha_vencimiento=hoy() + timedelta(days=300), cantidad=10,
                            costo_unitario=Decimal('1234.00'))

    def datos_producto(self, **cambios):
        datos = {'sku': 'gal-002', 'nombre': '  Galletas   de vainilla ', 'precio_actual': '990'}
        datos.update(cambios)
        return datos

    def editar(self, **cambios):
        datos = {'nombre': self.producto.nombre, 'precio_actual': str(self.producto.precio_actual),
                 'activo': 'on'}
        datos.update(cambios)
        return self.client.post(reverse('producto_editar', args=[self.producto.sku]), datos)

    # --- Acceso por rol ------------------------------------------------------
    def test_cajero_ve_productos_pero_no_costos(self):
        self.client.force_login(self.cuenta_cajero)
        r = self.client.get(reverse('productos'))
        self.assertContains(r, 'Arroz 1kg')
        self.assertNotContains(r, '1234')
        self.assertNotContains(r, 'Margen')
        self.assertNotContains(r, 'Crear producto')

    def test_admin_ve_costo_y_margen(self):
        self.client.force_login(self.cuenta_admin)
        r = self.client.get(reverse('productos'))
        self.assertContains(r, '$1.234')
        self.assertContains(r, '33,3%')  # formato chileno: coma decimal

    def test_cajero_no_crea_ni_edita_productos(self):
        self.client.force_login(self.cuenta_cajero)
        self.assertEqual(self.client.get(reverse('producto_nuevo')).status_code, 403)
        r = self.client.post(reverse('producto_nuevo'), self.datos_producto())
        self.assertEqual(r.status_code, 403)
        r = self.editar(precio_actual='1')
        self.assertEqual(r.status_code, 403)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precio_actual, Decimal('1850.00'))
        self.assertFalse(Producto.objects.filter(sku='GAL-002').exists())

    def test_pantallas_de_productos_exigen_login(self):
        for url in (reverse('productos'), reverse('producto_nuevo'),
                    reverse('producto_editar', args=[self.producto.sku])):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn('/cuentas/login/', r['Location'])

    def test_producto_inexistente_da_404(self):
        self.client.force_login(self.cuenta_admin)
        r = self.client.get(reverse('producto_editar', args=['NO-EXISTE']))
        self.assertEqual(r.status_code, 404)

    # --- Crear ---------------------------------------------------------------
    def test_admin_crea_producto_normalizado_y_auditado(self):
        self.client.force_login(self.cuenta_admin)
        r = self.client.post(reverse('producto_nuevo'), self.datos_producto())
        self.assertRedirects(r, reverse('productos'))
        p = Producto.objects.get(sku='GAL-002')
        self.assertEqual(p.nombre, 'Galletas de vainilla')
        self.assertEqual(p.stock_total, 0)
        self.assertTrue(p.activo)
        reg = RegistroAuditoria.objects.get(accion='PRODUCTO_CREADO')
        self.assertEqual((reg.usuario, reg.realizado_por, reg.producto), (self.admin, 'ana', p))

    def test_sku_invalido_o_repetido_se_rechaza(self):
        self.client.force_login(self.cuenta_admin)
        for sku in ('A', 'ARR 001', 'ARR/001', '<script>', 'arr-001'):
            r = self.client.post(reverse('producto_nuevo'), self.datos_producto(sku=sku))
            self.assertEqual(r.status_code, 200, sku)
        self.assertEqual(Producto.objects.count(), 1)

    def test_precio_cero_o_negativo_se_rechaza(self):
        self.client.force_login(self.cuenta_admin)
        for precio in ('0', '-500', 'abc'):
            r = self.client.post(reverse('producto_nuevo'), self.datos_producto(precio_actual=precio))
            self.assertEqual(r.status_code, 200, precio)
        self.assertFalse(Producto.objects.filter(sku='GAL-002').exists())

    def test_no_se_puede_fijar_el_stock_al_crear(self):
        self.client.force_login(self.cuenta_admin)
        self.client.post(reverse('producto_nuevo'), self.datos_producto(stock_total='9999'))
        self.assertEqual(Producto.objects.get(sku='GAL-002').stock_total, 0)

    # --- Editar --------------------------------------------------------------
    def test_cambio_de_precio_queda_auditado(self):
        self.client.force_login(self.cuenta_admin)
        r = self.editar(precio_actual='1990')
        self.assertRedirects(r, reverse('productos'))
        reg = RegistroAuditoria.objects.get(accion='CAMBIO_PRECIO')
        self.assertEqual((reg.valor_anterior, reg.valor_nuevo), ('1850.00', '1990.00'))
        self.assertEqual(reg.realizado_por, 'ana')

    def test_guardar_sin_cambios_no_audita(self):
        self.client.force_login(self.cuenta_admin)
        self.editar()
        self.assertEqual(RegistroAuditoria.objects.count(), 0)

    def test_sku_y_stock_no_se_editan(self):
        self.client.force_login(self.cuenta_admin)
        self.editar(sku='OTRO-1', stock_total='0', nombre='Arroz grado 1')
        self.assertFalse(Producto.objects.filter(sku='OTRO-1').exists())
        self.assertEqual(self.stock()[1], 60)
        self.assertEqual(RegistroAuditoria.objects.get().accion, 'CAMBIO_NOMBRE')

    def test_precio_bajo_el_costo_avisa_sin_bloquear(self):
        self.client.force_login(self.cuenta_admin)
        r = self.editar(precio_actual='1000')
        r = self.client.get(r['Location'])
        self.assertContains(r, 'se vende sin ganancia')
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precio_actual, Decimal('1000.00'))

    # --- Desactivar ----------------------------------------------------------
    def test_desactivar_audita_y_bloquea_venta_y_lotes(self):
        self.client.force_login(self.cuenta_admin)
        self.editar(activo='')  # casilla desmarcada
        self.producto.refresh_from_db()
        self.assertFalse(self.producto.activo)
        self.assertEqual(RegistroAuditoria.objects.get().accion, 'PRODUCTO_DESACTIVADO')
        with self.assertRaises(ValueError):
            registrar_pedido(self.cajero, [(self.producto, 1)])
        lote = Lote(producto=self.producto, codigo_lote='L-NUEVO',
                    fecha_vencimiento=hoy() + timedelta(days=30), cantidad=5)
        with self.assertRaises(ValidationError):
            lote.full_clean()
        self.assertEqual(self.stock()[1], 60)

    def test_producto_desactivado_no_aparece_en_pedidos(self):
        Producto.objects.filter(pk=self.producto.pk).update(activo=False)
        self.client.force_login(self.cuenta_cajero)
        r = self.client.get(reverse('pedido_nuevo'))
        self.assertNotContains(r, 'Arroz 1kg')

    # --- Lotes con costo y stock vendible ------------------------------------
    def test_lote_nuevo_exige_costo_y_lo_audita(self):
        self.client.force_login(self.cuenta_admin)
        datos = {'producto': self.producto.pk, 'codigo_lote': 'L9', 'cantidad': 20,
                 'fecha_vencimiento': (hoy() + timedelta(days=100)).isoformat()}
        r = self.client.post(reverse('lote_nuevo'), datos)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Lote.objects.filter(codigo_lote='L9').exists())
        datos['costo_unitario'] = '1500'
        r = self.client.post(reverse('lote_nuevo'), datos)
        self.assertRedirects(r, reverse('lotes'))
        self.assertEqual(Lote.objects.get(codigo_lote='L9').costo_unitario, Decimal('1500.00'))
        self.assertIn('costo 1500.00', RegistroAuditoria.objects.get(accion='INGRESO_LOTE').valor_nuevo)

    def test_stock_vendible_excluye_lotes_vencidos(self):
        Lote.objects.filter(pk=self.lote.pk).update(fecha_vencimiento=hoy() - timedelta(days=1))
        from .servicios import productos_con_stock
        p = productos_con_stock().get(pk=self.producto.pk)
        self.assertEqual((p.stock_vendible, p.stock_vencido), (10, 50))

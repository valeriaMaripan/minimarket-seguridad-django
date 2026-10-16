from django.contrib import admin
from .models import Usuario, Producto, Lote, Movimiento, RegistroAuditoria


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'rol', 'cuenta')


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('sku', 'nombre', 'precio_actual', 'stock_total')
    # El stock no se edita a mano: lo calculan los lotes y movimientos.
    readonly_fields = ('stock_total',)

    def save_model(self, request, obj, form, change):
        # Cada cambio de precio deja una traza automática e inmutable
        if change and 'precio_actual' in form.changed_data:
            anterior = Producto.objects.get(pk=obj.pk).precio_actual
            RegistroAuditoria.objects.create(
                realizado_por=request.user.get_username(),
                producto=obj,
                accion='CAMBIO_PRECIO',
                valor_anterior=str(anterior),
                valor_nuevo=str(obj.precio_actual),
            )
        super().save_model(request, obj, form, change)


@admin.register(Lote)
class LoteAdmin(admin.ModelAdmin):
    list_display = ('producto', 'codigo_lote', 'fecha_vencimiento', 'cantidad', 'estado_vencimiento')
    list_filter = ('fecha_vencimiento',)

    @admin.display(description='Estado')
    def estado_vencimiento(self, obj):
        return obj.get_estado_label()

    def get_readonly_fields(self, request, obj=None):
        # La cantidad de un lote existente cambia solo mediante movimientos.
        return ('cantidad',) if obj else ()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Movimiento)
class MovimientoAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'tipo', 'lote', 'cantidad', 'usuario')
    list_filter = ('tipo',)

    # Registro de solo adición: cada movimiento ya afectó el stock.
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RegistroAuditoria)
class RegistroAuditoriaAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'realizado_por', 'producto', 'accion', 'valor_anterior', 'valor_nuevo')

    # Solo lectura: nadie puede crear, editar ni borrar desde el panel
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

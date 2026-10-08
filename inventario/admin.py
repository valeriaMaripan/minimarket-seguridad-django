from django.contrib import admin
from .models import Usuario, Producto, Lote, Movimiento, RegistroAuditoria

admin.site.register(Usuario)


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('sku', 'nombre', 'precio_actual', 'stock_total')

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
    list_display = ('producto', 'codigo_lote', 'fecha_vencimiento', 'cantidad')
    list_filter = ('fecha_vencimiento',)


@admin.register(Movimiento)
class MovimientoAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'tipo', 'lote', 'cantidad', 'usuario')
    list_filter = ('tipo',)


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
from django.urls import path

from . import views

urlpatterns = [
    path('', views.lotes, name='lotes'),
    path('movimientos/', views.movimientos, name='movimientos'),
    path('movimientos/nuevo/', views.movimiento_nuevo, name='movimiento_nuevo'),
    path('pedidos/nuevo/', views.pedido_nuevo, name='pedido_nuevo'),
    path('lotes/nuevo/', views.lote_nuevo, name='lote_nuevo'),
    path('auditoria/', views.auditoria, name='auditoria'),
    path('productos/', views.productos, name='productos'),
    path('productos/nuevo/', views.producto_nuevo, name='producto_nuevo'),
    path('productos/<str:sku>/editar/', views.producto_editar, name='producto_editar'),
]

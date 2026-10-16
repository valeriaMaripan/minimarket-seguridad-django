from django.urls import path

from . import views

urlpatterns = [
    path('', views.lotes, name='lotes'),
    path('movimientos/', views.movimientos, name='movimientos'),
    path('movimientos/nuevo/', views.movimiento_nuevo, name='movimiento_nuevo'),
]

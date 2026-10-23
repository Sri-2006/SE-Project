from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'), path('health/', views.health, name='health'),
    path('inventory/', views.inventory, name='inventory'), path('inventory/<int:pk>/', views.inventory_detail, name='inventory_detail'),
    path('forecasting/', views.forecasting, name='forecasting'),
    path('suppliers/', views.suppliers, name='suppliers'), path('suppliers/<int:pk>/', views.supplier_detail, name='supplier_detail'),
    path('recommendations/', views.recommendations, name='recommendations'),
    path('recommendations/run/', views.run_planning, name='run_planning'),
    path('recommendations/<int:pk>/', views.recommendation_detail, name='recommendation_detail'),
    path('orders/', views.orders, name='orders'), path('orders/new/', views.create_order, name='create_order'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('alerts/', views.alerts, name='alerts'), path('alerts/<int:pk>/read/', views.read_alert, name='read_alert'),
    path('reports/', views.reports, name='reports'), path('reports/export/', views.export_report, name='export_report'),
    path('imports/', views.imports, name='imports'), path('api/events/', views.event_api, name='event_api'),
    path('catalog/<str:kind>/new/', views.catalog_form, name='catalog_new'),
    path('catalog/<str:kind>/<int:pk>/edit/', views.catalog_form, name='catalog_edit'),
]


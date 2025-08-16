"""
URL configuration for gateway project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from rest_framework import permissions

# API Documentation
schema_view = get_schema_view(
    openapi.Info(
        title="Client Nest API Gateway",
        default_version='v1',
        description="API Gateway for Client Nest Microservices Architecture",
        terms_of_service="https://www.google.com/policies/terms/",
        contact=openapi.Contact(email="contact@clientnest.xyz"),
        license=openapi.License(name="BSD License"),
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
)

urlpatterns = [
    # Admin interface
    path('admin/', admin.site.urls),
    
    # API Documentation
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('swagger.json', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    
    # Gateway management endpoints
    path('gateway/', include('gateway_core.urls')),
    
    # Authentication service endpoints
    path('auth/', include('auth_service.urls')),
    
    # Health and readiness for the gateway itself
    path('health/', include('routing.urls')),
    path('ready/', include('routing.urls')),
    
    # All other requests go through the routing service
    path('', include('routing.urls')),
]

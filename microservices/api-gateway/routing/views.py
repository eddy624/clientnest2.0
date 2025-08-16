from django.shortcuts import render
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.db import connections
from django.db.utils import OperationalError
import requests
import logging
import re
from datetime import datetime, timedelta

from gateway_core.models import ServiceRegistry, RouteConfiguration, CircuitBreakerState

logger = logging.getLogger(__name__)

@api_view(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])  # Enforce authentication for all routed requests
def route_request(request, path=''):
    """
    Main routing function that forwards requests to appropriate microservices
    This view handles all incoming requests and routes them based on path patterns
    """
    try:
        # Get the full path from the request
        full_path = f"/{path}" if path else request.path
        
        # Find matching route configuration
        route_config = find_matching_route(full_path)
        if not route_config:
            return Response({
                'error': 'No route found for this path',
                'path': full_path
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Check if service is healthy and circuit breaker is closed
        service = route_config.service
        if not is_service_available(service):
            return Response({
                'error': 'Service temporarily unavailable',
                'service': service.name
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
        # Forward the request to the target service
        response_data, response_status = forward_request(
            request, service, full_path, route_config
        )
        
        return Response(response_data, status=response_status)
        
    except Exception as e:
        logger.error(f"Routing error: {str(e)}")
        return Response({
            'error': 'Internal gateway error'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

def find_matching_route(path):
    """
    Find the best matching route configuration for a given path
    """
    # Get all active routes ordered by priority
    routes = RouteConfiguration.objects.filter(
        is_active=True,
        service__is_active=True
    ).select_related('service').order_by('-priority', 'path_pattern')
    
    for route in routes:
        if route.route_type == 'exact':
            if path == route.path_pattern:
                return route
        elif route.route_type == 'prefix':
            if path.startswith(route.path_pattern):
                return route
        elif route.route_type == 'regex':
            try:
                if re.match(route.path_pattern, path):
                    return route
            except re.error:
                logger.warning(f"Invalid regex pattern in route {route.id}: {route.path_pattern}")
                continue
    
    return None

def is_service_available(service):
    """
    Check if a service is available based on health status and circuit breaker
    """
    # Check circuit breaker state
    try:
        circuit_breaker = CircuitBreakerState.objects.get(service=service)
        
        if circuit_breaker.state == 'open':
            # Check if we should try half-open
            if (circuit_breaker.next_attempt_time and 
                timezone.now() >= circuit_breaker.next_attempt_time):
                circuit_breaker.state = 'half_open'
                circuit_breaker.save()
                return True
            return False
        
        return circuit_breaker.state in ['closed', 'half_open']
        
    except CircuitBreakerState.DoesNotExist:
        # No circuit breaker state, check service health
        return service.status == 'healthy'

def forward_request(request, service, path, route_config):
    """
    Forward the request to the target microservice
    """
    try:
        # Build target URL
        # Remove the route prefix from the path if it's a prefix match
        if route_config.route_type == 'prefix':
            target_path = path[len(route_config.path_pattern):] or '/'
            if not target_path.startswith('/'):
                target_path = '/' + target_path
        else:
            target_path = path
        
        target_url = f"{service.base_url.rstrip('/')}{target_path}"
        
        # Prepare headers
        headers = {
            'Content-Type': request.content_type or 'application/json',
            'X-Forwarded-For': get_client_ip(request),
            'X-Forwarded-Proto': request.scheme,
            'X-Forwarded-Host': request.get_host(),
            'X-Gateway-Service': service.name,
        }
        
        # Add authorization header if present
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if auth_header:
            headers['Authorization'] = auth_header
        
        # Add custom headers
        for key, value in request.META.items():
            if key.startswith('HTTP_X_'):
                header_name = key[5:].replace('_', '-').title()
                headers[header_name] = value
        
        # Prepare request data
        data = None
        if request.method in ['POST', 'PUT', 'PATCH']:
            if request.content_type == 'application/json':
                data = request.data
            else:
                data = request.body
        
        # Add query parameters
        params = dict(request.GET.items())
        
        # Make the request
        response = requests.request(
            method=request.method,
            url=target_url,
            headers=headers,
            json=data if request.content_type == 'application/json' else None,
            data=data if request.content_type != 'application/json' else None,
            params=params,
            timeout=service.timeout,
            allow_redirects=False
        )
        
        # Handle circuit breaker based on response
        handle_circuit_breaker_response(service, response.status_code)
        
        # Return response data and status
        try:
            response_data = response.json() if response.content else {}
        except ValueError:
            response_data = {'content': response.text}
        
        return response_data, response.status_code
        
    except requests.Timeout:
        handle_circuit_breaker_failure(service, 'timeout')
        return {'error': 'Service timeout'}, status.HTTP_504_GATEWAY_TIMEOUT
        
    except requests.ConnectionError:
        handle_circuit_breaker_failure(service, 'connection_error')
        return {'error': 'Service unavailable'}, status.HTTP_503_SERVICE_UNAVAILABLE
        
    except Exception as e:
        handle_circuit_breaker_failure(service, str(e))
        logger.error(f"Request forwarding error: {str(e)}")
        return {'error': 'Gateway error'}, status.HTTP_502_BAD_GATEWAY

def handle_circuit_breaker_response(service, status_code):
    """
    Update circuit breaker state based on response status
    """
    try:
        circuit_breaker, created = CircuitBreakerState.objects.get_or_create(
            service=service,
            defaults={'state': 'closed', 'failure_count': 0}
        )
        
        if status_code >= 500:  # Server errors
            circuit_breaker.failure_count += 1
            circuit_breaker.last_failure_time = timezone.now()
            
            if circuit_breaker.failure_count >= 5:  # Threshold
                circuit_breaker.state = 'open'
                circuit_breaker.next_attempt_time = timezone.now() + timedelta(minutes=5)
        else:
            # Success - reset circuit breaker
            if circuit_breaker.state != 'closed':
                circuit_breaker.state = 'closed'
                circuit_breaker.failure_count = 0
                circuit_breaker.last_failure_time = None
                circuit_breaker.next_attempt_time = None
        
        circuit_breaker.save()
        
    except Exception as e:
        logger.error(f"Circuit breaker update error: {str(e)}")

def handle_circuit_breaker_failure(service, error_type):
    """
    Handle circuit breaker on request failure
    """
    try:
        circuit_breaker, created = CircuitBreakerState.objects.get_or_create(
            service=service,
            defaults={'state': 'closed', 'failure_count': 0}
        )
        
        circuit_breaker.failure_count += 1
        circuit_breaker.last_failure_time = timezone.now()
        
        if circuit_breaker.failure_count >= 5:  # Threshold
            circuit_breaker.state = 'open'
            circuit_breaker.next_attempt_time = timezone.now() + timedelta(minutes=5)
        
        circuit_breaker.save()
        
        # Update service status
        service.status = 'unhealthy'
        service.save()
        
    except Exception as e:
        logger.error(f"Circuit breaker failure handling error: {str(e)}")

def get_client_ip(request):
    """
    Get the client IP address from the request
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def service_discovery(request):
    """
    Service discovery endpoint that returns available services and their routes
    """
    services = ServiceRegistry.objects.filter(is_active=True)
    service_data = []
    
    for service in services:
        routes = RouteConfiguration.objects.filter(
            service=service,
            is_active=True
        ).values('path_pattern', 'route_type', 'requires_auth')
        
        service_data.append({
            'name': service.name,
            'base_url': service.base_url,
            'status': service.status,
            'version': service.version,
            'routes': list(routes),
            'last_health_check': service.last_health_check
        })
    
    return Response({
        'services': service_data,
        'total_services': len(service_data)
    })

@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """
    Health check endpoint for the routing service
    """
    return Response({
        'status': 'healthy',
        'service': 'routing-service',
        'timestamp': datetime.now().isoformat()
    })

@api_view(['GET'])
@permission_classes([AllowAny])
def readiness_check(request):
    """Readiness probe that verifies DB connectivity"""
    try:
        db_conn = connections['default']
        db_conn.cursor()
        db_ready = True
    except OperationalError:
        db_ready = False
    status_code = 200 if db_ready else 503
    return Response({'ready': db_ready}, status=status_code)

@api_view(['POST'])
@permission_classes([IsAdminUser])
def reload_routes(request):
    """
    Reload route configurations (clear cache if using caching)
    """
    try:
        # Clear any cached route data
        cache.clear()
        
        # Count active routes
        active_routes = RouteConfiguration.objects.filter(is_active=True).count()
        
        return Response({
            'message': 'Routes reloaded successfully',
            'active_routes': active_routes,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Route reload error: {str(e)}")
        return Response({
            'error': 'Failed to reload routes'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

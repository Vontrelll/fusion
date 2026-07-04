"""
Lightweight IP-based rate limiting using Django's cache framework.
Best-effort protection for abuse-prone endpoints (signup, password reset, export).
"""
from functools import wraps

from django.core.cache import cache
from django.http import HttpResponse


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def rate_limit(key_prefix, limit=10, period=3600):
    """
    Decorator: allow `limit` requests per `period` seconds per IP for `key_prefix`.
    Returns 429 when exceeded.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            ip = _client_ip(request)
            cache_key = f'ratelimit:{key_prefix}:{ip}'
            count = cache.get(cache_key, 0)
            if count >= limit:
                return HttpResponse(
                    'Too many requests. Please wait and try again later.',
                    status=429,
                    content_type='text/plain',
                )
            cache.set(cache_key, count + 1, timeout=period)
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator
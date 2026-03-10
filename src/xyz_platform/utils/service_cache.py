#!/usr/bin/env python3
"""
===============================================================================
Script Name   : cache.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Simple service caching to avoid re-parsing YAML files.
===============================================================================
"""

from typing import Dict, Optional, Type, Any
from xyz_platform.logger import get_logger

logger = get_logger(__name__)

# Global service cache
_service_cache: Dict[str, Any] = {}


def get_cache_key(service_class: Type, file_path: Optional[str], **kwargs) -> str:
    """
    Generate cache key for service instance.

    Args:
        service_class: Service class type
        file_path: Path to service file
        **kwargs: Additional parameters that affect caching

    Returns:
        Cache key string
    """
    # Include class name and file path
    key_parts = [service_class.__name__]

    if file_path:
        # Convert Path to string
        key_parts.append(str(file_path))

    # Include any additional cache-relevant kwargs
    if kwargs:
        # Sort for consistency
        for k, v in sorted(kwargs.items()):
            if isinstance(v, (str, int, bool)):
                key_parts.append(f"{k}={v}")

    return ":".join(key_parts)


def get_cached_service(
    service_class: Type, file_path: Optional[str] = None, **kwargs
) -> Optional[Any]:
    """
    Get cached service instance if it exists.

    Args:
        service_class: Service class type
        file_path: Path to service file
        **kwargs: Additional parameters

    Returns:
        Cached service instance or None
    """
    cache_key = get_cache_key(service_class, file_path, **kwargs)
    service = _service_cache.get(cache_key)

    if service:
        logger.debug(
            f"Cache hit for {service_class.__name__}", extra={"file_path": file_path}
        )

    return service


def cache_service(
    service_class: Type,
    service_instance: Any,
    file_path: Optional[str] = None,
    **kwargs,
) -> None:
    """
    Cache a service instance.

    Args:
        service_class: Service class type
        service_instance: Service instance to cache
        file_path: Path to service file
        **kwargs: Additional parameters
    """
    cache_key = get_cache_key(service_class, file_path, **kwargs)
    _service_cache[cache_key] = service_instance
    logger.debug(
        f"Cached {service_class.__name__}",
        extra={"file_path": file_path, "cache_size": len(_service_cache)},
    )


def get_or_cache(
    key: str,
    factory: callable,
) -> Any:
    """
    Get cached instance or create using factory function.

    Args:
        key: Cache key
        factory: Factory function to create instance if not cached

    Returns:
        Cached or newly created instance
    """
    # Check cache first
    cached = _service_cache.get(key)
    if cached:
        logger.debug(f"Cache hit", extra={"key": key})
        return cached

    # Create new instance using factory
    logger.debug(f"Cache miss, creating new instance", extra={"key": key})
    instance = factory()

    # Cache the instance
    _service_cache[key] = instance
    logger.debug(
        f"Cached instance",
        extra={"key": key, "cache_size": len(_service_cache)},
    )

    return instance


def clear_cache() -> None:
    """Clear all cached services."""
    count = len(_service_cache)
    _service_cache.clear()
    logger.info("Cleared service cache", extra={"cleared_count": count})


def get_cache_stats() -> Dict[str, Any]:
    """
    Get cache statistics.

    Returns:
        Dict with cache stats
    """
    return {"size": len(_service_cache), "keys": list(_service_cache.keys())}

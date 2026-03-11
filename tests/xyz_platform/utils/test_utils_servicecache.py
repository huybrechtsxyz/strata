#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_cache.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Tests for service caching functionality.
===============================================================================
"""

import pytest
from xyz_platform.utils.service_cache import (
    get_cache_key,
    get_or_cache,
    clear_cache,
    get_cache_stats,
)


# Simple test class to avoid service dependencies
class MockService:
    """Mock service for testing cache functionality."""

    def __init__(self, name: str):
        self.name = name


class TestCacheKey:
    """Test cache key generation."""

    def test_cache_key_with_file_path(self):
        """Cache key includes class name and file path."""
        key = get_cache_key(MockService, "/path/to/file.yaml")
        assert "MockService" in key
        assert "/path/to/file.yaml" in key

    def test_cache_key_without_file_path(self):
        """Cache key works without file path."""
        key = get_cache_key(MockService, None)
        assert "MockService" in key

    def test_cache_key_with_kwargs(self):
        """Cache key includes kwargs."""
        key = get_cache_key(MockService, "/path/file.yaml", debug=True, count=5)
        assert "count=5" in key
        assert "debug=True" in key

    def test_cache_key_consistency(self):
        """Same parameters generate same key."""
        key1 = get_cache_key(MockService, "/path/file.yaml", count=5)
        key2 = get_cache_key(MockService, "/path/file.yaml", count=5)
        assert key1 == key2


class TestCacheOperations:
    """Test cache get/set operations."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_cache()

    def teardown_method(self):
        """Clear cache after each test."""
        clear_cache()

    def test_get_or_cache_creates_new(self):
        """Creates new instance when not cached."""
        counter = {"calls": 0}

        def factory():
            counter["calls"] += 1
            return MockService("test")

        key = "test:key"
        result = get_or_cache(key, factory)

        assert result.name == "test"
        assert counter["calls"] == 1

    def test_get_or_cache_returns_cached(self):
        """Returns cached instance on subsequent calls."""
        counter = {"calls": 0}

        def factory():
            counter["calls"] += 1
            return MockService("test")

        key = "test:key"
        result1 = get_or_cache(key, factory)
        result2 = get_or_cache(key, factory)

        assert result1 is result2
        assert counter["calls"] == 1  # Factory only called once

    def test_different_keys_cache_separately(self):
        """Different keys cache different instances."""

        def factory1():
            return MockService("service1")

        def factory2():
            return MockService("service2")

        result1 = get_or_cache("key1", factory1)
        result2 = get_or_cache("key2", factory2)

        assert result1 is not result2
        assert result1.name == "service1"
        assert result2.name == "service2"

    def test_clear_cache(self):
        """Clear cache removes all entries."""

        def factory():
            return MockService("test")

        result1 = get_or_cache("key1", factory)
        clear_cache()

        stats = get_cache_stats()
        assert stats["size"] == 0


class TestCacheStats:
    """Test cache statistics."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_cache()

    def teardown_method(self):
        """Clear cache after each test."""
        clear_cache()

    def test_empty_cache_stats(self):
        """Empty cache returns zero size."""
        stats = get_cache_stats()
        assert stats["size"] == 0
        assert stats["keys"] == []

    def test_cache_stats_with_entries(self):
        """Cache stats show correct size and keys."""

        def factory():
            return MockService("test")

        get_or_cache("key1", factory)
        get_or_cache("key2", factory)

        stats = get_cache_stats()
        assert stats["size"] == 2
        assert len(stats["keys"]) == 2
        assert "key1" in stats["keys"]
        assert "key2" in stats["keys"]

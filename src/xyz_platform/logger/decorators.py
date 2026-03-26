#!/usr/bin/env python3
"""
===============================================================================
Script Name   : decorators.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Decorators for logging (performance tracking, tracing)
===============================================================================
"""

import functools
import time
import logging
from typing import Callable, Any


def log_performance(func: Callable) -> Callable:
    """
    Decorator to log function execution time.

    Usage:
        @log_performance
        def slow_function():
            time.sleep(1)
            return "done"
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        logger = logging.getLogger(func.__module__)
        start_time = time.time()

        try:
            result = func(*args, **kwargs)
            duration_ms = (time.time() - start_time) * 1000

            logger.info(
                f"Performance: {func.__name__}",
                extra={
                    "function": func.__name__,
                    "duration_ms": round(duration_ms, 2),
                    "module": func.__module__,
                },
            )

            return result
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                f"Performance (with error): {func.__name__}",
                extra={
                    "function": func.__name__,
                    "duration_ms": round(duration_ms, 2),
                    "module": func.__module__,
                    "error": str(e),
                },
                exc_info=True,
            )
            raise

    return wrapper


def trace_operation(func: Callable) -> Callable:
    """
    Decorator to trace function entry/exit with timing.

    Usage:
        @trace_operation
        def important_function(x, y):
            return x + y
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        logger = logging.getLogger(func.__module__)
        start_time = time.time()

        # Log entry
        logger.debug(
            f"Entering: {func.__name__}",
            extra={
                "function": func.__name__,
                "module": func.__module__,
                "args_count": len(args),
                "kwargs_count": len(kwargs),
            },
        )

        try:
            result = func(*args, **kwargs)
            duration_ms = (time.time() - start_time) * 1000

            # Log exit
            logger.debug(
                f"Exiting: {func.__name__}",
                extra={
                    "function": func.__name__,
                    "module": func.__module__,
                    "duration_ms": round(duration_ms, 2),
                    "status": "success",
                },
            )

            return result
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            # Log exception
            logger.debug(
                f"Exiting with error: {func.__name__}",
                extra={
                    "function": func.__name__,
                    "module": func.__module__,
                    "duration_ms": round(duration_ms, 2),
                    "status": "error",
                    "error": str(e),
                },
            )
            raise

    return wrapper

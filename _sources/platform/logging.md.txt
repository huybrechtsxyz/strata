# Logging Framework

Enterprise-grade structured logging using **structlog**, routed through Python's stdlib `logging` so that Azure Monitor's OpenTelemetry bridge captures all output automatically.

## Quick Start

```python
from strata.logger import get_logger, configure_logging

# Basic usage
logger = get_logger(__name__)
logger.info("Application started")
logger.debug("Debug info", user_id=123)  # structlog: kwargs, not extra={}

# Configure
configure_logging(level="INFO", enable_console=True)
```

## Configuration Options

### 1. From YAML File (Recommended)

```python
configure_logging(config_path="config/logging.yaml")
```

### 2. Programmatic

```python
# Console + Logstash (ELK)
configure_logging(
    level="INFO",
    enable_console=True,
    enable_logstash=True,
    logstash_host="localhost",
    logstash_port=5000
)

# Console + Azure Application Insights
configure_logging(
    level="INFO",
    enable_console=True,
    enable_azure=True,
    azure_connection_string="InstrumentationKey=xxx..."
)
```

## Features

### Correlation IDs

```python
from strata.logger import set_correlation_id

set_correlation_id("req-123-456")
logger.info("Processing request")  # Includes correlation_id
```

### Context Data

```python
from strata.logger import LogContext

with LogContext(user_id=123, tenant="acme"):
    logger.info("User action")  # Includes context
```

### Performance Tracking

```python
from strata.logger import log_performance, trace_operation

@log_performance
def slow_function():
    pass  # Automatically logs execution time

@trace_operation
def traced_function():
    pass  # Logs entry/exit with timing
```

## Integration

### ELK Stack

Logs output standard JSON for Elasticsearch/Logstash:

```json
{
  "timestamp": "2026-02-06T10:30:45.123Z",
  "level": "INFO",
  "logger": "strata.module",
  "message": "Operation completed",
  "correlation_id": "req-123",
  "context": { "user_id": 123 }
}
```

### Azure Application Insights

```python
configure_logging(
    enable_azure=True,
    azure_connection_string="InstrumentationKey=xxx..."
)
```

Or use environment variable:

```bash
export APPLICATIONINSIGHTS_CONNECTION_STRING="InstrumentationKey=xxx..."
```

## Best Practices

1. **Use structured logging** with `extra` dict, not f-strings
2. **Use YAML configs** for environment-specific settings
3. **Set correlation IDs** per request in web apps
4. **Configure via platform config** for centralized management
5. **Shutdown gracefully**: `atexit.register(shutdown_logging)`

## Default Config Files

- `src/STRATA_platform/data/logging.yaml` — default logging config used when none is specified
- `src/STRATA_platform/templates/solution/logging.yaml` — scaffold placed into new workspace on `strata sln init`
# Tool Integration Pattern

## Overview

Standardized pattern for external tools (git, terraform, vault) with validation, error handling, and availability detection.

**Dependencies:** Tools can only depend on Tool Registry and Utilities module.

## Architecture

**BaseTool** - Abstract class providing:

- `is_available()` / `get_version()` / `run()` / `ensure_available()` / `get_info()`

**ToolRegistry** - Singleton for:

- Tool registration with requirements
- Pre-flight validation
- Error messages on missing tools

## Implementation

```python
from xyz_platform.tools.base_tool import BaseTool

class MyTool(BaseTool):
    def __init__(self):
        super().__init__(tool_name="My Tool", command="mytool")

    def get_version_command(self) -> List[str]:
        return ["mytool", "--version"]

    def ensure_available(self) -> tuple[bool, str]:
        if not self.is_available():
            return False, f"{self.tool_name} not installed."
        return True, ""

# Singleton accessor
def get_mytool() -> MyTool:
    global _mytool_instance
    if _mytool_instance is None:
        _mytool_instance = MyTool()
    return _mytool_instance

# Registry usage
ToolRegistry.register_tool("provision", "mytool")
is_valid, missing = ToolRegistry.validate_operation("provision")
```

**Help docs:** Create `src/xyz_platform/docs/mytool.txt` with installation/configuration details.

## Best Practices

- Extend BaseTool for consistency
- Check availability before operations
- Provide helpful errors with installation hints
- Cache expensive operations with `@functools.lru_cache`
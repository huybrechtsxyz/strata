"""Low-level Terraform file loader and merger.

Handles HCL2 file I/O and data merging only.
No knowledge of platform schemas, validation, or file selection strategy.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import hcl2

from strata.logger import get_logger


class TerraformLoader:
    """
    Low-level Terraform/HCL2 file loader and merger.

    Responsibilities:
    - Load .tf files (HCL2) and parse to dict
    - Load .tfvars files (HCL2 variable values) and parse to dict
    - Load .tfvars.json / .tf.json files (JSON format)
    - Deep merge multiple terraform data structures
    - Concatenate raw file contents (for cases where block-level merge is not needed)
    - Write merged output as HCL2 (.tf) or JSON (.tf.json / .auto.tfvars.json)

    Does NOT:
    - Know about platform schemas or models
    - Decide which files to load (caller provides paths)
    - Validate Terraform configuration semantics
    - Resolve @repo/ references or glob patterns
    - Manage state or caching
    """

    # File extensions this loader handles
    HCL_EXTENSIONS = (".tf", ".tfvars")
    JSON_EXTENSIONS = (".tf.json", ".tfvars.json")

    def __init__(self) -> None:
        """Initialize the terraform loader."""
        self._logger = get_logger(__name__)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_hcl_file(self, file_path: Path) -> Dict[str, Any]:
        """
        Load a single .tf or .tfvars file and parse HCL2 to dict.

        Args:
            file_path: Path to the HCL2 file

        Returns:
            Parsed HCL2 content as a dictionary

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file is not a file or parsing fails
        """
        self._validate_file_exists(file_path)

        self._logger.debug("Loading HCL file", file=str(file_path))

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = hcl2.load(f)
        except Exception as e:
            self._logger.error(
                "HCL parsing failed",
                file=str(file_path),
                error=str(e),
            )
            raise ValueError(f"Failed to parse HCL file: {file_path} — {e}") from e

        if data is None:
            self._logger.debug("HCL file is empty", file=str(file_path))
            return {}

        return data

    def load_json_file(self, file_path: Path) -> Dict[str, Any]:
        """
        Load a .tf.json or .tfvars.json file.

        Args:
            file_path: Path to the JSON file

        Returns:
            Parsed JSON content as a dictionary

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file is not valid JSON
        """
        self._validate_file_exists(file_path)

        self._logger.debug("Loading JSON terraform file", file=str(file_path))

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            self._logger.error(
                "JSON parsing failed",
                file=str(file_path),
                error=str(e),
            )
            raise ValueError(f"Failed to parse JSON file: {file_path} — {e}") from e

        if not isinstance(data, dict):
            raise ValueError(f"Terraform JSON file must contain a dictionary, got {type(data).__name__}: {file_path}")

        return data

    def load_file(self, file_path: Path) -> Dict[str, Any]:
        """
        Load any supported terraform file (auto-detects format).

        Supports: .tf, .tfvars (HCL2), .tf.json, .tfvars.json (JSON)

        Args:
            file_path: Path to the terraform file

        Returns:
            Parsed content as a dictionary

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If format is unsupported or parsing fails
        """
        name = file_path.name

        # Check JSON extensions first (they contain dots, so suffix alone is ambiguous)
        if name.endswith(".tf.json") or name.endswith(".tfvars.json"):
            return self.load_json_file(file_path)

        if file_path.suffix in self.HCL_EXTENSIONS:
            return self.load_hcl_file(file_path)

        raise ValueError(
            f"Unsupported terraform file format: {file_path.suffix} "
            f"(supported: {', '.join(self.HCL_EXTENSIONS + self.JSON_EXTENSIONS)})"
        )

    def load_files(self, file_paths: List[Path]) -> List[Dict[str, Any]]:
        """
        Load multiple terraform files.

        Args:
            file_paths: List of paths to terraform files

        Returns:
            List of parsed dictionaries (one per file)

        Raises:
            FileNotFoundError: If any file doesn't exist
            ValueError: If any file has unsupported format or parsing fails
        """
        results = []

        for file_path in file_paths:
            data = self.load_file(file_path)
            results.append(data)

        self._logger.debug("Loaded terraform files", file_count=len(results))
        return results

    def load_raw(self, file_path: Path) -> str:
        """
        Load raw text content of a terraform file (no parsing).

        Used for concatenation strategy where files are appended as-is.

        Args:
            file_path: Path to the file

        Returns:
            Raw file content as string

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        self._validate_file_exists(file_path)

        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------

    def merge(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Deep merge multiple terraform data structures.

        Later items override earlier ones for scalar values.
        Lists within the same block type are concatenated (e.g., multiple
        resource blocks, variable blocks).

        Args:
            items: List of parsed terraform dicts to merge (priority: last wins)

        Returns:
            Single merged dictionary
        """
        if not items:
            return {}

        if len(items) == 1:
            return items[0].copy()

        self._logger.debug("Merging terraform structures", item_count=len(items))

        merged: Dict[str, Any] = {}
        for item in items:
            merged = self._deep_merge_terraform(merged, item)

        self._logger.debug("Terraform merge complete", item_count=len(items))
        return merged

    def load_and_merge(self, file_paths: List[Path]) -> Dict[str, Any]:
        """
        Load multiple terraform files and merge them.

        Convenience method combining load_files() and merge().

        Args:
            file_paths: List of paths (in priority order — later overrides earlier)

        Returns:
            Single merged dictionary
        """
        items = self.load_files(file_paths)
        return self.merge(items)

    def concatenate(self, file_paths: List[Path], separator: str = "\n\n") -> str:
        """
        Concatenate multiple terraform files as raw text.

        Use this for cases where files need to be combined without structural
        parsing (e.g., merging WAF listener blocks that must live in one file).

        Args:
            file_paths: List of paths to concatenate (in order)
            separator: Text between concatenated files (default: double newline)

        Returns:
            Combined file content as a single string
        """
        parts = []

        for file_path in file_paths:
            content = self.load_raw(file_path)
            if content.strip():
                parts.append(content.rstrip())

        self._logger.debug("Concatenated terraform files", file_count=len(parts))
        return separator.join(parts) + "\n"

    def merge_tfvars(self, file_paths: List[Path]) -> Dict[str, Any]:
        """
        Load and deep-merge multiple .tfvars or .tfvars.json files.

        Tfvars files contain flat or nested variable assignments.
        Later files override earlier ones (scalars) or extend them (lists/maps).

        Args:
            file_paths: List of tfvars file paths (priority: last wins)

        Returns:
            Merged variable values dictionary
        """
        items = self.load_files(file_paths)

        if not items:
            return {}

        merged: Dict[str, Any] = {}
        for item in items:
            merged = self._deep_merge_values(merged, item)

        self._logger.debug("Merged tfvars files", file_count=len(items))
        return merged

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def write_hcl(self, data: Dict[str, Any], output_path: Path) -> None:
        """
        Write merged terraform data as HCL2 format.

        Args:
            data: Terraform data dictionary to write
            output_path: Destination file path (.tf)
        """
        self._ensure_parent_dir(output_path)

        self._logger.debug("Writing HCL file", file=str(output_path))

        with open(output_path, "w", encoding="utf-8") as f:
            hcl2.dump(data, f)

    def write_json(self, data: Dict[str, Any], output_path: Path) -> None:
        """
        Write merged terraform data as JSON format (.tf.json or .tfvars.json).

        Args:
            data: Terraform data dictionary to write
            output_path: Destination file path
        """
        self._ensure_parent_dir(output_path)

        self._logger.debug("Writing JSON terraform file", file=str(output_path))

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")

    def write_raw(self, content: str, output_path: Path) -> None:
        """
        Write raw text content to a file.

        Used after concatenate() to persist the combined output.

        Args:
            content: Text content to write
            output_path: Destination file path
        """
        self._ensure_parent_dir(output_path)

        self._logger.debug("Writing raw terraform file", file=str(output_path))

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    def write(self, data: Dict[str, Any], output_path: Path, fmt: Optional[str] = None) -> None:
        """
        Write terraform data in the appropriate format (auto-detected or explicit).

        Args:
            data: Terraform data dictionary
            output_path: Destination file path
            fmt: Explicit format ('hcl' or 'json'). If None, detected from extension.
        """
        if fmt is None:
            name = output_path.name
            if name.endswith(".tf.json") or name.endswith(".tfvars.json"):
                fmt = "json"
            elif output_path.suffix in (".tf", ".tfvars"):
                fmt = "hcl"
            else:
                fmt = "json"  # Default to JSON for unknown extensions

        if fmt == "hcl":
            self.write_hcl(data, output_path)
        else:
            self.write_json(data, output_path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _deep_merge_terraform(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep merge terraform structures with HCL-aware list handling.

        In HCL2, top-level keys like 'resource', 'variable', 'data', 'output',
        'locals', 'module' contain lists of block definitions. These are
        concatenated (not replaced) to combine blocks from multiple files.

        Nested dicts within blocks are recursively merged (later wins).
        """
        # Top-level block types that contain lists of definitions
        list_block_types = ("resource", "data", "variable", "output", "module", "provider", "locals", "moved")

        result = base.copy()

        for key, value in override.items():
            if key not in result:
                result[key] = value
            elif key in list_block_types and isinstance(result[key], list) and isinstance(value, list):
                # Concatenate block lists (combine all resource/variable/etc. definitions)
                result[key] = result[key] + value
            elif isinstance(result[key], dict) and isinstance(value, dict):
                # Recursively merge nested dicts
                result[key] = self._deep_merge_terraform(result[key], value)
            else:
                # Scalar or type mismatch: override wins
                result[key] = value

        return result

    def _deep_merge_values(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep merge tfvars value structures.

        For variable values: maps are recursively merged, scalars and lists
        are replaced by the override (last wins).
        """
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge_values(result[key], value)
            else:
                result[key] = value

        return result

    def _validate_file_exists(self, file_path: Path) -> None:
        """Validate that a file exists and is a regular file."""
        if not file_path.exists():
            raise FileNotFoundError(f"Terraform file not found: {file_path}")
        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")

    def _ensure_parent_dir(self, file_path: Path) -> None:
        """Create parent directories if they don't exist."""
        file_path.parent.mkdir(parents=True, exist_ok=True)

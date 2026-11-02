#!/usr/bin/env python3
"""Tests for strata.logging.config."""

import io
import json
import logging

from strata.logging.config import configure_logging, get_logger, shutdown_logging


def teardown_function() -> None:
    shutdown_logging()


def test_configure_logging_json_output_writes_json_lines():
    stream = io.StringIO()
    configure_logging(level="INFO", json_output=True, stream=stream)
    log = get_logger(__name__)
    log.info("hello", user="alice")

    line = stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello"
    assert payload["user"] == "alice"


def test_configure_logging_redacts_sensitive_fields():
    stream = io.StringIO()
    configure_logging(level="INFO", json_output=True, stream=stream)
    log = get_logger(__name__)
    log.info("login", password="hunter2")

    line = stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["password"] == "***redacted***"


def test_configure_logging_accepts_string_level():
    stream = io.StringIO()
    configure_logging(level="WARNING", json_output=True, stream=stream)
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_json_file_sink_writes_to_file(tmp_path):
    stream = io.StringIO()
    file_path = tmp_path / "strata.log.json"
    configure_logging(level="INFO", json_output=True, stream=stream, json_file_path=str(file_path))
    log = get_logger(__name__)
    log.info("to file")

    assert file_path.exists()
    content = file_path.read_text(encoding="utf-8").strip()
    payload = json.loads(content.splitlines()[-1])
    assert payload["event"] == "to file"

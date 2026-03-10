#!/usr/bin/env python3
"""
===============================================================================
Script Name   : config.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : All fixed configuration values for the xyz-platform package.
===============================================================================
"""

# Build directory structure
DEFAULT_BUILD_PATH = "build"  # Root build directory
DEFAULT_REPOS_PATH = "repos"  # Fetched repositories: build/repos/
DEFAULT_DIST_PATH = "dist"  # Distribution artifacts: dist/

# Repository subdirectories (under build/repos/)
DEFAULT_CONFIG_REPO_PATH = "config"  # Config repo: build/repos/config/
DEFAULT_DEPLOY_REPO_PATH = "deploy"  # Deploy repo: build/repos/deploy/
DEFAULT_SERVICES_REPO_PATH = "services"  # Services repo: build/repos/services/

# Build output structure (under build/{deployid}/)
DEFAULT_CONFIG_BUILD_PATH = "config"  # Assembled config: build/{deployid}/config/
DEFAULT_DEPLOY_BUILD_PATH = "deploy"  # Assembled deploy: build/{deployid}/deploy/
DEFAULT_SERVICES_BUILD_PATH = (
    "services"  # Assembled services: build/{deployid}/services/
)

# Workspace state management
DEFAULT_STATE_DIR = ".xyz-platform"  # Hidden state directory in workspace
DEFAULT_STATE_FILE = "state.json"  # State file name

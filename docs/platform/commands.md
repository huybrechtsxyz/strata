# XYZ Platform - Commands

This document provides an overview of the command-line interface (CLI) commands available in the XYZ Platform. These commands allow users to interact with and manage various aspects of their cloud infrastructure, including provisioning, configuration, and monitoring.

## Command Overview

### General Commands

| Command   | Category | Description                                                            |
| --------- | -------- | ---------------------------------------------------------------------- |
| `help`    | General  | Displays help information about the XYZ Platform CLI and its commands. |
| `version` | General  | Shows the current version of the XYZ Platform CLI.                     |

### Configuration Commands

| Command | Category      | Description                                                                    |
| ------- | ------------- | ------------------------------------------------------------------------------ |
| `init`  | Configuration | Initializes a new configuration                                                |
| `add`   | Configuration | Initializes a new XYZ Platform configuration file in the current directory.    |
| `fetch` | Configuration | Fetches the latest configuration files and updates from the remote repository. |
| `check` | Configuration | Validates the current configuration files for syntax and schema compliance.    |

### Build Commands

| Command | Category      | Description                                                                         |
| ------- | ------------- | ----------------------------------------------------------------------------------- |
| `clean` | Configuration | Cleans up temporary files and resources used by the XYZ Platform.                   |
| `run`   | Build         | Executes the build process for the XYZ Platform based on the current configuration. |

### Deploy Commands

| Command   | Category      | Description                                                                 |
| --------- | ------------- | --------------------------------------------------------------------------- |
| `check`   | Configuration | Validates the current configuration files for syntax and schema compliance. |
| `setup`   | Deployment    | Sets up the initial environment and configurations for the XYZ Platform.    |
| `plan`    | Deployment    | Generates an execution plan based on the current configuration files.       |
| `apply`   | Deployment    | Applies the planned changes to the cloud infrastructure.                    |
| `destroy` | Deployment    | Destroys the provisioned cloud infrastructure managed by the XYZ Platform.  |
| `status`  | Deployment    | Displays the current status of the provisioned resources.                   |
| `logs`    | Deployment    | Retrieves and displays logs from the XYZ Platform services.                 |
| `output`  | Deployment    | Shows the output values from the last applied configuration.                |

### Service Commands

| Command   | Category | Description                                                 |
| --------- | -------- | ----------------------------------------------------------- |
| `start`   | Service  | Starts the XYZ Platform services.                           |
| `stop`    | Service  | Stops the XYZ Platform services.                            |
| `restart` | Service  | Restarts the XYZ Platform services.                         |
| `status`  | Service  | Displays the current status of the XYZ Platform services.   |
| `logs`    | Service  | Retrieves and displays logs from the XYZ Platform services. |
| `update`  | Service  | Updates the XYZ Platform services to the latest version.    |
| `remove`  | Service  | Removes the XYZ Platform services from the system.          |
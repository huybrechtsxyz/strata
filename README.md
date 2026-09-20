# Strata v2

Infrastructure as Code Platform - Version 2.0

## Project Structure

```
strata-v2/
├── .venv/                  # Python virtual environment
├── src/
│   ├── strata/
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── common_models.py       # Base classes and enums
│   │   │   ├── auth_models.py         # Authentication models
│   │   │   └── provider_model.py      # Provider configuration model
│   │   └── __init__.py
│   └── tests/
│       └── __init__.py
├── pyproject.toml          # Project configuration
└── README.md              # This file
```

## Models Implemented

### Core Models

1. **ProviderModel** - Root model for provider configuration
   - metadata (name, annotations, labels, tags)
   - specification (properties, authentication, references, lifecycle)

2. **ProviderPropertiesModel** - Provider configuration details
   - type: Cloud/infrastructure provider (e.g., kamatera, local)
   - region: Datacenter region
   - location: Optional datacenter location
   - organization: Optional organization/subscription
   - version: Optional version constraint

3. **AuthenticationModel** - Multi-method authentication support
   - OAuth2
   - AWS (access key)
   - GCP (service account)
   - API Key
   - Certificate/mTLS
   - SAML
   - CLI-based
   - Managed Identity

4. **Common Models**
   - PlatformBaseModel: Base for all models
   - PlatformKind: Enum of supported kinds
   - PlatformVersion: API version support
   - CommonLifecycleModel: IaC workflow phases

## Getting Started

### Setup Development Environment

```powershell
# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy src/
```

## Development Notes

- All models are Pydantic v2 based
- Strict validation with extra field rejection
- Type hints throughout
- Support for key references (runtime resolution of env vars/secrets)

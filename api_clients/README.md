# API Clients Package

Reusable API client implementations for HubSpot and Lemlist APIs.

## Overview

This package provides modular, reusable API clients that can be used across different projects. The clients follow a clean architecture with:

- **Base client** with common HTTP request handling
- **Config dataclasses** for easy configuration
- **Streamlit wrappers** for UI integration (optional)
- **Comprehensive error handling** with custom exceptions
- **Rate limit management** with automatic retries
- **Pagination support** for large datasets

## Installation

The package is self-contained within this project. To use in another project, copy the entire `api_clients/` directory.

### Dependencies

```bash
pip install requests
pip install streamlit  # Only needed for Streamlit wrappers
```

## Quick Start

### HubSpot Client

```python
from api_clients import HubSpotClient, HubSpotConfig

# Create config
config = HubSpotConfig(
    api_token="your-token-here",
    batch_size=50,
    batch_delay=0.25
)

# Initialize client
client = HubSpotClient(config)

# Verify token
if client.verify_token():
    # Get all contacts
    contacts = client.get_all_contacts(['email', 'firstname'])

    # Update contact properties
    client.update_contact_properties("12345", {
        "company": "Acme Inc",
        "jobtitle": "Marketing Manager"
    })
```

### Lemlist Client

```python
from api_clients import LemlistClient, LemlistConfig

# Create config
config = LemlistConfig(
    api_key="your-api-key-here",
    pagination_limit=100
)

# Initialize client
client = LemlistClient(config)

# Get all campaigns
campaigns = client.get_all_campaigns(status='running')

# Get activities for a campaign
activities = client.get_all_activities(campaign_id='cmp_123')

# Get lead details
lead = client.get_lead_details('john@example.com')
```

### Streamlit Integration

```python
from api_clients import HubSpotConfig
from api_clients.streamlit_wrappers import StreamlitHubSpotClient

# Create client with Streamlit UI notifications
config = HubSpotConfig(api_token="your-token")
client = StreamlitHubSpotClient(config)

# Now st.warning() messages will be shown for rate limits, errors, etc.
contacts = client.get_all_contacts(['email'])
```

## Architecture

```
api_clients/
├── __init__.py              # Package exports
├── base_client.py           # BaseAPIClient (abstract)
├── config.py                # Configuration dataclasses
├── hubspot.py               # HubSpotClient
├── lemlist.py               # LemlistClient
└── streamlit_wrappers.py    # UI integration (optional)
```

### Base Client

`BaseAPIClient` provides:
- HTTP request handling with retries
- Rate limit management
- Error handling with custom exceptions
- Progress callback system

All clients inherit from `BaseAPIClient`.

### Configuration

Configuration is handled via dataclasses:

```python
@dataclass
class HubSpotConfig:
    api_token: str
    base_url: str = "https://api.hubapi.com"
    default_timeout: int = 30
    max_retries: int = 3
    batch_size: int = 50
    batch_delay: float = 0.25

@dataclass
class LemlistConfig:
    api_key: str
    base_url: str = "https://api.lemlist.com/api"
    default_timeout: int = 30
    max_retries: int = 3
    pagination_limit: int = 100
    pagination_delay: float = 0.1
    rate_limit_threshold: int = 5
```

### Error Handling

The package provides custom exceptions:

```python
from api_clients.base_client import (
    APIError,                 # Base exception
    UnauthorizedError,        # Invalid token (401)
    NotFoundError,            # Resource not found (404)
    RateLimitError,          # Rate limit exceeded (429)
)

try:
    contacts = client.get_all_contacts(['email'])
except UnauthorizedError:
    print("Invalid token!")
except RateLimitError as e:
    print(f"Rate limit hit, retry after {e.retry_after}s")
except APIError as e:
    print(f"API error: {e}")
```

## Features

### HubSpot Client

**Contact Operations:**
- `get_all_contacts()` - Fetch all contacts with cursor pagination
- `get_all_contacts_with_companies()` - Fetch contacts with company associations
- `get_contact()` - Get single contact by ID
- `update_contact_properties()` - Update single contact
- `batch_update_contacts()` - Batch update up to 100 contacts

**Company Operations:**
- `batch_get_companies()` - Batch fetch company details

**Notes Operations:**
- `get_notes_for_contact()` - Fetch all notes for a contact
- `get_note()` - Get single note by ID
- `delete_note()` - Delete single note
- `batch_delete_notes()` - Batch delete up to 100 notes

### Lemlist Client

**Campaign Operations:**
- `get_all_campaigns()` - Fetch all campaigns with pagination
- Filter by status: running, draft, archived, ended, paused

**Activity Operations:**
- `get_all_activities()` - Fetch all activities for a campaign

**Lead Operations:**
- `get_lead_details()` - Fetch detailed lead info by email

## Rate Limits

### HubSpot
- **Standard API**: 100 requests per 10 seconds
- **Batch API**: 4 requests per second
- Automatically handles 429 responses with retry-after

### Lemlist
- **Rate Limit**: 20 requests per 2 seconds
- Monitors `X-RateLimit-Remaining` header
- Warns when remaining requests < 5

## Extending

To add a new API client:

1. Create new file in `api_clients/` (e.g., `myapi.py`)
2. Inherit from `BaseAPIClient`
3. Implement `verify_token()` method
4. Add API-specific methods
5. Create config dataclass in `config.py`
6. Export in `__init__.py`

Example:

```python
from .base_client import BaseAPIClient, UnauthorizedError

class MyAPIClient(BaseAPIClient):
    def __init__(self, config: MyAPIConfig):
        super().__init__(config.base_url, config.default_timeout)
        self.config = config
        self.session.headers.update({
            'Authorization': f'Bearer {config.api_token}'
        })

    def verify_token(self) -> bool:
        try:
            self._make_request('GET', '/me')
            return True
        except UnauthorizedError:
            return False

    def get_users(self) -> List[Dict]:
        response = self._make_request('GET', '/users')
        return response.json()
```

## License

MIT License - Use freely in your projects.

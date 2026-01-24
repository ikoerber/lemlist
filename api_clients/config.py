"""
Configuration dataclasses for API clients.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HubSpotConfig:
    """Configuration for HubSpot API client.

    Attributes:
        api_token: HubSpot Private App access token
        base_url: Base URL for API (default: https://api.hubapi.com)
        default_timeout: Default timeout for requests in seconds
        max_retries: Maximum retry attempts for failed requests
        batch_size: Number of contacts per batch update (max 100)
        batch_delay: Delay between batch requests in seconds (rate limit: 4 req/sec)
    """
    api_token: str
    base_url: str = "https://api.hubapi.com"
    default_timeout: int = 30
    max_retries: int = 3
    batch_size: int = 50
    batch_delay: float = 0.25  # 4 requests/second


@dataclass
class LemlistConfig:
    """Configuration for Lemlist API client.

    Attributes:
        api_key: Lemlist API key
        base_url: Base URL for API (default: https://api.lemlist.com/api)
        default_timeout: Default timeout for requests in seconds
        max_retries: Maximum retry attempts for failed requests
        pagination_limit: Number of results per page (max 100)
        pagination_delay: Delay between pagination requests in seconds
        rate_limit_threshold: Trigger warning when remaining requests below this
    """
    api_key: str
    base_url: str = "https://api.lemlist.com/api"
    default_timeout: int = 30
    max_retries: int = 3
    pagination_limit: int = 100
    pagination_delay: float = 0.1
    rate_limit_threshold: int = 5  # Warn when X-RateLimit-Remaining < 5

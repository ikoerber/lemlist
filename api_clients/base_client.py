"""
Base API Client with common functionality for HTTP requests.

Provides:
- HTTP request handling with retries
- Rate limit management
- Error handling
- Logging
"""

import requests
import time
import logging
from typing import Dict, Any, Optional, Callable
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class APIError(Exception):
    """Base exception for API errors"""
    pass


class RateLimitError(APIError):
    """Raised when API rate limit is exceeded"""
    def __init__(self, retry_after: int = 10):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s")


class UnauthorizedError(APIError):
    """Raised when API token is invalid"""
    pass


class NotFoundError(APIError):
    """Raised when resource is not found"""
    pass


class BaseAPIClient(ABC):
    """Abstract base class for API clients.

    Provides common functionality:
    - Session management
    - HTTP request handling with retries
    - Rate limit handling
    - Error handling
    - Progress callbacks
    """

    def __init__(self, base_url: str, default_timeout: int = 30):
        """Initialize base API client.

        Args:
            base_url: Base URL for API endpoints
            default_timeout: Default timeout for requests in seconds
        """
        self.base_url = base_url
        self.default_timeout = default_timeout
        self.session = requests.Session()
        self._progress_callback: Optional[Callable] = None

    def set_progress_callback(self, callback: Optional[Callable]):
        """Set callback for progress updates.

        Args:
            callback: Function to call with progress updates.
                     Signature depends on implementation.
        """
        self._progress_callback = callback

    def _notify_progress(self, *args, **kwargs):
        """Notify progress callback if set."""
        if self._progress_callback:
            self._progress_callback(*args, **kwargs)

    def _make_request(self,
                     method: str,
                     endpoint: str,
                     params: Optional[Dict] = None,
                     json: Optional[Dict] = None,
                     max_retries: int = 3,
                     **kwargs) -> requests.Response:
        """Make HTTP request with retry logic and error handling.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE, etc.)
            endpoint: API endpoint (without base URL)
            params: Query parameters
            json: JSON body for POST/PATCH
            max_retries: Maximum retry attempts
            **kwargs: Additional arguments for requests

        Returns:
            Response object

        Raises:
            UnauthorizedError: Invalid token
            NotFoundError: Resource not found
            RateLimitError: Rate limit exceeded after retries
            APIError: Other API errors
        """
        url = f"{self.base_url}{endpoint}"

        # Set default timeout if not specified
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self.default_timeout

        for attempt in range(max_retries):
            try:
                response = self.session.request(
                    method, url, params=params, json=json, **kwargs
                )

                # Check rate limiting
                if response.status_code == 429:
                    retry_after = self._extract_retry_after(response)
                    logger.warning(f"Rate limit hit. Waiting {retry_after}s...")

                    if attempt < max_retries - 1:
                        self._notify_rate_limit(retry_after, attempt, max_retries)
                        time.sleep(retry_after)
                        continue
                    else:
                        raise RateLimitError(retry_after)

                # Check authorization
                if response.status_code == 401:
                    raise UnauthorizedError("Invalid API token")

                # Check not found
                if response.status_code == 404:
                    raise NotFoundError("Resource not found")

                # Check other errors
                if response.status_code >= 400:
                    error_msg = self._extract_error_message(response)
                    raise APIError(f"API error ({response.status_code}): {error_msg}")

                return response

            except requests.exceptions.Timeout:
                logger.warning(f"Request timeout (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    self._notify_timeout(wait_time, attempt, max_retries)
                    time.sleep(wait_time)
                    continue
                raise APIError("Request timeout after retries")

            except requests.exceptions.RequestException as e:
                logger.error(f"Request error: {e}")
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    self._notify_error(str(e), wait_time, attempt, max_retries)
                    time.sleep(wait_time)
                    continue
                raise APIError(f"Request failed: {e}")

        raise APIError("Max retries exceeded")

    def _extract_retry_after(self, response: requests.Response) -> int:
        """Extract retry-after time from response headers.

        Args:
            response: Response object

        Returns:
            Retry after time in seconds (default 10)
        """
        return int(response.headers.get('Retry-After', 10))

    def _extract_error_message(self, response: requests.Response) -> str:
        """Extract error message from response.

        Args:
            response: Response object

        Returns:
            Error message string
        """
        error_msg = response.text
        try:
            error_data = response.json()
            error_msg = error_data.get('message', error_msg)
        except Exception:
            pass
        return error_msg

    # Notification methods (can be overridden for custom behavior)

    def _notify_rate_limit(self, retry_after: int, attempt: int, max_retries: int):
        """Notify about rate limit. Override for custom behavior."""
        pass

    def _notify_timeout(self, wait_time: int, attempt: int, max_retries: int):
        """Notify about timeout. Override for custom behavior."""
        pass

    def _notify_error(self, error: str, wait_time: int, attempt: int, max_retries: int):
        """Notify about request error. Override for custom behavior."""
        pass

    @abstractmethod
    def verify_token(self) -> bool:
        """Verify that the API token is valid.

        Returns:
            True if token is valid, False otherwise
        """
        pass

"""
Streamlit integration wrappers for API clients.

These wrapper classes extend the base clients with Streamlit-specific
UI notifications (warnings, errors, progress indicators).
"""

import time
import streamlit as st
from typing import Optional

from .hubspot import HubSpotClient
from .lemlist import LemlistClient
from .config import HubSpotConfig, LemlistConfig


class StreamlitHubSpotClient(HubSpotClient):
    """HubSpot client with Streamlit UI integration.

    Extends HubSpotClient to show st.warning() and st.error() messages
    for rate limits, timeouts, and errors.
    """

    def __init__(self, config: HubSpotConfig):
        """Initialize Streamlit-integrated HubSpot client.

        Args:
            config: HubSpotConfig with API token and settings
        """
        super().__init__(config)

    def _notify_rate_limit(self, retry_after: int, attempt: int, max_retries: int):
        """Show Streamlit warning about rate limit."""
        st.warning(f"⏳ HubSpot Rate Limit erreicht. Retry in {retry_after}s... (Versuch {attempt + 1}/{max_retries})")

    def _notify_timeout(self, wait_time: int, attempt: int, max_retries: int):
        """Show Streamlit warning about timeout."""
        st.warning(f"⏳ HubSpot Timeout. Retry in {wait_time}s... (Versuch {attempt + 1}/{max_retries})")

    def _notify_error(self, error: str, wait_time: int, attempt: int, max_retries: int):
        """Show Streamlit warning about request error."""
        st.warning(f"⏳ HubSpot Netzwerkfehler. Retry in {wait_time}s... (Versuch {attempt + 1}/{max_retries})")


class StreamlitLemlistClient(LemlistClient):
    """Lemlist client with Streamlit UI integration.

    Extends LemlistClient to show st.warning() messages for rate limits,
    timeouts, and low rate limit warnings.
    """

    def __init__(self, config: LemlistConfig):
        """Initialize Streamlit-integrated Lemlist client.

        Args:
            config: LemlistConfig with API key and settings
        """
        super().__init__(config)

    def _notify_rate_limit(self, retry_after: int, attempt: int, max_retries: int):
        """Show Streamlit warning about rate limit."""
        st.warning(f"⏳ Lemlist Rate Limit erreicht. Retry in {retry_after}s... (Versuch {attempt + 1}/{max_retries})")

    def _notify_timeout(self, wait_time: int, attempt: int, max_retries: int):
        """Show Streamlit warning about timeout."""
        st.warning(f"⏳ Lemlist Timeout. Retry in {wait_time}s... (Versuch {attempt + 1}/{max_retries})")

    def _notify_error(self, error: str, wait_time: int, attempt: int, max_retries: int):
        """Show Streamlit warning about request error."""
        st.warning(f"⏳ Lemlist Netzwerkfehler. Retry in {wait_time}s... (Versuch {attempt + 1}/{max_retries})")

    def _notify_low_rate_limit(self, remaining: int, reset_in: int):
        """Show Streamlit warning about low rate limit."""
        if reset_in > 0:
            st.warning(f"⏳ Lemlist Rate Limit niedrig ({remaining} remaining). Warte {reset_in}s...")
            time.sleep(reset_in)

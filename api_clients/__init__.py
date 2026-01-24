"""
API Clients Package

Reusable API client implementations for HubSpot and Lemlist.

Main exports:
- HubSpotClient: Client for HubSpot CRM API
- LemlistClient: Client for Lemlist API
- HubSpotConfig, LemlistConfig: Configuration dataclasses
- StreamlitHubSpotClient, StreamlitLemlistClient: Streamlit-integrated clients
"""

from .hubspot import HubSpotClient
from .lemlist import LemlistClient
from .config import HubSpotConfig, LemlistConfig
from .streamlit_wrappers import StreamlitHubSpotClient, StreamlitLemlistClient

__all__ = [
    'HubSpotClient',
    'LemlistClient',
    'HubSpotConfig',
    'LemlistConfig',
    'StreamlitHubSpotClient',
    'StreamlitLemlistClient',
]

"""
SQLite Database module for Lemlist Campaign Data Extractor

Provides persistent storage for campaigns, leads, and activities with
incremental update support and background HubSpot/LinkedIn data fetching.
"""

import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager
import os


class LemlistDB:
    """SQLite database manager for Lemlist data"""

    def __init__(self, db_path: str = "lemlist_data.db"):
        """Initialize database connection

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Initialize database schema"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Campaigns table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT,
                    last_updated TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Leads table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    email TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    first_name TEXT,
                    last_name TEXT,
                    hubspot_id TEXT,
                    linkedin_url TEXT,
                    last_updated TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                )
            """)

            # Activities table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activities (
                    id TEXT PRIMARY KEY,
                    lead_email TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    type_display TEXT,
                    created_at TIMESTAMP NOT NULL,
                    details TEXT,
                    raw_json TEXT,
                    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (lead_email) REFERENCES leads(email),
                    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                )
            """)

            # Indexes for performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_activities_campaign
                ON activities(campaign_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_activities_lead
                ON activities(lead_email)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_activities_created
                ON activities(created_at DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_leads_campaign
                ON leads(campaign_id)
            """)

    # Campaign Operations

    def upsert_campaign(self, campaign_id: str, name: str, status: str):
        """Insert or update campaign"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO campaigns (campaign_id, name, status, last_updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(campaign_id) DO UPDATE SET
                    name = excluded.name,
                    status = excluded.status,
                    last_updated = excluded.last_updated
            """, (campaign_id, name, status, datetime.now()))

    def get_campaign(self, campaign_id: str) -> Optional[Dict]:
        """Get campaign by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM campaigns WHERE campaign_id = ?
            """, (campaign_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_campaign_last_updated(self, campaign_id: str) -> Optional[datetime]:
        """Get last update timestamp for campaign"""
        campaign = self.get_campaign(campaign_id)
        if campaign and campaign['last_updated']:
            return datetime.fromisoformat(campaign['last_updated'])
        return None

    # Lead Operations

    def upsert_leads(self, leads: List[Dict], campaign_id: str):
        """Insert or update multiple leads"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now()

            for lead in leads:
                # Extract HubSpot and LinkedIn data if present
                hubspot_id = lead.get('hubspotLeadId') or lead.get('hubspot_id')
                linkedin_url = (lead.get('linkedinUrl') or
                               lead.get('linkedinPublicUrl') or
                               lead.get('linkedin') or
                               lead.get('linkedInUrl'))

                cursor.execute("""
                    INSERT INTO leads (
                        email, campaign_id, first_name, last_name,
                        hubspot_id, linkedin_url, last_updated
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(email) DO UPDATE SET
                        first_name = excluded.first_name,
                        last_name = excluded.last_name,
                        hubspot_id = COALESCE(excluded.hubspot_id, leads.hubspot_id),
                        linkedin_url = COALESCE(excluded.linkedin_url, leads.linkedin_url),
                        last_updated = excluded.last_updated
                """, (
                    lead.get('email'),
                    campaign_id,
                    lead.get('firstName'),
                    lead.get('lastName'),
                    hubspot_id,
                    linkedin_url,
                    now
                ))

    def update_lead_details(self, email: str, hubspot_id: Optional[str] = None,
                           linkedin_url: Optional[str] = None):
        """Update HubSpot ID and/or LinkedIn URL for a lead.

        Uses a single UPDATE statement with COALESCE to only update
        non-NULL values while preserving existing data.
        """
        if hubspot_id is None and linkedin_url is None:
            return  # Nothing to update

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE leads
                SET hubspot_id = COALESCE(?, hubspot_id),
                    linkedin_url = COALESCE(?, linkedin_url),
                    last_updated = ?
                WHERE email = ?
            """, (hubspot_id, linkedin_url, datetime.now(), email))

    def get_lead(self, email: str) -> Optional[Dict]:
        """Get lead by email"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM leads WHERE email = ?
            """, (email,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_leads_by_campaign(self, campaign_id: str) -> List[Dict]:
        """Get all leads for a campaign"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM leads WHERE campaign_id = ?
            """, (campaign_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_leads_without_hubspot_id(self, campaign_id: str, limit: int = 100) -> List[str]:
        """Get emails of leads that don't have HubSpot ID yet"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT email FROM leads
                WHERE campaign_id = ? AND hubspot_id IS NULL
                LIMIT ?
            """, (campaign_id, limit))
            return [row['email'] for row in cursor.fetchall()]

    # Activity Operations

    def upsert_activities(self, activities: List[Dict], campaign_id: str):
        """Insert or update multiple activities"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            for activity in activities:
                # Generate activity ID if not present
                activity_id = activity.get('_id', f"{activity.get('leadEmail')}_{activity.get('createdAt')}")

                cursor.execute("""
                    INSERT INTO activities (
                        id, lead_email, campaign_id, type, type_display,
                        created_at, details, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        type_display = excluded.type_display,
                        details = excluded.details,
                        raw_json = excluded.raw_json,
                        synced_at = CURRENT_TIMESTAMP
                """, (
                    activity_id,
                    activity.get('leadEmail'),
                    campaign_id,
                    activity.get('type'),
                    activity.get('type_display', activity.get('type')),
                    activity.get('createdAt'),
                    activity.get('details', ''),
                    json.dumps(activity)
                ))

    def get_activities_by_campaign(self, campaign_id: str) -> List[Dict]:
        """Get all activities for a campaign with lead details"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    a.*,
                    l.first_name as lead_first_name,
                    l.last_name as lead_last_name,
                    l.hubspot_id as lead_hubspot_id,
                    l.linkedin_url as lead_linkedin_url
                FROM activities a
                LEFT JOIN leads l ON a.lead_email = l.email
                WHERE a.campaign_id = ?
                ORDER BY a.created_at ASC
            """, (campaign_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_activities_by_lead(self, email: str) -> List[Dict]:
        """Get all activities for a specific lead"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM activities
                WHERE lead_email = ?
                ORDER BY created_at ASC
            """, (email,))
            return [dict(row) for row in cursor.fetchall()]

    def get_latest_activity_date(self, campaign_id: str) -> Optional[str]:
        """Get the created_at timestamp of the most recent activity"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MAX(created_at) as latest
                FROM activities
                WHERE campaign_id = ?
            """, (campaign_id,))
            row = cursor.fetchone()
            return row['latest'] if row and row['latest'] else None

    # Statistics

    def get_campaign_stats(self, campaign_id: str) -> Dict:
        """Get statistics for a campaign"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Count leads
            cursor.execute("""
                SELECT COUNT(*) as count FROM leads WHERE campaign_id = ?
            """, (campaign_id,))
            lead_count = cursor.fetchone()['count']

            # Count activities
            cursor.execute("""
                SELECT COUNT(*) as count FROM activities WHERE campaign_id = ?
            """, (campaign_id,))
            activity_count = cursor.fetchone()['count']

            # Count leads with HubSpot ID
            cursor.execute("""
                SELECT COUNT(*) as count FROM leads
                WHERE campaign_id = ? AND hubspot_id IS NOT NULL
            """, (campaign_id,))
            hubspot_count = cursor.fetchone()['count']

            # Get last update
            campaign = self.get_campaign(campaign_id)
            last_updated = campaign['last_updated'] if campaign else None

            return {
                'leads': lead_count,
                'activities': activity_count,
                'leads_with_hubspot': hubspot_count,
                'last_updated': last_updated
            }

    # Utility

    def clear_campaign_data(self, campaign_id: str):
        """Delete all data for a campaign"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM activities WHERE campaign_id = ?", (campaign_id,))
            cursor.execute("DELETE FROM leads WHERE campaign_id = ?", (campaign_id,))
            cursor.execute("DELETE FROM campaigns WHERE campaign_id = ?", (campaign_id,))

    def vacuum(self):
        """Optimize database.

        Note: VACUUM cannot run inside a transaction, so we use
        isolation_level=None for autocommit mode.
        """
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()

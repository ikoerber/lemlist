"""
SQLite Database module for Lemlist Campaign Data Extractor

Provides persistent storage for campaigns, leads, and activities with
incremental update support and background HubSpot/LinkedIn data fetching.
"""

import sqlite3
import json
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Any
from contextlib import contextmanager
import os

# Engagement score weights for calculating lead engagement
# Higher values indicate more positive engagement signals
ENGAGEMENT_WEIGHTS = {
    'emailsSent': 1,
    'emailsOpened': 3,
    'emailsBounced': -5,
    'emailsClicked': 4,
    'emailsReplied': 5,
    'emailsFailed': -3,
    'emailsUnsubscribed': -10,
    'linkedinVisitDone': 2,
    'linkedinInviteDone': 2,
    'linkedinInviteAccepted': 5,
    'linkedinSent': 2,
    'linkedinOpened': 4,
    'linkedinReplied': 5,
    'aircallDone': 3,
    'aircallAnswered': 5,
    'manualDone': 2,
    'apiDone': 1,
    'paused': 0,
    'resumed': 0,
    'conditionChosen': 0,
    'hooked': 3,
    'interested': 10,
    'notInterested': -5,
    'skipped': -2,
    'outOfOffice': 0,
}


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

            # Leads table - lead_id from Lemlist is PRIMARY KEY
            # This allows same email in multiple campaigns (each with unique lead_id)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    lead_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
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

            # Activities table - references lead_id instead of email
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activities (
                    id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    lead_email TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    type_display TEXT,
                    created_at TIMESTAMP NOT NULL,
                    details TEXT,
                    raw_json TEXT,
                    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (lead_id) REFERENCES leads(lead_id),
                    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                )
            """)

            # Indexes for performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_activities_campaign
                ON activities(campaign_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_activities_lead_id
                ON activities(lead_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_activities_lead_email
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
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_leads_email
                ON leads(email)
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
        """Insert or update multiple leads.

        Uses lead_id (from Lemlist) as primary key. This allows the same
        email to exist in multiple campaigns with different lead_ids.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now()

            for lead in leads:
                # lead_id and email are required - skip leads without them
                lead_id = lead.get('leadId') or lead.get('lead_id')
                email = lead.get('email')
                if not lead_id or not email:
                    continue

                # Extract HubSpot and LinkedIn data if present
                hubspot_id = lead.get('hubspotLeadId') or lead.get('hubspot_id')
                linkedin_url = (lead.get('linkedinUrl') or
                               lead.get('linkedinPublicUrl') or
                               lead.get('linkedin') or
                               lead.get('linkedInUrl'))

                cursor.execute("""
                    INSERT INTO leads (
                        lead_id, email, campaign_id, first_name, last_name,
                        hubspot_id, linkedin_url, last_updated
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(lead_id) DO UPDATE SET
                        email = excluded.email,
                        first_name = excluded.first_name,
                        last_name = excluded.last_name,
                        hubspot_id = COALESCE(excluded.hubspot_id, leads.hubspot_id),
                        linkedin_url = COALESCE(excluded.linkedin_url, leads.linkedin_url),
                        last_updated = excluded.last_updated
                """, (
                    lead_id,
                    lead.get('email'),
                    campaign_id,
                    lead.get('firstName'),
                    lead.get('lastName'),
                    hubspot_id,
                    linkedin_url,
                    now
                ))

    def update_lead_details(self, lead_id: str, hubspot_id: Optional[str] = None,
                           linkedin_url: Optional[str] = None):
        """Update HubSpot ID and/or LinkedIn URL for a lead.

        Args:
            lead_id: Lemlist lead ID (primary key)
            hubspot_id: Optional HubSpot contact ID
            linkedin_url: Optional LinkedIn profile URL

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
                WHERE lead_id = ?
            """, (hubspot_id, linkedin_url, datetime.now(), lead_id))

    def get_lead(self, lead_id: str) -> Optional[Dict]:
        """Get lead by lead_id"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM leads WHERE lead_id = ?
            """, (lead_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_lead_by_email(self, email: str, campaign_id: str) -> Optional[Dict]:
        """Get lead by email and campaign_id.

        Since email is not unique (same email can be in multiple campaigns),
        we need campaign_id to uniquely identify the lead.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM leads WHERE email = ? AND campaign_id = ?
            """, (email, campaign_id))
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

    def get_leads_without_hubspot_id(self, campaign_id: str, limit: int = 100) -> List[Dict]:
        """Get leads that don't have HubSpot ID yet.

        Returns list of dicts with lead_id and email for fetching HubSpot IDs.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT lead_id, email FROM leads
                WHERE campaign_id = ? AND hubspot_id IS NULL
                LIMIT ?
            """, (campaign_id, limit))
            return [{'lead_id': row['lead_id'], 'email': row['email']} for row in cursor.fetchall()]

    # Activity Operations

    def upsert_activities(self, activities: List[Dict], campaign_id: str):
        """Insert or update multiple activities.

        Each activity must have a leadId for proper foreign key relationship.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            for activity in activities:
                # Generate activity ID if not present
                activity_id = activity.get('_id', f"{activity.get('leadId')}_{activity.get('createdAt')}")

                # lead_id is required for FK relationship
                lead_id = activity.get('leadId')
                if not lead_id:
                    continue

                cursor.execute("""
                    INSERT INTO activities (
                        id, lead_id, lead_email, campaign_id, type, type_display,
                        created_at, details, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        type_display = excluded.type_display,
                        details = excluded.details,
                        raw_json = excluded.raw_json,
                        synced_at = CURRENT_TIMESTAMP
                """, (
                    activity_id,
                    lead_id,
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
                LEFT JOIN leads l ON a.lead_id = l.lead_id
                WHERE a.campaign_id = ?
                ORDER BY a.created_at ASC
            """, (campaign_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_activities_by_lead(self, lead_id: str) -> List[Dict]:
        """Get all activities for a specific lead by lead_id"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM activities
                WHERE lead_id = ?
                ORDER BY created_at ASC
            """, (lead_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_activities_by_email(self, email: str) -> List[Dict]:
        """Get all activities for a specific email (across all campaigns).

        Used for calculating metrics across all campaigns for HubSpot sync.
        """
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

    # HubSpot Sync Operations

    def get_all_leads_with_hubspot_ids(self, campaign_id: str) -> List[Dict]:
        """Get all leads that have HubSpot IDs for sync.

        Args:
            campaign_id: Campaign to get leads for

        Returns:
            List of dicts with lead_id, email, hubspot_id, first_name, last_name
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT lead_id, email, hubspot_id, first_name, last_name
                FROM leads
                WHERE campaign_id = ?
                AND hubspot_id IS NOT NULL
                AND hubspot_id != ''
            """, (campaign_id,))
            return [dict(row) for row in cursor.fetchall()]

    def calculate_lead_metrics(self, email: str, campaign_id: str) -> Optional[Dict[str, Any]]:
        """Calculate aggregated metrics for a lead.

        Computes engagement metrics from all activities for the given email
        within the specified campaign.

        Args:
            email: Lead email address
            campaign_id: Campaign ID for context (campaign name)

        Returns:
            Dict with all HubSpot property values, or None if no activities
        """
        # Get all activities for this email (across all campaigns for full picture)
        activities = self.get_activities_by_email(email)

        if not activities:
            return None

        # Parse dates and count by type
        dates = []
        type_counts = {}

        for activity in activities:
            # Parse created_at
            created_at = activity.get('created_at')
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    dates.append(dt)
                except (ValueError, AttributeError):
                    pass

            # Count by type
            activity_type = activity.get('type', '')
            type_counts[activity_type] = type_counts.get(activity_type, 0) + 1

        if not dates:
            return None

        # Calculate date metrics
        first_date = min(dates)
        last_date = max(dates)
        days_in_campaign = (datetime.now(first_date.tzinfo) - first_date).days if first_date.tzinfo else (datetime.now() - first_date).days

        # Email metrics
        emails_sent = type_counts.get('emailsSent', 0)
        emails_opened = type_counts.get('emailsOpened', 0)
        emails_bounced = type_counts.get('emailsBounced', 0)
        emails_clicked = type_counts.get('emailsClicked', 0)
        emails_replied = type_counts.get('emailsReplied', 0)

        # Calculate open rate
        email_open_rate = round((emails_opened / emails_sent) * 100, 1) if emails_sent > 0 else 0

        # LinkedIn metrics
        linkedin_visits = type_counts.get('linkedinVisitDone', 0)
        linkedin_invites_sent = type_counts.get('linkedinInviteDone', 0)
        linkedin_invites_accepted = type_counts.get('linkedinInviteAccepted', 0)
        linkedin_messages_sent = type_counts.get('linkedinSent', 0)
        linkedin_messages_opened = type_counts.get('linkedinOpened', 0)

        # Calculate engagement score
        engagement_score = 0
        for activity in activities:
            activity_type = activity.get('type', '')
            engagement_score += ENGAGEMENT_WEIGHTS.get(activity_type, 0)

        # Normalize score to 0-100 range (cap at 100)
        engagement_score = min(max(engagement_score, 0), 100)

        # Determine lead status based on engagement and bounces
        if emails_bounced > 0:
            lead_status = 'bounced'
        elif engagement_score >= 30:
            lead_status = 'high_engagement'
        elif engagement_score >= 15:
            lead_status = 'medium_engagement'
        elif engagement_score >= 5:
            lead_status = 'low_engagement'
        elif len(activities) <= 2:
            lead_status = 'new'
        else:
            lead_status = 'cold'

        # Get campaign name
        campaign = self.get_campaign(campaign_id)
        campaign_name = campaign['name'] if campaign else f"Campaign {campaign_id}"

        # Format dates for HubSpot (Unix timestamp in milliseconds at midnight UTC)
        def format_date_as_timestamp(dt: datetime) -> int:
            """Format date as Unix timestamp in milliseconds at midnight UTC for HubSpot"""
            # HubSpot expects dates as timestamps at midnight UTC
            # Convert to UTC if timezone-aware, otherwise assume UTC
            if dt.tzinfo is not None:
                dt_utc = dt.astimezone(timezone.utc)
            else:
                dt_utc = dt.replace(tzinfo=timezone.utc)
            # Create midnight UTC for the date
            midnight_utc = datetime(dt_utc.year, dt_utc.month, dt_utc.day, 0, 0, 0, tzinfo=timezone.utc)
            return int(midnight_utc.timestamp() * 1000)

        def format_datetime_as_timestamp(dt: datetime) -> int:
            """Format datetime as Unix timestamp in milliseconds for HubSpot"""
            return int(dt.timestamp() * 1000)

        # Find last email opened date
        last_email_opened_date = None
        for activity in sorted(activities, key=lambda a: a.get('created_at', ''), reverse=True):
            if activity.get('type') == 'emailsOpened':
                try:
                    last_email_opened_date = datetime.fromisoformat(
                        activity['created_at'].replace('Z', '+00:00')
                    )
                    break
                except (ValueError, AttributeError, KeyError):
                    pass

        return {
            # Core metrics
            'lemlist_total_activities': len(activities),
            'lemlist_first_activity_date': format_date_as_timestamp(first_date),
            'lemlist_last_activity_date': format_date_as_timestamp(last_date),
            'lemlist_days_in_campaign': days_in_campaign,
            'lemlist_current_campaign': campaign_name,

            # Email metrics
            'lemlist_emails_sent': emails_sent,
            'lemlist_emails_opened': emails_opened,
            'lemlist_emails_bounced': emails_bounced,
            'lemlist_emails_clicked': emails_clicked,
            'lemlist_emails_replied': emails_replied,
            'lemlist_email_open_rate': email_open_rate,
            'lemlist_last_email_opened_date': format_date_as_timestamp(last_email_opened_date) if last_email_opened_date else None,

            # LinkedIn metrics
            'lemlist_linkedin_visits': linkedin_visits,
            'lemlist_linkedin_invites_sent': linkedin_invites_sent,
            'lemlist_linkedin_invites_accepted': linkedin_invites_accepted,
            'lemlist_linkedin_messages_sent': linkedin_messages_sent,
            'lemlist_linkedin_messages_opened': linkedin_messages_opened,

            # Engagement
            'lemlist_engagement_score': engagement_score,
            'lemlist_lead_status': lead_status,
            'lemlist_last_sync_date': format_date_as_timestamp(datetime.now(timezone.utc)),  # HubSpot date fields require midnight UTC
        }

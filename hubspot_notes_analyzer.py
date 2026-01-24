"""
HubSpot Notes Analyzer for Lemlist activities.

Provides functionality to:
- Parse Lemlist notes from HubSpot to extract activity data
- Find duplicate notes across contacts
- Compare notes with local database activities
"""

import re
import logging
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class LemlistNoteParser:
    """Parse Lemlist notes from HubSpot to extract activity data.

    Note Format (from Lemlist native integration):
    ```
    LinkedIn invite sent from campaign Sitecore_Strapi_Kampagne_Marketing - (step 2)
    Text: Hallo Sebastian, ...
    ```

    Pattern: {Activity Type} from campaign {Campaign Name} - (step {N})
    Optional: Text: {Message Content}
    """

    # Map note text to our activity types (case-insensitive matching)
    ACTIVITY_TYPE_MAP = {
        'linkedin invite sent': 'linkedinInviteDone',
        'linkedin profile visited': 'linkedinVisitDone',
        'linkedin message sent': 'linkedinSent',
        'linkedin message opened': 'linkedinOpened',
        'linkedin invite accepted': 'linkedinInviteAccepted',
        'linkedin replied': 'linkedinReplied',
        'email sent': 'emailsSent',
        'email opened': 'emailsOpened',
        'email clicked': 'emailsClicked',
        'email replied': 'emailsReplied',
        'email bounced': 'emailsBounced',
        'email failed': 'emailsFailed',
        'call done': 'aircallDone',
        'call answered': 'aircallAnswered',
        'manual task done': 'manualDone',
        'interested': 'interested',
        'not interested': 'notInterested',
    }

    # Main regex pattern to extract activity info
    MAIN_PATTERN = r'^(.+?)\s+from\s+campaign\s+(.+?)\s*-\s*\(step\s+(\d+)\)'
    TEXT_PATTERN = r'Text:\s*(.+)'

    def parse_note(self, note_body: str) -> Optional[Dict]:
        """Extract activity data from note body.

        Args:
            note_body: The raw note text from HubSpot

        Returns:
            Dict with parsed data or None if not a Lemlist note:
            {
                'activity_text': 'LinkedIn invite sent',
                'activity_type': 'linkedinInviteDone',
                'campaign': 'Campaign Name',
                'step': 2,
                'message_text': 'Hallo...' (optional),
                'raw_body': '...'
            }
        """
        if not note_body:
            return None

        # Clean up the note body (remove HTML if present)
        clean_body = self._strip_html(note_body)

        # Parse main line
        match = re.match(self.MAIN_PATTERN, clean_body.strip(), re.IGNORECASE)
        if not match:
            return None  # Not a Lemlist note

        activity_text = match.group(1).strip()
        campaign = match.group(2).strip()
        step = int(match.group(3))

        # Map to our activity type (case-insensitive)
        activity_type = self.ACTIVITY_TYPE_MAP.get(
            activity_text.lower(),
            activity_text  # Keep original if not found
        )

        # Extract optional message text
        text_match = re.search(self.TEXT_PATTERN, clean_body, re.IGNORECASE | re.DOTALL)
        message_text = text_match.group(1).strip() if text_match else None

        return {
            'activity_text': activity_text,
            'activity_type': activity_type,
            'campaign': campaign,
            'step': step,
            'message_text': message_text,
            'raw_body': note_body
        }

    def _strip_html(self, text: str) -> str:
        """Remove HTML tags from text."""
        # Simple HTML tag removal
        clean = re.sub(r'<[^>]+>', '', text)
        # Decode common HTML entities
        clean = clean.replace('&nbsp;', ' ')
        clean = clean.replace('&amp;', '&')
        clean = clean.replace('&lt;', '<')
        clean = clean.replace('&gt;', '>')
        clean = clean.replace('&quot;', '"')
        # Normalize whitespace
        clean = re.sub(r'\s+', ' ', clean)
        return clean.strip()

    def is_lemlist_note(self, note_body: str) -> bool:
        """Check if a note is from Lemlist.

        Args:
            note_body: The raw note text

        Returns:
            True if the note matches Lemlist format
        """
        return self.parse_note(note_body) is not None


class NotesAnalyzer:
    """Analyze HubSpot notes and compare with DB activities."""

    def __init__(self, hubspot_client, db):
        """Initialize the analyzer.

        Args:
            hubspot_client: HubSpotClient instance
            db: LemlistDB instance
        """
        self.hubspot = hubspot_client
        self.db = db
        self.parser = LemlistNoteParser()

    def fetch_all_notes(self, campaign_id: str,
                        progress_callback=None) -> List[Dict]:
        """Fetch all notes for leads in a campaign.

        Args:
            campaign_id: Campaign ID to fetch notes for
            progress_callback: Optional callback(current, total) for progress

        Returns:
            List of note dicts with additional lead info
        """
        leads = self.db.get_all_leads_with_hubspot_ids(campaign_id)
        all_notes = []
        total = len(leads)

        logger.info(f"Fetching notes for {total} leads...")

        for i, lead in enumerate(leads):
            hubspot_id = lead['hubspot_id']

            try:
                notes = self.hubspot.get_notes_for_contact(hubspot_id)

                for note in notes:
                    # Add lead context to each note
                    note['lead_email'] = lead['email']
                    note['lead_first_name'] = lead.get('first_name', '')
                    note['lead_last_name'] = lead.get('last_name', '')
                    note['hubspot_contact_id'] = hubspot_id

                    # Parse the note
                    parsed = self.parser.parse_note(
                        note.get('properties', {}).get('hs_note_body', '')
                    )
                    note['parsed'] = parsed

                    all_notes.append(note)

            except Exception as e:
                logger.warning(f"Failed to fetch notes for {lead['email']}: {e}")

            if progress_callback:
                progress_callback(i + 1, total)

            # Small delay to avoid rate limits (not needed with proper rate limit handling,
            # but good to be safe)
            # time.sleep(0.1)

        logger.info(f"Fetched {len(all_notes)} total notes")
        return all_notes

    def find_duplicates(self, notes: List[Dict]) -> List[List[Dict]]:
        """Find duplicate notes (same content, same contact).

        Duplicates are identified by matching:
        - Contact ID
        - Activity type
        - Campaign name
        - Step number

        Args:
            notes: List of note dicts from fetch_all_notes()

        Returns:
            List of duplicate groups (each group = list of duplicate notes)
            Only groups with 2+ notes are returned.
        """
        # Group by (contact_id, activity_type, campaign, step)
        groups: Dict[Tuple, List[Dict]] = {}

        for note in notes:
            parsed = note.get('parsed')
            if not parsed:
                continue  # Skip non-Lemlist notes

            key = (
                note['hubspot_contact_id'],
                parsed['activity_type'],
                parsed['campaign'],
                parsed['step']
            )

            if key not in groups:
                groups[key] = []
            groups[key].append(note)

        # Return only groups with more than 1 note (duplicates)
        duplicates = [g for g in groups.values() if len(g) > 1]

        # Sort each group by creation date (newest first)
        for group in duplicates:
            group.sort(
                key=lambda n: n.get('properties', {}).get('hs_createdate', ''),
                reverse=True
            )

        logger.info(f"Found {len(duplicates)} duplicate groups")
        return duplicates

    def get_duplicate_stats(self, duplicates: List[List[Dict]]) -> Dict:
        """Calculate statistics about duplicates.

        Args:
            duplicates: List of duplicate groups from find_duplicates()

        Returns:
            Dict with statistics
        """
        total_notes = sum(len(g) for g in duplicates)
        total_to_delete = sum(len(g) - 1 for g in duplicates)  # Keep 1 per group

        # Count by activity type
        by_type: Dict[str, int] = {}
        for group in duplicates:
            activity_type = group[0].get('parsed', {}).get('activity_type', 'unknown')
            by_type[activity_type] = by_type.get(activity_type, 0) + (len(group) - 1)

        return {
            'total_duplicate_groups': len(duplicates),
            'total_duplicate_notes': total_notes,
            'total_to_delete': total_to_delete,
            'by_activity_type': by_type
        }

    def compare_with_db(self, notes: List[Dict], campaign_id: str) -> Dict:
        """Compare HubSpot notes with DB activities.

        Args:
            notes: List of note dicts from fetch_all_notes()
            campaign_id: Campaign ID for DB lookup

        Returns:
            Dict with comparison results:
            {
                'matched': [...],          # Matching (email, type, campaign)
                'in_notes_not_db': [...],  # Notes without DB activity
                'in_db_not_notes': [...],  # DB activities without note
                'stats': {...}             # Summary statistics
            }
        """
        db_activities = self.db.get_activities_by_campaign(campaign_id)

        # Build sets for comparison
        # Key: (email, activity_type, campaign_name)
        notes_set: Set[Tuple[str, str, str]] = set()
        notes_lookup: Dict[Tuple[str, str, str], Dict] = {}

        for note in notes:
            parsed = note.get('parsed')
            if not parsed:
                continue

            key = (
                note['lead_email'].lower(),
                parsed['activity_type'],
                parsed['campaign']
            )
            notes_set.add(key)
            notes_lookup[key] = note

        db_set: Set[Tuple[str, str, str]] = set()
        db_lookup: Dict[Tuple[str, str, str], Dict] = {}

        # Get campaign name for matching
        campaign = self.db.get_campaign(campaign_id)
        campaign_name = campaign['name'] if campaign else ''

        for activity in db_activities:
            key = (
                activity['lead_email'].lower(),
                activity['type'],
                campaign_name
            )
            db_set.add(key)
            db_lookup[key] = activity

        # Calculate differences
        matched = notes_set & db_set
        in_notes_not_db = notes_set - db_set
        in_db_not_notes = db_set - notes_set

        return {
            'matched': [notes_lookup.get(k) for k in matched if k in notes_lookup],
            'in_notes_not_db': [notes_lookup.get(k) for k in in_notes_not_db if k in notes_lookup],
            'in_db_not_notes': [db_lookup.get(k) for k in in_db_not_notes if k in db_lookup],
            'stats': {
                'total_notes': len(notes),
                'lemlist_notes': len(notes_set),
                'total_db_activities': len(db_activities),
                'matched_count': len(matched),
                'notes_only_count': len(in_notes_not_db),
                'db_only_count': len(in_db_not_notes)
            }
        }

    def delete_duplicates(self, duplicates: List[List[Dict]],
                          keep_newest: bool = True,
                          progress_callback=None) -> Dict:
        """Delete duplicate notes, keeping one per group.

        Args:
            duplicates: List of duplicate groups from find_duplicates()
            keep_newest: If True, keep the newest note; otherwise keep oldest
            progress_callback: Optional callback(current, total) for progress

        Returns:
            Dict with deletion results
        """
        total_to_delete = sum(len(g) - 1 for g in duplicates)
        deleted = 0
        failed = 0
        failed_ids = []

        logger.info(f"Deleting {total_to_delete} duplicate notes...")

        # Collect all note IDs to delete
        note_ids_to_delete = []
        for group in duplicates:
            # Sort by creation date
            sorted_group = sorted(
                group,
                key=lambda n: n.get('properties', {}).get('hs_createdate', ''),
                reverse=keep_newest
            )
            # Keep first (newest if keep_newest=True), delete rest
            for note in sorted_group[1:]:
                note_ids_to_delete.append(note['id'])

        # Delete in batches of 100
        batch_size = 100
        for i in range(0, len(note_ids_to_delete), batch_size):
            batch = note_ids_to_delete[i:i + batch_size]

            try:
                result = self.hubspot.batch_delete_notes(batch)
                deleted += result.get('deleted', len(batch))
            except Exception as e:
                logger.error(f"Failed to delete batch: {e}")
                failed += len(batch)
                failed_ids.extend(batch)

            if progress_callback:
                progress_callback(min(i + batch_size, total_to_delete), total_to_delete)

        logger.info(f"Deleted {deleted} notes, {failed} failed")

        return {
            'total_to_delete': total_to_delete,
            'deleted': deleted,
            'failed': failed,
            'failed_ids': failed_ids
        }

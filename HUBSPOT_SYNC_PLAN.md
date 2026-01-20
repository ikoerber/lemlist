# Implementation Plan: HubSpot Sync mit aggregierten Metriken

## Ziel
Lemlist Activity-Daten aus unserer SQLite DB als strukturierte Custom Properties in HubSpot syncen, um:
- Unstrukturierte Notizen zu ersetzen
- Filterbare, reportfähige Metriken zu erstellen
- Lead-Segmentierung zu ermöglichen

## User Requirements
- ✅ Aggregierte Metriken (keine einzelnen Timeline Events)
- ✅ Manueller Sync via Button (später automatisierbar)
- ⚠️ Welche Metriken genau → noch zu klären

## Vorgeschlagene HubSpot Custom Properties

### 1. Core Engagement Metrics
| Property Name | Type | Description | Berechnung |
|---------------|------|-------------|------------|
| `lemlist_total_activities` | Number | Gesamtzahl aller Activities | COUNT(*) |
| `lemlist_last_activity_date` | Date | Datum der letzten Activity | MAX(created_at) |
| `lemlist_first_activity_date` | Date | Datum der ersten Activity | MIN(created_at) |
| `lemlist_days_in_campaign` | Number | Tage seit erster Activity | TODAY - first_activity_date |
| `lemlist_current_campaign` | String | Name der aktuellen Campaign | Campaign Name |

### 2. Email Metrics
| Property Name | Type | Description | Berechnung |
|---------------|------|-------------|------------|
| `lemlist_emails_sent` | Number | Anzahl gesendete Emails | COUNT(type='emailsSent') |
| `lemlist_emails_opened` | Number | Anzahl geöffnete Emails | COUNT(type='emailsOpened') |
| `lemlist_emails_bounced` | Number | Anzahl bounced Emails | COUNT(type='emailsBounced') |
| `lemlist_email_open_rate` | Number | Email Open Rate (%) | (opened/sent) * 100 |
| `lemlist_last_email_opened_date` | Date | Letzter Email-Open | MAX(created_at WHERE type='emailsOpened') |

### 3. LinkedIn Metrics
| Property Name | Type | Description | Berechnung |
|---------------|------|-------------|------------|
| `lemlist_linkedin_visits` | Number | LinkedIn Profile Besuche | COUNT(type='linkedinVisitDone') |
| `lemlist_linkedin_invites_sent` | Number | LinkedIn Einladungen gesendet | COUNT(type='linkedinInviteDone') |
| `lemlist_linkedin_invites_accepted` | Number | LinkedIn Einladungen akzeptiert | COUNT(type='linkedinInviteAccepted') |
| `lemlist_linkedin_messages_sent` | Number | LinkedIn Nachrichten gesendet | COUNT(type='linkedinSent') |
| `lemlist_linkedin_messages_opened` | Number | LinkedIn Nachrichten geöffnet | COUNT(type='linkedinOpened') |

### 4. Engagement Score & Status
| Property Name | Type | Description | Berechnung |
|---------------|------|-------------|------------|
| `lemlist_engagement_score` | Number | Engagement Score (0-100) | Weighted sum (siehe unten) |
| `lemlist_lead_status` | Enum | Lead Status | Regel-basiert (siehe unten) |
| `lemlist_last_sync_date` | DateTime | Letzte Sync mit Lemlist | Timestamp des letzten Syncs |

**Engagement Score Berechnung:**
```
- Email Sent: +1
- Email Opened: +3
- Email Bounced: -5
- LinkedIn Visit: +2
- LinkedIn Invite Accepted: +5
- LinkedIn Message Opened: +4
- Out of Office: 0
- Skipped: -2
```

**Lead Status Logik:**
```
- 'bounced': emails_bounced > 0
- 'high_engagement': engagement_score >= 20
- 'medium_engagement': engagement_score >= 10
- 'low_engagement': engagement_score >= 5
- 'cold': engagement_score < 5
- 'new': total_activities <= 2
```

## Architektur

### 1. Neue Komponente: HubSpotClient (`hubspot_client.py`)

```python
class HubSpotClient:
    """Client für HubSpot API Interaktionen"""
    
    def __init__(self, api_token: str):
        self.base_url = "https://api.hubapi.com"
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        })
    
    def _make_request(self, method, endpoint, **kwargs):
        """Zentrale Request-Methode mit Error Handling"""
        # Rate Limit: 100 req/10sec (Standard), 
        # 150 req/10sec (Marketing Hub Pro+)
        # Retry bei 429 mit exponential backoff
        pass
    
    def update_contact_properties(self, hubspot_id: str, 
                                  properties: Dict[str, Any]):
        """Update properties für einen Contact
        
        PATCH /crm/v3/objects/contacts/{contactId}
        
        Body: {
            "properties": {
                "lemlist_total_activities": 15,
                "lemlist_engagement_score": 42,
                ...
            }
        }
        """
        pass
    
    def batch_update_contacts(self, updates: List[Dict]):
        """Batch update für bis zu 100 Contacts
        
        POST /crm/v3/objects/contacts/batch/update
        
        Body: {
            "inputs": [
                {
                    "id": "hubspot_id",
                    "properties": {...}
                }
            ]
        }
        """
        # Rate Limit: 4 requests/sec für Batch APIs
        pass
```

### 2. Neue DB-Methode: Metriken berechnen (`db.py`)

```python
def calculate_lead_metrics(self, email: str, 
                           campaign_id: str) -> Dict[str, Any]:
    """Berechnet aggregierte Metriken für einen Lead
    
    Returns: Dict mit allen HubSpot Property Values
    """
    activities = self.get_activities_by_lead(email)
    
    if not activities:
        return None
    
    # Dates
    dates = [a['created_at'] for a in activities]
    first_date = min(dates)
    last_date = max(dates)
    
    # Counts by type
    email_sent = len([a for a in activities 
                     if a['type'] == 'emailsSent'])
    email_opened = len([a for a in activities 
                       if a['type'] == 'emailsOpened'])
    email_bounced = len([a for a in activities 
                        if a['type'] == 'emailsBounced'])
    
    # LinkedIn
    linkedin_visits = len([a for a in activities 
                          if a['type'] == 'linkedinVisitDone'])
    linkedin_invites_sent = len([a for a in activities 
                                if a['type'] == 'linkedinInviteDone'])
    linkedin_invites_accepted = len([a for a in activities 
                                    if a['type'] == 'linkedinInviteAccepted'])
    
    # Engagement Score
    score = 0
    for activity in activities:
        score += ENGAGEMENT_WEIGHTS.get(activity['type'], 0)
    
    # Status
    if email_bounced > 0:
        status = 'bounced'
    elif score >= 20:
        status = 'high_engagement'
    elif score >= 10:
        status = 'medium_engagement'
    # ...
    
    return {
        'lemlist_total_activities': len(activities),
        'lemlist_first_activity_date': first_date,
        'lemlist_last_activity_date': last_date,
        'lemlist_emails_sent': email_sent,
        'lemlist_emails_opened': email_opened,
        'lemlist_email_open_rate': round((email_opened/email_sent)*100, 1) 
                                   if email_sent > 0 else 0,
        'lemlist_engagement_score': score,
        'lemlist_lead_status': status,
        'lemlist_current_campaign': self.get_campaign(campaign_id)['name'],
        'lemlist_last_sync_date': datetime.now().isoformat(),
        # ... alle anderen Properties
    }

def get_all_leads_with_hubspot_ids(self, campaign_id: str) -> List[Dict]:
    """Holt alle Leads mit HubSpot IDs für Batch-Sync"""
    with self.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT email, hubspot_id, first_name, last_name
            FROM leads
            WHERE campaign_id = ?
            AND hubspot_id IS NOT NULL
            AND hubspot_id != ''
        """, (campaign_id,))
        return [dict(row) for row in cursor.fetchall()]
```

### 3. Neue Core Function: Sync zu HubSpot (`app.py`)

```python
def sync_to_hubspot(api_key: str, campaign_id: str, 
                    hubspot_token: str, 
                    batch_size: int = 50) -> Dict[str, int]:
    """Synct Lead-Metriken zu HubSpot
    
    Returns: Statistics (processed, success, failed)
    """
    db = LemlistDB()
    hubspot = HubSpotClient(hubspot_token)
    
    # Get all leads with HubSpot IDs
    leads = db.get_all_leads_with_hubspot_ids(campaign_id)
    
    if not leads:
        return {'processed': 0, 'success': 0, 'failed': 0}
    
    processed = 0
    success = 0
    failed = 0
    
    # Process in batches of batch_size
    for i in range(0, len(leads), batch_size):
        batch = leads[i:i + batch_size]
        
        # Calculate metrics for each lead
        updates = []
        for lead in batch:
            metrics = db.calculate_lead_metrics(
                lead['email'], 
                campaign_id
            )
            
            if metrics:
                updates.append({
                    'id': lead['hubspot_id'],
                    'properties': metrics
                })
        
        # Batch update to HubSpot
        try:
            hubspot.batch_update_contacts(updates)
            success += len(updates)
        except Exception as e:
            st.error(f"Batch {i//batch_size + 1} failed: {e}")
            failed += len(updates)
        
        processed += len(updates)
        
        # Rate limit: 4 req/sec für Batch API
        time.sleep(0.25)
    
    return {
        'processed': processed,
        'success': success,
        'failed': failed
    }
```

### 4. UI Integration (`app.py`)

**Sidebar: HubSpot Sync Section**
```python
st.divider()
st.header("🔄 HubSpot Sync")

hubspot_token = st.text_input(
    "HubSpot API Token",
    type="password",
    help="Private App Token aus HubSpot Settings"
)

if st.button("⬆️ Nach HubSpot syncen", 
            disabled=not (campaign_id and hubspot_token)):
    with st.spinner("Syncronisiere Metriken zu HubSpot..."):
        result = sync_to_hubspot(
            api_key, 
            campaign_id, 
            hubspot_token,
            batch_size=50
        )
        
        st.success(
            f"✅ {result['success']} von {result['processed']} "
            f"Leads erfolgreich gesynct"
        )
        
        if result['failed'] > 0:
            st.warning(f"⚠️ {result['failed']} Leads fehlgeschlagen")
```

**Optional: Sync-Status anzeigen**
```python
# Show which leads will be synced
if campaign_in_db:
    leads_with_hubspot = db.get_all_leads_with_hubspot_ids(campaign_id)
    st.info(
        f"ℹ️ {len(leads_with_hubspot)} Leads bereit für HubSpot Sync"
    )
```

## Implementation Steps

### Phase 1: Setup & Preparation
1. **HubSpot Private App erstellen**
   - HubSpot Settings → Integrations → Private Apps
   - Permissions: `crm.objects.contacts.write`
   - Token kopieren

2. **Custom Properties in HubSpot anlegen**
   - HubSpot Settings → Properties → Contact Properties
   - Alle Properties aus Tabelle oben manuell anlegen
   - Field Types: Number, Date, Enum korrekt setzen
   - **Alternative**: Via API automatisch erstellen (später)

### Phase 2: Core Implementation
3. **Neue Datei: `hubspot_client.py`**
   - HubSpotClient Klasse implementieren
   - Error Handling & Rate Limiting
   - Batch Update Methode

4. **DB erweitern: `db.py`**
   - `calculate_lead_metrics()` Methode
   - `get_all_leads_with_hubspot_ids()` Methode
   - Engagement Score Weights als Constants

5. **App erweitern: `app.py`**
   - `sync_to_hubspot()` Funktion
   - UI Section für HubSpot Token & Sync Button
   - Progress-Anzeige mit Statistiken

6. **Dependencies: `requirements.txt`**
   - Keine neuen Dependencies nötig (requests bereits vorhanden)

### Phase 3: Testing
7. **Unit Tests**
   - Test `calculate_lead_metrics()` mit Sample-Daten
   - Mock HubSpot API für Tests

8. **Integration Test**
   - Mit 10 Test-Contacts in HubSpot
   - Verify Properties wurden korrekt gesetzt
   - Check Rate Limiting

9. **Full Sync Test**
   - Mit allen ~250 Leads
   - Monitor für Fehler
   - Verify in HubSpot

### Phase 4: Documentation
10. **README.md Update**
    - HubSpot Sync Feature dokumentieren
    - Setup Instructions (Private App, Properties)
    - Rate Limits & Batch Size

11. **CLAUDE.md Update**
    - HubSpot API Integration
    - Custom Properties Struktur
    - Sync Logic Flow

## Error Handling

### Rate Limits
**HubSpot Standard Limits:**
- 100 requests/10 seconds (Standard)
- 150 requests/10 seconds (Marketing Hub Pro+)
- 4 requests/second für Batch APIs

**Implementierung:**
```python
def _make_request(self, ...):
    max_retries = 3
    for attempt in range(max_retries):
        response = self.session.request(...)
        
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 10))
            st.warning(f"Rate limit reached. Retry in {retry_after}s...")
            time.sleep(retry_after)
            continue
        
        response.raise_for_status()
        return response
```

### Contact Not Found
- Log warning für fehlende HubSpot IDs
- Continue mit nächstem Lead
- Zeige Statistik am Ende

### Invalid Properties
- Validate vor dem Sync (z.B. Dates im richtigen Format)
- Skip invalid Properties statt ganzen Contact

## Security & Best Practices

1. **Token Storage**
   - HubSpot Token via UI eingeben (password input)
   - Optional: In `.env` speichern
   - **NIEMALS** in Git committen

2. **Batch Size**
   - Default: 50 Contacts pro Batch
   - Max: 100 (HubSpot Limit)
   - Bei Errors: Retry mit kleinerer Batch Size

3. **Idempotenz**
   - Properties überschreiben (nicht addieren)
   - Keine Duplikate durch mehrfache Syncs

4. **Logging**
   - Log jeden Batch-Sync
   - Errors mit Details loggen
   - Success-Rate tracken

## Future Enhancements

### Automatischer Sync
```python
# Cron Job oder Cloud Function (später)
def auto_sync():
    campaigns = db.get_all_campaigns()
    for campaign in campaigns:
        sync_to_hubspot(...)
```

### Selective Sync
```python
# Nur Leads syncen die Updates haben
def sync_changed_leads_only():
    # Track last_synced_at in DB
    # Compare mit last_updated
    pass
```

### Property Creation via API
```python
def create_custom_properties():
    """Erstellt alle Properties automatisch via API"""
    # POST /crm/v3/properties/contacts
    pass
```

## Open Questions für User

1. **Metriken-Auswahl**: Welche der vorgeschlagenen Properties sind wichtig?
   - Core Metrics (total, dates) → Alle?
   - Email Metrics → Alle?
   - LinkedIn Metrics → Alle?
   - Engagement Score → Ja/Nein?

2. **Engagement Score Weights**: Passen die Gewichtungen?
   - Email Opened: +3 Punkte
   - LinkedIn Invite Accepted: +5 Punkte
   - Etc.

3. **Lead Status Thresholds**: Passen die Grenzen?
   - High Engagement: >= 20 Punkte
   - Medium: >= 10 Punkte
   - Low: >= 5 Punkte

4. **Batch Size**: 50 Contacts pro Batch ok? (kann 10-100 sein)

5. **Error Handling**: Bei Fehlern abbrechen oder weitermachen?

## Critical Files

- **NEW**: `hubspot_client.py` - HubSpot API Client
- **MODIFY**: `db.py` - Add metrics calculation methods
- **MODIFY**: `app.py` - Add sync function and UI
- **MODIFY**: `requirements.txt` - (keine neuen Dependencies)
- **MODIFY**: `.env.example` - Add HUBSPOT_API_TOKEN
- **MODIFY**: `README.md` - Document HubSpot integration
- **MODIFY**: `CLAUDE.md` - Document HubSpot architecture

## Risks & Mitigation

| Risk | Impact | Mitigation |
|------|--------|-----------|
| HubSpot Rate Limit exceeded | Sync fails | Batch processing + delays |
| Invalid HubSpot IDs | Some contacts fail | Skip & log errors |
| Property name conflicts | Properties not found | Validate names before sync |
| Token expiration | Auth fails | Clear error message + refresh |
| Large dataset (1000+ leads) | Long sync time | Progress bar + batch status |

## Success Criteria

✅ Custom Properties in HubSpot angelegt
✅ Metriken werden korrekt berechnet
✅ Batch-Sync funktioniert ohne Rate Limit Errors
✅ UI Button für manuellen Sync
✅ Error Handling für fehlgeschlagene Contacts
✅ Statistiken nach Sync angezeigt
✅ Dokumentation aktualisiert

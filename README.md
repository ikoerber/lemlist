# Lemlist Campaign Data Extractor

Eine robuste Streamlit-App zum Extrahieren und Analysieren von Leads und Activities aus der Lemlist API.

## Features

- **Campaign Dropdown**: Wähle Campaign aus Liste statt manuelle ID-Eingabe
- **Status Filter**: Zeige nur aktive, Draft oder pausierte Campaigns
- **SQLite Datenbank**: Lokales Caching aller Daten für schnellen Zugriff
- **Incremental Updates**: Lädt nur neue Activities seit letztem Sync
- **HubSpot Integration**: Automatisches Fetching von HubSpot IDs mit klickbaren Links
- **HubSpot Sync**: Engagement-Metriken zu HubSpot Custom Properties synchronisieren
- **HubSpot Notes Analyse**: Lemlist Notes in HubSpot analysieren und Duplikate bereinigen
- **LinkedIn URLs**: Klickbare LinkedIn Profile direkt in der Tabelle
- **Lead-spezifische Ansicht**: Wähle einen einzelnen Lead um dessen Activity Timeline zu sehen
- **Optimierte Performance**: Nutzt `campaignId` Filter für 50-100x schnellere Datenextraktion
- **Activity Filtering**: Filtert automatisch irrelevante Activities (`hasEmailAddress`, `conditionChosen`)
- **Duplicate Detection**: Dedupliziert `emailsOpened` (behebt Tracking-Pixel Mehrfach-Loads)
- **Rate Limit Handling**: Automatisches Retry mit exponential backoff
- **Pagination Support**: Holt automatisch alle Leads und Activities (keine Limitierung)
- **User-Friendly UI**: Intuitive Streamlit-Oberfläche mit Progress-Anzeigen
- **CSV Export**: Zeitgestempelte Exports für einfache Weiterverarbeitung
- **Dual Filter**: Lead-Filter + Activity Type Filter kombinierbar
- **.env Support**: Speichere API Key lokal für schnellen Zugriff

## Installation

### Voraussetzungen

- Python 3.8 oder höher
- Lemlist Account mit API Zugriff

### Setup

1. Repository klonen oder Dateien herunterladen

2. Virtual Environment erstellen (empfohlen):
```bash
python -m venv venv
source venv/bin/activate  # Auf Windows: venv\Scripts\activate
```

3. Dependencies installieren:
```bash
pip3 install -r requirements.txt
```

4. **(Optional) .env Datei erstellen** für automatisches Laden der Credentials:
```bash
cp .env.example .env
```

Dann bearbeite die `.env` Datei und füge deine Werte ein:
```bash
LEMLIST_API_KEY=dein_api_key_hier
CAMPAIGN_ID=deine_campaign_id_hier
```

**Vorteile**:
- Keine Eingabe bei jedem Start nötig
- Credentials werden nicht in der History gespeichert
- Praktisch für tägliche/häufige Nutzung

## Verwendung

### Schnellstart (empfohlen)

1. Starte die App: `streamlit run app.py`
2. Die App öffnet sich automatisch im Browser unter `http://localhost:8501`
3. Gib deinen API Key ein (oder speichere ihn in `.env`)
4. **NEU**: Wähle deine Campaign aus dem Dropdown
   - Automatisch alle Campaigns geladen
   - Filtere nach Status (running, draft, paused, etc.)
   - Kein Copy-Paste der Campaign ID nötig!
5. Klicke auf "Daten laden"

### Alternative: Mit .env Datei

1. Erstelle `.env` Datei wie oben beschrieben
2. Starte die App: `streamlit run app.py`
3. API Key wird automatisch geladen
4. Campaign kann aus Dropdown gewählt werden (oder aus .env vorausgewählt)

### API Key und Campaign ID finden

**API Key holen**:
- Gehe zu Lemlist → Settings → API
- Kopiere deinen API Key
- **Hinweis**: Es wird NUR der API Key benötigt (keine User ID oder Email)

**Campaign auswählen**:
- **Automatisch (empfohlen)**: Aktiviere "Campaign aus Liste wählen" in der App
  - Alle Campaigns werden geladen und im Dropdown angezeigt
  - Optional: Filtere nach Status (nur aktive Campaigns zeigen)
  - Wähle Campaign mit einem Klick
- **Manuell**: Deaktiviere Checkbox und gib Campaign ID direkt ein
  - Die Campaign ID steht in der Lemlist URL: `https://app.lemlist.com/campaigns/{CAMPAIGN_ID}/...`

### Daten exportieren

1. Klicke auf "Daten laden" (typisch 5-10 Sekunden für 500 Leads)
2. Nutze optional den Activity Type Filter
3. Klicke auf "Download CSV"

### Output Format

Die App erstellt eine "Flat Table" mit folgenden Spalten:

| Spalte | Beschreibung |
|--------|--------------|
| Lead Email | Email-Adresse des Leads |
| Lead FirstName | Vorname des Leads |
| Lead LastName | Nachname des Leads |
| Activity Type | Art der Aktivität (z.B. emailOpened, linkedinVisit) |
| Activity Date | Datum und Uhrzeit (Format: YYYY-MM-DD HH:MM) |
| Details | Zusätzliche Details (Subject, URL, Payload) |
| HubSpot Link | Klickbarer Link zum HubSpot Kontakt (falls verfügbar) |
| LinkedIn Link | Klickbarer Link zum LinkedIn Profil (falls verfügbar) |

## API Limits und Performance

### Rate Limits
- **Lemlist API**: 20 Requests pro 2 Sekunden
- Die App respektiert diese Limits automatisch mit:
  - Monitoring der `X-RateLimit-Remaining` Header
  - Auto-Retry bei 429 Errors
  - Exponential Backoff bei Netzwerkfehlern

### Typische Performance

| Kampagnengröße | API Calls | Ladezeit | Cache Hit |
|----------------|-----------|----------|-----------|
| 100 Leads | 2-4 | ~2 Sek | < 1 Sek |
| 500 Leads | 5-10 | ~5 Sek | < 1 Sek |
| 1000 Leads | 10-20 | ~10 Sek | < 1 Sek |

**Performance-Vorteil**: Statt 1000+ einzelner API Calls (alter Ansatz) macht die App nur 10-20 Calls durch Nutzung des `campaignId` Filters.

## Architektur

### LemlistClient (`app.py`)
- Zentraler API Client mit Session-Management
- Rate Limit Handling und Retry Logic
- Automatische Pagination für Activities und Campaigns
- `get_all_activities()`: Holt alle Activities mit `campaignId` Filter
- `get_lead_details()`: Holt HubSpot IDs für einzelne Leads
- `get_all_campaigns()`: Lädt Campaign-Liste für Dropdown

### LemlistDB (`db.py`)
- SQLite Datenbank-Layer für lokales Caching
- Tabellen: `campaigns`, `leads`, `activities`
- `upsert_*()` Methoden für idempotente Speicherung
- `get_activities_by_campaign()`: LEFT JOIN von Activities mit Lead-Daten
- Incremental Updates durch Timestamp-Tracking

### Data Flow
1. **First Load**: Activities von API → Leads extrahieren → HubSpot IDs fetchen (erste 50) → DB speichern
2. **Incremental Update**: Neue Activities seit letztem Sync → Neue Leads extrahieren → HubSpot IDs fetchen → DB updaten
3. **Display**: Daten aus DB laden (JOIN) → Streamlit DataFrame → Filter anwenden → Anzeigen/Exportieren

### Buttons & Actions
- **🔄 Aktivitäten aktualisieren**: Lädt nur neue Activities (schnell)
- **🔁 Vollständig neu laden**: Löscht DB und lädt alles neu (bei Problemen)
- **⬇️ Alle Lead Details laden**: Fetcht HubSpot IDs für ALLE Leads automatisch (mit Progress Bar, Pause alle 50 Leads)
- **⬆️ Nach HubSpot syncen**: Synchronisiert Engagement-Metriken zu HubSpot Custom Properties
- **📥 Notes von HubSpot laden**: Lädt Lemlist Notes für Analyse und Duplikat-Erkennung
- **🗑️ Datenbank leeren**: Löscht alle Campaign-Daten aus DB

## Fehlerbehandlung

Die App behandelt folgende Error-Szenarien:

- **401 Unauthorized**: Ungültiger API Key
- **404 Not Found**: Campaign ID existiert nicht
- **429 Too Many Requests**: Rate Limit überschritten (Auto-Retry)
- **Timeout**: Netzwerk-Timeouts (Auto-Retry mit Backoff)
- **Leere Kampagnen**: Warnung wenn keine Activities gefunden

## Troubleshooting

### "Ungültiger API Key"
- Überprüfe deinen API Key in den Lemlist Settings
- Stelle sicher, dass keine Leerzeichen kopiert wurden
- Es wird NUR der API Key benötigt - keine User ID, Email oder andere Credentials

### "Campaign ID nicht gefunden"
- Überprüfe die Campaign ID in der URL
- Stelle sicher, dass die Kampagne existiert

### "Rate Limit erreicht"
- Die App retried automatisch nach der angegebenen Zeit
- Bei wiederholten Problemen: Warte 2 Minuten und versuche es erneut

### App lädt langsam
- Erste Ladung ist normal (API Calls nötig)
- Zweite Ladung sollte < 1 Sekunde sein (Cache)
- Lösche Cache wenn Daten aktualisiert werden sollen

## Development

### Projektstruktur

```
lemlist/
├── app.py                    # Hauptapplikation (Streamlit UI, LemlistClient)
├── db.py                     # Datenbank-Layer (LemlistDB, SQLite)
├── hubspot_client.py         # HubSpot API Client (Kontakte & Notes)
├── hubspot_notes_analyzer.py # HubSpot Notes Parser & Duplikat-Erkennung
├── requirements.txt          # Python Dependencies
├── .env.example              # Template für API Config
├── .gitignore               # Git ignore rules
├── CLAUDE.md                # Claude Code Instruktionen
└── README.md                # Diese Datei
```

### Testing

Manuelle Tests:
```bash
# Mit kleiner Testkampagne starten
streamlit run app.py

# Verschiedene Szenarien testen:
# - Valide Daten
# - Invalider API Key
# - Invalide Campaign ID
# - Cache-Funktionalität
```

## HubSpot Integration

### HubSpot Sync (Engagement Metriken)

Die App kann aggregierte Engagement-Metriken aus der lokalen SQLite Datenbank zu HubSpot Custom Properties synchronisieren.

**Setup**:
1. HubSpot Private App erstellen: Settings → Integrations → Private Apps
2. Scope: `crm.objects.contacts.write`
3. Access Token in `.env` als `HUBSPOT_API_TOKEN` speichern

**Synchronisierte Metriken**:
- `lemlist_total_activities`: Gesamtanzahl Activities
- `lemlist_engagement_score`: Berechneter Engagement Score (0-100)
- `lemlist_lead_status`: Lead Status (new, cold, low/medium/high_engagement, bounced)
- Und viele weitere (siehe CLAUDE.md für vollständige Liste)

### HubSpot Notes Analyse

Analysiert Lemlist Notes in HubSpot und findet/entfernt Duplikate:
- **Notes laden**: Holt alle Lemlist-Notes für Leads in der Campaign
- **Duplikat-Erkennung**: Findet Notes mit gleichem (Kontakt, Activity Type, Campaign, Step)
- **Duplikate löschen**: Löscht Duplikate automatisch (behält neueste Note)
- **DB-Vergleich**: Vergleicht Notes mit lokaler Datenbank für Vollständigkeitsprüfung

## Activity Filtering & Deduplication

### Gefilterte Activity Types
Die folgenden Activity Types werden automatisch herausgefiltert (nicht nützlich für Analyse):
- `hasEmailAddress`: Internes Lemlist-Event
- `conditionChosen`: Workflow-Condition Event

### emailsOpened Deduplication
`emailsOpened` Activities werden dedupliziert basierend auf:
- `leadEmail`
- `emailTemplateId`
- `sequenceStep`

Dies behebt das Problem, dass Email-Clients den Tracking-Pixel mehrfach laden (z.B. bei Preview-Pane).

## Zukünftige Erweiterungen

- **Webhook Integration**: Real-time Activity Updates statt Polling
- **Multi-Campaign Support**: Mehrere Kampagnen gleichzeitig analysieren
- **Data Visualization**: Charts für Activity Types und Zeitverläufe
- **Export Formats**: Excel und JSON zusätzlich zu CSV

## Lizenz

Dieses Projekt ist für den persönlichen Gebrauch entwickelt.

## Support

Bei Fragen oder Problemen:
1. Überprüfe die [Lemlist API Dokumentation](https://developer.lemlist.com/)
2. Checke die Error Messages in der App
3. Nutze die Debug-Details im Expander bei Fehlern

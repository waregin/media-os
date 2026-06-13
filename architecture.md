# WatchSort, MusicSort & PodcastSync — Architecture Document

**Version:** 0.2  
**Status:** Pre-implementation  
**Platform:** Zorin OS, self-hosted

---

## Changelog

| Version | Changes |
|---------|---------|
| 0.1 | Initial document |
| 0.2 | Listen view now identical to Watch view (video visible). Watch Later remains a permanent dumping ground; recurring sync replaces one-time run. Videos move to Watch Queue / Listen Queue on processing; "Done" removes from those managed playlists. Bulk done controls with select all/deselect all added. Channel classification gains independent "archive to reference RAG" toggle. Trust/reliability score added to channel and source metadata. Summarize implies archive; archive alone does not imply summarize. Summarize/digest page designed for multiple source types. PodcastSync added as a third tool: automates Spotify podcast playlist in oldest-first order, auto-removes played episodes, optionally archives transcripts to reference RAG. Reference RAG defined as future scope; groundwork laid in schema and metadata. |

---

## Table of Contents

1. [Overview](#overview)
2. [Shared Infrastructure](#shared-infrastructure)
3. [Tool 1: WatchSort](#tool-1-watchsort)
4. [Tool 2: MusicSort](#tool-2-musicsort)
5. [Tool 3: PodcastSync](#tool-3-podcastsync)
6. [Reference RAG Groundwork](#reference-rag-groundwork)
7. [API Quota Strategy](#api-quota-strategy)
8. [Database Schema](#database-schema)
9. [Build Order](#build-order)
10. [Open Questions](#open-questions)

---

## Overview

Three self-hosted web tools sharing a common FastAPI backend and SQLite database.

**WatchSort** automates the management of a YouTube Watch Later dumping ground — categorizing
videos by channel, routing them into Watch Queue and Listen Queue playlists, providing a
catch-up view, generating RAG-based digests for "summarize" channels, and polling subscriptions
for new content.

**MusicSort** manages a YouTube Music uploaded-only library — syncing local MP3s, merging
soundtrack tracks into single files for the combined playlist, normalizing volume, removing
silence, and detecting unauthorized playlist changes made by YouTube Music automatically.

**PodcastSync** automates a Spotify podcast playlist — fetching new episodes from followed
shows, inserting them in oldest-first order using URI-anchored positioning, and auto-removing
fully-played episodes after a configurable delay. Interim solution until a unified playback
app exists; designed to be replaced or extended later without rework.

All three tools share infrastructure for transcript ingestion and the reference RAG pipeline,
whose full design is deferred but whose groundwork is laid now.

---

## Shared Infrastructure

### Stack

| Component      | Choice          | Rationale                                                   |
|----------------|-----------------|-------------------------------------------------------------|
| Backend        | FastAPI         | Async, lightweight, native Python ecosystem                 |
| Database       | SQLite/SQLModel  | Zero-ops, trivially backed up, sufficient for this load     |
| Frontend       | HTMX + Jinja2   | Server-rendered, minimal JS, clean browser experience       |
| Scheduler      | APScheduler     | Embedded in FastAPI process, no separate service            |
| Vector store   | ChromaDB        | Per-source collections, metadata filtering, local           |
| Transcription  | yt-dlp + whisper.cpp | Caption fetch with audio fallback                      |
| Auth (Google)  | Google OAuth2   | YouTube Data API and ytmusicapi                             |
| Auth (Spotify) | Spotify OAuth2  | PodcastSync; separate flow and token file                   |

### Authentication

**Google OAuth2** covers WatchSort and MusicSort. Scopes required:

- `youtube.force-ssl` — Watch Later read/delete, playlist management, subscriptions
- `youtube` — general YouTube Data API access

ytmusicapi uses its own `setup_oauth` flow but shares the same Google Cloud project and
credentials. Tokens stored as JSON files on disk, refreshed automatically.

**Spotify OAuth2** covers PodcastSync. Scopes required:

- `playlist-read-private` — read existing podcast playlist
- `playlist-modify-private` — add/remove/reorder episodes
- `user-library-read` — read followed shows
- `user-read-playback-position` — check fully-played status for auto-removal

Both auth systems are independent. Losing one does not affect the other.

### Running the App

```bash
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Access at `http://localhost:8080`. No reverse proxy required for local use; add nginx if
exposing to a home network.

---

## Tool 1: WatchSort

### Concept

Watch Later remains a permanent personal dumping ground — the user adds videos manually as
before, at any time. WatchSort processes that list on a recurring schedule (and on demand),
categorizes videos by channel, moves them into managed playlists, and provides a structured
catch-up interface. The tool also polls subscribed channels for new uploads and routes them
automatically without Watch Later involvement.

### Playlist Architecture

```
YouTube (actual playlists)
├── Watch Later (WL)              ← user dumps videos here as before; always a dumping ground
├── WatchSort: Watch Queue        ← managed by tool; videos to watch
└── WatchSort: Listen Queue       ← managed by tool; videos to listen to
```

"Summarize" and "Archive" channel videos are pulled into the local DB and transcript pipeline
only — no separate YouTube playlist is created for them.

Unavailable/deleted videos are detected during sync and removed from all managed playlists
and the DB without user action.

### Channel Classification

Each channel has two independent attributes:

**Primary classification** (how you consume it):
- `watch` — full video experience
- `listen` — audio-focus, but video is still visible (identical player to Watch)
- `summarize` — digest only; no individual viewing queue entry generated
- `unclassified` — newly seen channel, pending your decision

**Archive flag** (whether transcripts feed the reference RAG):
- `true` — transcript is fetched and stored in ChromaDB for future reference RAG use
- `false` — no transcript work done (default)

Rules:
- `summarize` always implies `archive = true`. You cannot summarize without archiving.
- `watch` or `listen` with `archive = true` means the video appears in the viewing queue
  AND its transcript is archived. No digest is generated.
- `watch` or `listen` with `archive = false` is the simple case — watch/listen and done.

### Feature Breakdown

#### Feature 1 — Watch Later Sync (recurring)

Runs on a schedule and on demand. This is not a one-time migration — it runs indefinitely
every time you add new videos to Watch Later.

1. Fetch all items from the Watch Later playlist (`playlistItems.list`, 1 unit/page).
2. For each video, check against the local DB:
   - **Already known and routed:** skip entirely.
   - **New, channel is classified:** route immediately (see below).
   - **New, channel is unclassified:** add to "pending classification" queue in DB.
   - **Unavailable (tombstone):** mark deleted in DB; remove from Watch Later and any
     managed playlists (`playlistItems.delete`, 50 units each — batch carefully).
3. Routing for newly classified videos:
   - `watch` or `listen`: remove from Watch Later, insert into Watch Queue or Listen Queue.
   - `summarize`: remove from Watch Later, queue transcript fetch; no YouTube playlist entry.
   - `watch`/`listen` with `archive = true`: route to viewing queue AND queue transcript fetch.
4. Update DB status for all processed videos.

**Note:** Watch Later (ID = `WL`) supports read and delete via the API, but not insert.
User addition to Watch Later remains manual, as intended.

#### Feature 2 — Channel Classifier

A settings page listing all channels that have appeared in Watch Later or subscriptions,
grouped by classification status. For each channel:

- Channel name, thumbnail, subscriber count
- Sample video titles (to jog memory)
- Primary classification selector (Watch / Listen / Summarize / Unclassified)
- Archive toggle (checkbox), auto-checked and locked when Summarize is selected
- Trust/reliability score (see Reference RAG Groundwork)

Bulk actions: classify multiple unclassified channels at once with the same settings.
Reclassifying an existing channel re-routes any queued videos that haven't been consumed yet.

The classifier page also shows new subscriptions detected since last visit, so channels can
be pre-classified before any of their videos appear in Watch Later.

#### Feature 3 — Catch-Up View

Three sections (tabs or stacked panels — TBD during implementation):

**Watch section**
- Lists videos from the Watch Queue playlist, ordered by date added
- Each entry: thumbnail, title, channel, duration, date added
- Clicking a video opens it on YouTube (or embeds — see Open Questions)
- Checkbox per video for bulk selection
- Select all / Deselect all controls
- "Done with checked" — removes selected videos from Watch Queue playlist and marks done in DB
- "Keep checked, done with rest" — removes all unchecked videos instead

**Listen section**
- Identical layout, controls, and YouTube player to Watch section
- Video is still visible — user can glance at it freely
- Source playlist: Listen Queue

**Summarize/Digest section**
- Designed to accept multiple source types (YouTube channels today; podcasts, RSS, or other
  sources potentially in future)
- One card per source, showing:
  - Source name and thumbnail
  - Source type badge (e.g. "YouTube channel")
  - Date of last "mark read"
  - Rolling digest synthesized from all new content since last mark-read
  - Collapsible list of source items included in the current digest
- "Mark read" button: stores timestamp; next digest window starts from here
- Digest auto-updates eagerly in the background when new transcripts arrive

#### Feature 4 — Subscription Watcher

APScheduler job, frequency configurable (default: every 12 hours). For each subscribed
channel:

1. Check `next_poll_due` in DB against current time.
2. If due (based on priority tier — see API Quota Strategy), fetch the channel's uploads
   playlist (`playlistItems.list` with `publishedAfter = last_polled`).
3. For any new videos, apply the channel's classification and route accordingly.
4. Update `last_polled`, `next_poll_due`, and quota log in DB.

New subscriptions are detected by comparing a fresh `subscriptions.list` fetch against the
`channels` table. Newly detected channels are added as `unclassified` and surfaced in the
classifier UI.

#### Transcript and Archive Pipeline

Triggered for any video whose channel has `archive = true` (which includes all `summarize`
channels):

```
Video identified for archiving
    │
    ▼
Transcript fetch
    ├── yt-dlp: attempt to fetch existing captions (fast, free)
    └── whisper.cpp fallback: download audio and transcribe if no captions exist
        (runs as background job with status indicator)
    │
    ▼
Chunking
    └── Overlapping chunks (~500 tokens, 50-token overlap)
        Metadata tagged: source_id, source_type='youtube_channel', channel_id,
                         publish_date, chunk_index, trust_score (from channel)
    │
    ▼
ChromaDB storage
    └── Collection: 'reference_rag' (shared across all archivable sources)
        Metadata enables filtering by source, date, type, trust score
    │
    ▼
If channel classification is 'summarize':
    └── Digest regeneration queued (see Digest Generation below)

If classification is 'watch' or 'listen':
    └── Transcript stored; no digest generated
```

#### Digest Generation

Only applies to `summarize` channels. Triggered eagerly when new transcripts are stored.

```
New transcript stored for a 'summarize' channel
    │
    ▼
Query ChromaDB for all chunks:
    - source_id = this channel
    - publish_date > last_mark_read for this channel
    │
    ▼
Deduplication pass
    └── Filter chunks with cosine similarity > 0.92 to already-included chunks
    │
    ▼
Claude API synthesis
    System: "You are synthesizing a briefing from multiple episodes of [channel name].
             Combine the key ideas across episodes, eliminating repetition.
             Present as a concise briefing with clear topic sections."
    User: [deduplicated chunks, ordered by publish_date ascending]
    │
    ▼
Store digest in DB
    └── digest(channel_id, content, generated_at, covers_from, covers_to, source_ids)
        Displayed in Summarize section of catch-up view
```

---

## Tool 2: MusicSort

### Concept

All YouTube Music playlists should contain only user-uploaded tracks. The tool syncs a local
MP3 library to YouTube Music, manages individual-track playlists, and maintains a combined
playlist where multi-track albums/soundtracks are merged into single files so shuffle works
sensibly. It also detects and flags unauthorized playlist changes made by YouTube Music.

### Playlist Architecture

```
YouTube Music (uploaded content only)
├── [Soundtrack Playlist A]     ← individual tracks for this soundtrack
├── [Soundtrack Playlist B]     ← individual tracks for this soundtrack
├── [Other Playlist C]          ← individual tracks, non-soundtrack content
└── [Combined Playlist]         ← one merged track per soundtrack/series;
                                   individual tracks for non-soundtrack content
```

### ytmusicapi Capabilities

The library (v1.12 current) supports:

- `upload_song(filepath)` — upload a local file to YouTube Music
- `delete_upload_entity(entityId)` — remove an uploaded track
- `get_library_upload_songs/albums/artists` — list uploaded content
- `create_playlist`, `add_playlist_items`, `remove_playlist_items`, `edit_playlist` — full
  playlist management
- OAuth2 authentication via `setup_oauth`

It does not support downloading tracks or real-time change notifications. It is unofficial
and reverse-engineered; Google can break it without notice.

### Feature Breakdown

#### Feature 1 — Local MP3 Scanner

Scans a configured directory (recursive). For each file:

1. Read ID3 tags with `mutagen` (title, artist, album, track number, year).
2. Fingerprint with `acoustid` + MusicBrainz lookup for canonical metadata where tags are
   missing or inconsistent.
3. Apply silence detection and removal (see Audio Processing).
4. Apply volume normalization (see Audio Processing).
5. Store in DB with file path, hash, tags, and processing status.

Processed files are written to a configurable output directory (originals preserved).

#### Feature 2 — Track Merger (soundtrack/series grouping)

For playlists designated as "merge for combined":

1. Group tracks by album (or user-defined group, e.g. a film series across multiple albums).
2. Concatenate audio in track-number order using `ffmpeg` directly.
3. Apply configurable gap or crossfade between tracks (default: 0s).
4. Write merged file with combined metadata (album title as track title).
5. Upload merged file to YouTube Music as a single track.
6. Add merged track to Combined Playlist.
7. Add individual tracks to the individual playlist for that soundtrack.

Merge group configuration is managed via a settings page.

#### Feature 3 — YouTube Music Sync

After processing local files:

1. Fetch all uploaded tracks from YouTube Music.
2. Match against local DB by title/artist/album (fuzzy match, configurable threshold).
3. Identify gaps:
   - Local file not on YTM → upload it.
   - On YTM but not local → flag for review (not auto-deleted).
4. Verify each playlist's contents match the expected set from DB.
5. Log all changes.

#### Feature 4 — Change Detector

Runs on a schedule. After every sync, snapshot current playlist state to DB. On next run,
diff current YTM state against snapshot:

- Track not added by this tool → flagged as "unauthorized change"
- Expected track missing → flagged as "unexpected removal"

Review page lists flagged changes with options to accept (update snapshot) or revert.

#### Audio Processing

**Volume normalization** — ffmpeg `loudnorm` filter (EBU R128):

```bash
ffmpeg -i input.mp3 -af loudnorm=I=-16:TP=-1.5:LRA=11 output.mp3
```

Two-pass normalization for accuracy. Applied to all files before upload.

**Silence removal** — `pydub` with configurable threshold (default: -50 dBFS) and minimum
silence duration (default: 500ms). Trailing silence only stripped by default; leading silence
preserved. Full stripping available as a per-playlist option.

---

## Tool 3: PodcastSync

### Concept

Interim solution to automate the current manual process of adding podcast episodes to a
Spotify playlist in oldest-first order and removing them after listening. Designed to be
replaced or extended (e.g. by a unified playback app) without requiring rework of the
broader architecture.

### Scope (explicit)

**In scope:**
- Fetch new episodes from followed Spotify shows
- Insert episodes into a designated Spotify playlist in oldest-first order
- Auto-remove fully-played episodes after a configurable delay
- Optionally archive episode transcripts to the reference RAG pipeline
- Show management UI within the main web app

**Out of scope (explicitly deferred):**
- Any playback functionality
- YouTube Music podcast upload/playback
- Custom Android app
- RSS-only podcasts not available on Spotify

### Playlist Position Management

Numeric position tracking is not used — it breaks when episodes are manually added, removed,
or reordered. Instead, URI-anchored insertion is used:

1. On each sync, fetch the full current playlist from Spotify.
2. Find the last episode in the playlist that is known to the DB ("anchor episode").
3. Insert new episodes immediately after the anchor, in release date ascending order.
4. If no anchor is found (e.g. playlist was manually cleared), append to end.

This is robust to manual additions and removals because it anchors to a known item's URI
rather than a numeric slot.

### Auto-Removal of Played Episodes

The Spotify API exposes `resume_point.fully_played` (boolean) and
`resume_point.resume_position_ms` on each episode object, accessible with the
`user-read-playback-position` scope.

Removal logic:
1. On each sync, fetch play state for all episodes currently in the podcast playlist.
2. For any episode where `fully_played = true`, record `marked_played_at` in DB if not
   already set.
3. On the next sync run that occurs at least N minutes after `marked_played_at` (default:
   30 minutes, configurable globally and per-show), remove the episode from the playlist.

The delay prevents episodes from being removed while you are mid-listen or if you briefly
stop and the app marks the episode played prematurely.

### Podcast Transcript Archiving (optional)

Per-show toggle in settings. When enabled for a show:

- On each sync, queue transcript fetch for newly added episodes.
- Transcript source: RSS feed audio → whisper.cpp (Spotify provides no transcript API).
- Chunked and stored in ChromaDB with metadata:
  `source_type='podcast'`, `show_id`, `episode_id`, `publish_date`, `trust_score`
- No digest is generated. Transcripts are reference material only.
- The show's RSS feed URL must be configured manually (Spotify API does not expose it).

### Show Management UI

A dedicated page within the main web app:

- List of all followed Spotify shows
- Per-show: name, thumbnail, last episode added, "new since last sync" count
- Archive toggle (enables transcript pipeline for that show)
- Trust/reliability score (for ChromaDB metadata)
- Removal delay override (per-show, falls back to global default)
- "Sync now" button (manual trigger, respects Spotify API rate limits)
- Quota/rate-limit status indicator

---

## Reference RAG Groundwork

The full reference RAG design is explicitly out of scope for this version. However, the
following groundwork is laid now so that nothing needs to be re-ingested or restructured
when the reference RAG is built out:

### Design principles (to be preserved)

- **One shared ChromaDB collection** (`reference_rag`) for all archivable sources for now.
  Individual sub-collections or a federated multi-RAG architecture can be adopted later;
  the metadata schema supports either without re-ingestion.
- **Source metadata on every chunk** enables filtering, weighting, and provenance tracking
  regardless of future architecture decisions.
- **Trust score attached at ingest time**, not query time, so it travels with the data.

### Chunk metadata schema (all sources)

Every chunk stored in ChromaDB carries:

```python
{
    "source_id":    str,   # channel_id, show_id, or future source identifier
    "source_type":  str,   # 'youtube_channel' | 'podcast' | (future types)
    "source_name":  str,   # human-readable name
    "trust_score":  float, # 0.0–1.0; set per source in channel/show settings
    "publish_date": str,   # ISO 8601
    "chunk_index":  int,
    "item_id":      str,   # video_id or episode_id
    "item_title":   str,
}
```

### Trust score

Stored on the `channels` table and `podcast_shows` table as a float (0.0–1.0). Editable
in the classifier UI and show management UI. Suggested starting categories (not enforced):

| Score range | Intended meaning |
|-------------|-----------------|
| 0.9 – 1.0   | Primary source / direct expert |
| 0.7 – 0.9   | Expert commentary, high-quality journalism |
| 0.5 – 0.7   | Opinion / analysis, generally reliable |
| 0.0 – 0.5   | Speculative, contested, or unknown reliability |

The score is stored as a raw float; the categories above are a human guide only. Future
query logic can use it however makes sense at that time.

---

## API Quota Strategy

### Quota costs for our operations

| Operation                              | API Method              | Cost      |
|----------------------------------------|-------------------------|-----------|
| Fetch Watch Later contents             | playlistItems.list      | 1/page    |
| Fetch channel uploads playlist         | playlistItems.list      | 1/page    |
| Remove video from a playlist           | playlistItems.delete    | 50        |
| Add video to a playlist                | playlistItems.insert    | 50        |
| Fetch subscription list                | subscriptions.list      | 1/page    |
| Fetch channel metadata                 | channels.list           | 1         |
| Fetch video metadata (batch up to 50)  | videos.list             | 1         |

`search.list` (100 units each) is never used. New videos from subscriptions are discovered
by polling each channel's uploads playlist directly, not by searching.

### Quota tracking

Every API call is logged to `quota_log`. A dashboard shows:

- Units used today (resets at midnight Pacific Time)
- Projected units for the next poll cycle
- Configurable warning threshold (default: 80% of daily budget)

### Subscription polling — rotating priority

Channels are assigned to a priority tier:

- **Tier 1 (always polled):** Up to 25 channels. Polled every cycle.
- **Tier 2 (rotating):** Remaining channels. One group polled per cycle such that each
  channel is covered within a configurable window (default: 7 days).

The scheduler always polls Tier 1 first, then fills remaining budget with the Tier 2 group
whose `next_poll_due` is soonest. If a cycle would exceed the daily budget, it stops and
logs a warning; the next cycle continues from where it left off.

### Subscription management UI

- All subscribed channels with name, thumbnail, classification, archive flag, trust score
- Polling tier (dropdown or drag-and-drop to change)
- Last polled, next poll due, new-since-last-poll count
- Force-poll button (deducts from today's budget immediately)

---

## Database Schema

```sql
-- YouTube channels
channels(
    channel_id      TEXT PRIMARY KEY,
    name            TEXT,
    thumbnail_url   TEXT,
    classification  TEXT,        -- 'watch' | 'listen' | 'summarize' | 'unclassified'
    archive         INTEGER DEFAULT 0,  -- 1 = archive transcripts to reference RAG
                                        -- always 1 when classification = 'summarize'
    trust_score     REAL DEFAULT 0.7,   -- 0.0–1.0; stored as chunk metadata at ingest
    poll_tier       INTEGER DEFAULT 2,  -- 1 = always poll, 2 = rotating
    last_polled     DATETIME,
    next_poll_due   DATETIME,
    created_at      DATETIME
)

-- Videos in WatchSort queues
videos(
    video_id              TEXT PRIMARY KEY,
    channel_id            TEXT REFERENCES channels,
    title                 TEXT,
    duration_seconds      INTEGER,
    published_at          DATETIME,
    added_to_watch_later  DATETIME,
    status                TEXT,   -- 'pending_classification' | 'in_watch_queue' |
                                  --   'in_listen_queue' | 'in_summarize_queue' |
                                  --   'done' | 'unavailable'
    yt_playlist_item_id   TEXT,   -- YouTube's item ID for deletion from managed playlist
    transcript_status     TEXT,   -- NULL | 'queued' | 'fetched' | 'chunked' | 'failed'
    created_at            DATETIME
)

-- Transcript chunks (YouTube videos and podcast episodes; shared reference RAG store)
transcript_chunks(
    id            INTEGER PRIMARY KEY,
    item_id       TEXT,           -- video_id or episode_id
    source_type   TEXT,           -- 'youtube_channel' | 'podcast'
    source_id     TEXT,           -- channel_id or show_id
    chunk_index   INTEGER,
    content       TEXT,
    embedding_id  TEXT,           -- ChromaDB document ID
    publish_date  DATETIME,
    trust_score   REAL,           -- denormalised from source at ingest time
    created_at    DATETIME
)

-- Digests (summarize channels only)
digests(
    id               INTEGER PRIMARY KEY,
    source_id        TEXT,        -- channel_id (podcast shows do not generate digests)
    source_type      TEXT,        -- 'youtube_channel' (extensible)
    content          TEXT,
    generated_at     DATETIME,
    covers_from      DATETIME,
    covers_to        DATETIME,
    source_item_ids  TEXT         -- JSON array of video_ids included
)

-- Mark-read timestamps (per summarize source)
digest_read_markers(
    source_id    TEXT PRIMARY KEY,
    source_type  TEXT,
    last_read_at DATETIME
)

-- API quota log (YouTube Data API)
quota_log(
    id          INTEGER PRIMARY KEY,
    timestamp   DATETIME,
    method      TEXT,
    units_used  INTEGER,
    source_id   TEXT,             -- nullable; channel being polled
    notes       TEXT
)

-- Podcast shows (PodcastSync)
podcast_shows(
    show_id          TEXT PRIMARY KEY,  -- Spotify show ID
    name             TEXT,
    thumbnail_url    TEXT,
    rss_feed_url     TEXT,              -- required for transcript archiving; set manually
    archive          INTEGER DEFAULT 0, -- 1 = archive transcripts to reference RAG
    trust_score      REAL DEFAULT 0.7,
    removal_delay_minutes INTEGER DEFAULT 30,  -- per-show override; NULL = use global
    last_synced      DATETIME,
    created_at       DATETIME
)

-- Podcast episodes (PodcastSync)
podcast_episodes(
    episode_id          TEXT PRIMARY KEY,  -- Spotify episode ID
    show_id             TEXT REFERENCES podcast_shows,
    title               TEXT,
    release_date        DATETIME,
    duration_ms         INTEGER,
    spotify_uri         TEXT,              -- e.g. spotify:episode:XXXX
    playlist_status     TEXT,             -- 'queued' | 'in_playlist' | 'done'
    marked_played_at    DATETIME,          -- when fully_played first seen; NULL if not yet
    transcript_status   TEXT,             -- NULL | 'queued' | 'fetched' | 'chunked' | 'failed'
    created_at          DATETIME
)

-- Local MP3 library (MusicSort)
local_tracks(
    id                 INTEGER PRIMARY KEY,
    file_path          TEXT UNIQUE,
    file_hash          TEXT,
    title              TEXT,
    artist             TEXT,
    album              TEXT,
    track_number       INTEGER,
    year               INTEGER,
    duration_seconds   REAL,
    ytm_video_id       TEXT,        -- YouTube Music entity ID after upload
    merge_group        TEXT,        -- nullable; album/series key for merged tracks
    processing_status  TEXT,        -- 'raw' | 'normalized' | 'uploaded'
    created_at         DATETIME,
    updated_at         DATETIME
)

-- Merged soundtrack tracks (MusicSort)
merged_tracks(
    id                     INTEGER PRIMARY KEY,
    merge_group            TEXT,
    output_file_path       TEXT,
    ytm_video_id           TEXT,
    constituent_track_ids  TEXT,    -- JSON array of local_tracks.id values
    created_at             DATETIME
)

-- YTM playlist snapshots for change detection (MusicSort)
ytm_playlist_snapshots(
    id               INTEGER PRIMARY KEY,
    ytm_playlist_id  TEXT,
    snapshot_at      DATETIME,
    track_ids        TEXT           -- JSON array of YTM video IDs in order
)
```

---

## Build Order

Each step is independently testable before moving on.

### Phase 1 — Foundation

1. **Project scaffold:** FastAPI app, SQLite DB with SQLModel, Jinja2 templates, HTMX wiring
2. **Google OAuth2:** YouTube Data API auth flow, token storage and refresh
3. **Watch Later read:** Fetch playlist, store videos in DB, detect unavailable tombstones

### Phase 2 — WatchSort Core

4. **Channel classifier UI:** Primary classification + archive toggle + trust score; persist to DB
5. **Queue routing:** Sync job moves videos from Watch Later to Watch Queue or Listen Queue;
   archive-flagged videos also queued for transcript pipeline
6. **Catch-up view:** Watch and Listen sections (identical player); checkboxes; "Done with
   checked" and "Keep checked, done with rest"; select all / deselect all

### Phase 3 — WatchSort Automation

7. **Subscription fetcher:** Read subscriptions, populate channels table, surface in classifier
8. **Priority tier UI:** Assign channels to tiers, configure poll frequency
9. **APScheduler jobs:** Watch Later sync job, subscription poll job
10. **Quota tracker:** Log all calls, dashboard display, budget enforcement

### Phase 4 — Transcript and Digest Pipeline

11. **yt-dlp transcript fetch:** Background job, triggered on routing for archive-flagged videos
12. **whisper.cpp fallback:** Background job with status indicator
13. **ChromaDB setup:** `reference_rag` collection, chunking with full metadata schema
14. **Digest generation:** Claude API synthesis for summarize channels; deduplication pass
15. **Summarize/Digest section UI:** Per-source cards, mark-read button; designed for
    multiple source types

### Phase 5 — PodcastSync

16. **Spotify OAuth2:** Auth flow, token storage and refresh
17. **Show sync:** Fetch followed shows, populate `podcast_shows` table, show management UI
18. **Episode fetch and insertion:** Oldest-first order, URI-anchored positioning
19. **Auto-removal:** Played episode detection via `resume_point`, configurable delay
20. **Transcript archiving:** RSS audio → whisper.cpp → ChromaDB (per-show opt-in)

### Phase 6 — MusicSort

21. **ytmusicapi auth:** Separate OAuth flow, confirm upload access
22. **Local MP3 scanner:** mutagen tag read, acoustid fingerprint, DB population
23. **Audio processing:** ffmpeg loudnorm normalization, pydub silence stripping
24. **Track merger:** Group-by-album, concatenation, merged file upload
25. **YTM playlist sync:** Upload missing tracks, verify playlist contents
26. **Change detector:** Snapshot diffing, review UI

---

## Open Questions

| # | Question | Default assumption |
|---|----------|--------------------|
| 1 | Embed YouTube player in the app, or open videos on youtube.com? | Open on YouTube for Watch; embed optional for Listen |
| 2 | Should "Done with checked" also remove from the actual YouTube playlist, or only from local DB tracking? | Remove from both |
| 3 | For the Combined Playlist in MusicSort — does non-soundtrack content appear as individual tracks in the combined playlist, or is it excluded? | Included as individual tracks |
| 4 | Silence stripping: leading silence, trailing silence, or both? | Trailing only by default; configurable per playlist |
| 5 | What is the output format for merged tracks? MP3 (universal) or FLAC (lossless)? | MP3 for compatibility |
| 6 | Should whisper.cpp transcription run inline (blocking) or as a background job? | Background job with status indicator |
| 7 | How to handle a channel that uploads very rarely — Tier 1 eligible regardless? | User decides; no auto-promotion |
| 8 | For podcast transcript archiving, should the RSS feed URL be required before the show appears in the archive queue, or should it warn and skip silently? | Warn in UI; skip silently in background job |
| 9 | Should the trust score have a UI-enforced scale with named levels, or remain a raw float the user sets freely? | Raw float with the suggested scale shown as a guide only |

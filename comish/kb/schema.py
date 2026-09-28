"""SQLite schema for a league's knowledge base, as ordered migrations.

Each migration runs once, in order, inside a transaction; `schema_version` records
how far a database has been migrated. Never edit a released migration: append one.
"""

MIGRATIONS: list[str] = [
    # 1: sources, records, transcriptions, Sleeper snapshots, field verifications, syncs
    """
    CREATE TABLE sources (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('gdoc', 'pdf', 'sheet', 'image', 'sleeper', 'ruling')),
        external_id TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        path TEXT NOT NULL DEFAULT '',
        mime_type TEXT NOT NULL DEFAULT '',
        modified_time TEXT,
        content_hash TEXT,
        effective_date TEXT,
        date_basis TEXT NOT NULL DEFAULT 'none' CHECK (date_basis IN
            ('none', 'in-document', 'commish-set', 'visible-in-image', 'sleeper-season',
             'undated')),
        date_hint TEXT,
        status TEXT NOT NULL DEFAULT 'pending_review' CHECK (status IN
            ('pending_review', 'approved', 'rejected', 'archived', 'failed')),
        status_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        reviewed_at TEXT
    );

    CREATE TABLE records (
        id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES sources(id),
        record_type TEXT NOT NULL CHECK (record_type IN
            ('doc_section', 'sheet_row', 'vote', 'ruling', 'sleeper_setting')),
        section_path TEXT NOT NULL,
        ordinal INTEGER NOT NULL DEFAULT 0,
        text TEXT NOT NULL,
        text_hash TEXT NOT NULL,
        season TEXT,
        effective_date TEXT,
        date_basis TEXT,
        status TEXT NOT NULL DEFAULT 'pending_review' CHECK (status IN
            ('pending_review', 'approved', 'superseded', 'rejected', 'archived')),
        superseded_by INTEGER REFERENCES records(id),
        topic_tags TEXT NOT NULL DEFAULT '[]',
        meta TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        reviewed_at TEXT
    );
    CREATE INDEX records_source ON records(source_id, section_path, ordinal);
    CREATE INDEX records_status ON records(status);

    CREATE TABLE image_transcriptions (
        record_id INTEGER PRIMARY KEY REFERENCES records(id),
        pass_a TEXT NOT NULL,
        pass_b TEXT NOT NULL,
        disagreements TEXT NOT NULL,
        model TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE sleeper_snapshots (
        league_id TEXT PRIMARY KEY,
        season TEXT NOT NULL,
        previous_league_id TEXT,
        content_hash TEXT NOT NULL,
        league_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL
    );

    CREATE TABLE field_verifications (
        path TEXT PRIMARY KEY,
        verified_at TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE sync_runs (
        id INTEGER PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        report TEXT NOT NULL
    );
    """,
]

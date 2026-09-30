CREATE TABLE IF NOT EXISTS responders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    skills TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    is_available INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'AVAILABLE'
);

CREATE TABLE IF NOT EXISTS hospitals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    phone TEXT
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_name TEXT,
    phone_number TEXT,
    reporter_id TEXT,
    description TEXT NOT NULL,
    emergency_type TEXT,
    priority TEXT,
    reported_condition TEXT NOT NULL DEFAULT 'Not specified',
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    ai_summary TEXT,
    status TEXT NOT NULL DEFAULT 'REPORTED',
    assigned_responder_id INTEGER,
    hospital_id INTEGER,
    assigned_at TEXT,
    created_at TEXT NOT NULL,
    low_bandwidth INTEGER NOT NULL DEFAULT 0,
    help_radius_m INTEGER NOT NULL DEFAULT 800,
    voice_note_file TEXT,
    control_call_status TEXT NOT NULL DEFAULT 'NOT_REQUESTED',
    control_call_sid TEXT,
    control_call_error TEXT,
    FOREIGN KEY (assigned_responder_id) REFERENCES responders(id),
    FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
);

CREATE TABLE IF NOT EXISTS incident_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    responder_id INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('ACCEPTED','REJECTED')),
    created_at TEXT NOT NULL,
    UNIQUE (incident_id, responder_id),
    FOREIGN KEY (incident_id) REFERENCES incidents(id),
    FOREIGN KEY (responder_id) REFERENCES responders(id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER,
    responder_id INTEGER,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(id),
    FOREIGN KEY (responder_id) REFERENCES responders(id)
);

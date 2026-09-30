from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from html import escape
import json
import math
import mimetypes
import os
import re
import sqlite3
import threading
import urllib.parse
import urllib.request
import base64
import secrets
import time

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / 'data'
DB_PATH = DB_DIR / 'resq.db'
SCHEMA_PATH = BASE_DIR / 'schema.sql'
DB_LOCK = threading.Lock()

SUPPORTED_TYPES = {'ACCIDENT', 'MEDICAL', 'FIRE', 'SECURITY', 'GENERAL'}
TYPE_LABELS = {
    'ACCIDENT': 'Road accident',
    'MEDICAL': 'Medical emergency',
    'FIRE': 'Fire',
    'SECURITY': 'Personal safety',
    'GENERAL': 'General emergency',
}


def load_dotenv():
    """Load simple KEY=VALUE pairs from .env without an external dependency."""
    env_path = BASE_DIR / '.env'
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
    except OSError:
        pass


load_dotenv()
HOST = os.getenv('RESQ_HOST', '127.0.0.1')
try:
    PORT = int(os.getenv('RESQ_PORT', '5000'))
except ValueError:
    PORT = 5000

try:
    DEFAULT_HELP_RADIUS_KM = float(os.getenv('DEFAULT_HELP_RADIUS_KM', '0.8'))
except ValueError:
    DEFAULT_HELP_RADIUS_KM = 0.8
try:
    MAX_HELP_RADIUS_KM = float(os.getenv('DEMO_MAX_RADIUS_KM', '5'))
except ValueError:
    MAX_HELP_RADIUS_KM = 5.0
DEMO_LAT = float(os.getenv('DEMO_LOCATION_LAT', '11.2588'))
DEMO_LON = float(os.getenv('DEMO_LOCATION_LON', '75.7804'))


def get_db():
    DB_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    DB_DIR.mkdir(exist_ok=True)
    (DB_DIR / 'voice_notes').mkdir(exist_ok=True)
    with get_db() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding='utf-8'))

        # Upgrade a demo DB created by the original prototype without requiring a reset.
        existing = {row['name'] for row in conn.execute('PRAGMA table_info(incidents)')}
        upgrades = {
            'phone_number': 'TEXT',
            'reporter_id': 'TEXT',
            'low_bandwidth': 'INTEGER NOT NULL DEFAULT 0',
            'reported_condition': "TEXT NOT NULL DEFAULT 'Not specified'",
            'help_radius_m': 'INTEGER NOT NULL DEFAULT 800',
            'voice_note_file': 'TEXT',
            'control_call_status': "TEXT NOT NULL DEFAULT 'NOT_REQUESTED'",
            'control_call_sid': 'TEXT',
            'control_call_error': 'TEXT',
        }
        for name, declaration in upgrades.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE incidents ADD COLUMN {name} {declaration}')

        if conn.execute('SELECT COUNT(*) AS c FROM responders').fetchone()['c'] == 0:
            conn.executemany(
                '''INSERT INTO responders
                (name,email,skills,latitude,longitude,is_available,status)
                VALUES (?,?,?,?,?,?,?)''',
                [
                    ('Aarav Menon', 'aarav@example.com', 'CPR, First Aid', 11.2552, 75.7804, 1, 'AVAILABLE'),
                    ('Ananya Nair', 'ananya@example.com', 'First Aid, Nursing', 11.2588, 75.7754, 1, 'AVAILABLE'),
                    ('Rahul Das', 'rahul@example.com', 'First Aid', 11.2620, 75.7830, 1, 'AVAILABLE'),
                    ('Maya Thomas', 'maya@example.com', 'CPR, First Aid', 11.2500, 75.7880, 0, 'BUSY'),
                    ('Vishnu Raj', 'vishnu@example.com', 'Fire Safety', 11.2680, 75.7700, 1, 'AVAILABLE'),
                ],
            )
            conn.executemany(
                '''INSERT INTO hospitals
                (name,type,latitude,longitude,status,phone) VALUES (?,?,?,?,?,?)''',
                [
                    ('City Care Hospital', 'Medical', 11.2567, 75.7818, 'OPEN', '0484-555-0101'),
                    ('Lifeline Medical Centre', 'Multi-specialty', 11.2494, 75.7741, 'OPEN', '0484-555-0102'),
                    ('District Emergency Hospital', 'Emergency', 11.2651, 75.7890, 'OPEN', '0484-555-0103'),
                ],
            )


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def rows_to_dict(rows):
    return [dict(r) for r in rows]


def haversine_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))



def as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

def normalize_phone(value):
    value = str(value or '').strip()
    if not value:
        return ''
    # E.164 is the most reliable format for provider APIs.
    if re.fullmatch(r'\+[1-9]\d{7,14}', value):
        return value
    # Helpful convenience for Indian demo numbers: 10 digits -> +91.
    if re.fullmatch(r'[6-9]\d{9}', value):
        return '+91' + value
    raise ValueError('Phone number must use E.164 format, e.g. +919876543210.')


def analyze_emergency(description):
    """Transparent keyword classifier, deliberately not a medical diagnosis."""
    keywords = {
        'ACCIDENT': [
            'accident', 'crash', 'bike', 'bicycle', 'motorcycle', 'scooter', 'car',
            'collision', 'fell', 'fallen', 'fall', 'road', 'traffic', 'vehicle', 'injury', 'injured'
        ],
        'MEDICAL': [
            'collapse', 'collapsed', 'unconscious', 'not responding', 'chest pain', 'bleeding',
            'blood', 'breathing', 'fainted', 'seizure', 'heart', 'pain', 'sick', 'vomit'
        ],
        'FIRE': ['fire', 'smoke', 'burning', 'flame', 'blast'],
        'SECURITY': ['attack', 'threat', 'fight', 'robbery', 'danger', 'assault', 'unsafe'],
    }
    text = str(description or '').lower()
    scores = {
        key: sum(1 for word in words if re.search(r'\b' + re.escape(word) + r'\b', text))
        for key, words in keywords.items()
    }
    emergency_type = max(scores, key=scores.get) if max(scores.values()) else 'GENERAL'

    critical_terms = [
        'unconscious', 'not responding', 'severe bleeding', 'heavy bleeding',
        'cannot breathe', 'not breathing', 'fire', 'attack', 'chest pain', 'blast'
    ]
    priority = 'CRITICAL' if any(term in text for term in critical_terms) else (
        'HIGH' if emergency_type != 'GENERAL' else 'MEDIUM'
    )

    condition_terms = [
        ('Bleeding reported', ['bleeding', 'bleed', 'blood']),
        ('Injury reported', ['injury', 'injured', 'wound', 'cut', 'fell', 'fallen', 'crash', 'accident']),
        ('Breathing difficulty reported', ['breathing difficulty', 'cannot breathe', 'not breathing', 'trouble breathing']),
        ('Unconsciousness reported', ['unconscious', 'not responding', 'fainted']),
        ('Pain reported', ['pain', 'hurts', 'hurt']),
    ]
    conditions = [label for label, terms in condition_terms if any(term in text for term in terms)]
    reported_condition = ', '.join(conditions) if conditions else 'Not specified by reporter'

    summaries = {
        'MEDICAL': 'Possible medical emergency reported. Professional emergency assistance should be contacted.',
        'ACCIDENT': 'Possible accident/injury reported. Emergency response and medical assessment may be required.',
        'FIRE': 'Possible fire or smoke emergency reported. Move to safety and contact emergency services.',
        'SECURITY': 'Possible personal safety/security emergency reported. Move to a safe location and contact authorities.',
        'GENERAL': 'Emergency reported. Additional details should be verified by the response team.',
    }
    return {
        'type': emergency_type,
        'category': TYPE_LABELS[emergency_type],
        'priority': priority,
        'reported_condition': reported_condition,
        'summary': summaries[emergency_type],
        'disclaimer': 'Based only on the reporter’s words; this is not a medical diagnosis.',
    }


def skill_score(skills, emergency_type):
    skills_text = str(skills or '').lower()
    if emergency_type in {'MEDICAL', 'ACCIDENT'}:
        return 3 if ('cpr' in skills_text or 'nursing' in skills_text) else (2 if 'first aid' in skills_text else 0)
    if emergency_type == 'FIRE':
        return 3 if 'fire' in skills_text else 0
    return 2 if 'first aid' in skills_text else 0


def estimate_route(lat1, lon1, lat2, lon2):
    """Demo road estimate; clearly labelled simulated traffic."""
    straight_km = haversine_km(lat1, lon1, lat2, lon2)
    road_km = straight_km * 1.22
    eta_min = max(1, math.ceil(road_km / 0.42))
    return {
        'distance_km': round(road_km, 2),
        'eta_min': eta_min,
        'source': 'SIMULATED TRAFFIC',
    }


def get_route(a, b, mode='simulated'):
    if str(mode).lower() == 'live' and os.getenv('ENABLE_LIVE_ROUTING', 'false').lower() == 'true':
        base = os.getenv('OSRM_BASE_URL', 'https://router.project-osrm.org').rstrip('/')
        url = f'{base}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=false'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'RESQ-hackathon-prototype/2.0'})
            with urllib.request.urlopen(req, timeout=4) as response:
                data = json.loads(response.read().decode('utf-8'))
            route = data['routes'][0]
            return {
                'distance_km': round(route['distance'] / 1000, 2),
                'eta_min': max(1, math.ceil(route['duration'] / 60)),
                'source': 'OSRM',
            }
        except Exception:
            pass
    return estimate_route(a[0], a[1], b[0], b[1])


def find_matches(lat, lon, emergency_type, radius_km=DEFAULT_HELP_RADIUS_KM):
    with get_db() as conn:
        responders = conn.execute(
            'SELECT * FROM responders WHERE is_available=1 ORDER BY name'
        ).fetchall()

    measured = []
    for responder in responders:
        distance = haversine_km(lat, lon, responder['latitude'], responder['longitude'])
        measured.append((responder, distance))

    help_radius = max(0.8, float(radius_km or 0.8))
    # Expand by 800 m until at least 3 available responders are inside,
    # capped by the configured demo limit.
    while help_radius < MAX_HELP_RADIUS_KM and sum(1 for _, d in measured if d <= help_radius) < 3:
        help_radius = min(MAX_HELP_RADIUS_KM, round(help_radius + 0.8, 1))

    matches = []
    for responder, distance in measured:
        if distance <= help_radius:
            ss = skill_score(responder['skills'], emergency_type)
            score = min(100, round(max(0, 100 - distance * 15) + ss * 15, 1))
            route = get_route((lat, lon), (responder['latitude'], responder['longitude']), 'simulated')
            matches.append({
                'id': responder['id'],
                'name': responder['name'],
                'skills': responder['skills'],
                'latitude': responder['latitude'],
                'longitude': responder['longitude'],
                'distance_km': round(distance, 2),
                'eta_min': route['eta_min'],
                'route_source': route['source'],
                'match_score': score,
            })
    matches.sort(key=lambda item: (-item['match_score'], item['distance_km']))
    return matches[:10], round(help_radius * 1000), sum(1 for _, d in measured if d <= help_radius)


def hospital_routes(lat, lon):
    with get_db() as conn:
        hospitals = conn.execute("SELECT * FROM hospitals WHERE status='OPEN'").fetchall()
    routes = []
    for hospital in hospitals:
        route = get_route((lat, lon), (hospital['latitude'], hospital['longitude']), 'simulated')
        routes.append({
            'id': hospital['id'],
            'name': hospital['name'],
            'type': hospital['type'],
            'phone': hospital['phone'],
            'latitude': hospital['latitude'],
            'longitude': hospital['longitude'],
            **route,
        })
    return sorted(routes, key=lambda item: (item['eta_min'], item['distance_km']))


def twilio_credentials_ready():
    enabled = os.getenv('CALL_ON_SOS', 'true').lower() == 'true'
    return enabled and bool(
        os.getenv('TWILIO_ACCOUNT_SID') and
        os.getenv('TWILIO_AUTH_TOKEN') and
        os.getenv('TWILIO_FROM_NUMBER')
    )


def build_control_call_twiml(incident_id, category, priority):
    control_name = escape(os.getenv('CONTROL_STATION_NAME', 'RESQ Control Station'))
    repeat = os.getenv('CONTROL_CALL_REPEAT', 'false').lower() == 'true'
    message = (
        f'{control_name}. We received your emergency S O S, incident number {incident_id}. '
        f'The reported category is {category}, with priority {priority}. '
        'Your location has been sent to the RESQ response system. '
        'Please stay in a safe place and stay on the line if it is safe to do so. '
        'This is an automated emergency callback from the control station.'
    )
    if repeat:
        message += ' I will repeat the emergency message once.'
        return f'<Response><Say language="en-US">{escape(message)}</Say><Pause length="1"/><Say language="en-US">{escape(message)}</Say></Response>'
    return f'<Response><Say language="en-US">{escape(message)}</Say></Response>'


def place_control_station_call(incident_id, phone, category, priority):
    """Place a real outbound call through Twilio. Never expose Twilio secrets to the browser."""
    if not phone:
        return {'status': 'NOT_REQUESTED', 'sid': None, 'error': 'No phone number was provided.'}
    if os.getenv('CALL_ON_SOS', 'true').lower() != 'true':
        return {'status': 'DISABLED', 'sid': None, 'error': 'Automatic control-station calling is disabled in .env.'}
    if not twilio_credentials_ready():
        return {
            'status': 'NOT_CONFIGURED',
            'sid': None,
            'error': 'Twilio call service is not configured. Add Twilio credentials and TWILIO_FROM_NUMBER to .env.',
        }

    account_sid = os.environ['TWILIO_ACCOUNT_SID'].strip()
    auth_token = os.environ['TWILIO_AUTH_TOKEN'].strip()
    from_number = os.environ['TWILIO_FROM_NUMBER'].strip()
    url = f'https://api.twilio.com/2010-04-01/Accounts/{urllib.parse.quote(account_sid)}/Calls.json'
    data = urllib.parse.urlencode({
        'To': phone,
        'From': from_number,
        'Twiml': build_control_call_twiml(incident_id, category, priority),
    }).encode('utf-8')
    auth = base64.b64encode(f'{account_sid}:{auth_token}'.encode('utf-8')).decode('ascii')
    request = urllib.request.Request(
        url,
        data=data,
        method='POST',
        headers={
            'Authorization': f'Basic {auth}',
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'RESQ-hackathon-prototype/2.0',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode('utf-8'))
        sid = payload.get('sid')
        return {'status': 'CALL_REQUESTED', 'sid': sid, 'error': None}
    except Exception as exc:
        return {'status': 'CALL_FAILED', 'sid': None, 'error': str(exc)[:400]}


def notify(message, incident_id=None, responder_id=None):
    with DB_LOCK:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO notifications (incident_id,responder_id,message,created_at) VALUES (?,?,?,?)',
                (incident_id, responder_id, message, now_iso()),
            )


def create_incident(data, voice_note=None):
    low_bandwidth = as_bool(data.get('low_bandwidth', False))
    reporter_id = str(data.get('reporter_id', data.get('user_id', ''))).strip()[:80] or 'guest'
    phone_number = normalize_phone(data.get('phone_number', ''))

    if low_bandwidth:
        requested_type = str(data.get('emergency_type', 'GENERAL')).upper()
        if requested_type not in SUPPORTED_TYPES:
            raise ValueError('Choose a valid emergency type.')
        description = f'Low-bandwidth SOS. Emergency type selected by reporter: {requested_type}.'
        analysis = analyze_emergency(description)
        analysis.update({
            'type': requested_type,
            'category': TYPE_LABELS[requested_type],
            'priority': 'HIGH' if requested_type != 'GENERAL' else 'MEDIUM',
            'reported_condition': 'Not provided in low-bandwidth mode',
            'summary': f"Reporter selected {TYPE_LABELS[requested_type]}; further details were unavailable in low-bandwidth mode.",
        })
    else:
        description = str(data.get('description', '')).strip()[:2000]
        if not description and voice_note:
            requested_type = str(data.get('emergency_type', 'GENERAL')).upper()
            if requested_type not in SUPPORTED_TYPES:
                raise ValueError('Choose a valid emergency type for the voice report.')
            # The audio is retained so responders can listen, even if browser transcription was unavailable.
            description = 'Voice report attached; automatic transcription was unavailable.'
            analysis = analyze_emergency('')
            analysis.update({
                'type': requested_type,
                'category': TYPE_LABELS[requested_type],
                'priority': 'HIGH' if requested_type != 'GENERAL' else 'MEDIUM',
                'reported_condition': 'Voice report attached; details await responder review',
                'summary': f"Reporter selected {TYPE_LABELS[requested_type]}; review the attached voice message for details.",
            })
        elif not description:
            raise ValueError('Describe the emergency, record a voice report, or choose low-bandwidth SOS.')
        else:
            analysis = analyze_emergency(description)

    try:
        lat, lon = float(data['latitude']), float(data['longitude'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('Valid latitude and longitude are required.')
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Latitude or longitude is outside valid bounds.')

    incident_id = None
    voice_url = None
    with DB_LOCK:
        with get_db() as conn:
            cur = conn.execute(
                '''INSERT INTO incidents
                (reporter_name,phone_number,reporter_id,description,emergency_type,priority,reported_condition,
                 latitude,longitude,ai_summary,status,created_at,low_bandwidth,help_radius_m,
                 voice_note_file,control_call_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    str(data.get('name', 'Anonymous User'))[:100] or 'Anonymous User',
                    phone_number or None,
                    reporter_id,
                    description,
                    analysis['type'],
                    analysis['priority'],
                    analysis['reported_condition'],
                    lat,
                    lon,
                    analysis['summary'],
                    'REPORTED',
                    now_iso(),
                    int(low_bandwidth),
                    int(DEFAULT_HELP_RADIUS_KM * 1000),
                    None,
                    'NOT_REQUESTED',
                ),
            )
            incident_id = cur.lastrowid

            if voice_note:
                voice_folder = DB_DIR / 'voice_notes'
                voice_folder.mkdir(parents=True, exist_ok=True)
                filename = f'{incident_id}-{secrets.token_hex(8)}.{voice_note["extension"]}'
                (voice_folder / filename).write_bytes(voice_note['content'])
                conn.execute('UPDATE incidents SET voice_note_file=? WHERE id=?', (filename, incident_id))
                voice_url = f'/api/incidents/{incident_id}/voice'

            hospital = conn.execute("SELECT * FROM hospitals WHERE status='OPEN'").fetchall()
            if hospital:
                nearest = min(
                    hospital,
                    key=lambda item: haversine_km(lat, lon, item['latitude'], item['longitude']),
                )
                conn.execute('UPDATE incidents SET hospital_id=? WHERE id=?', (nearest['id'], incident_id))

    matches, help_radius_m, responder_count = find_matches(lat, lon, analysis['type'])
    with DB_LOCK:
        with get_db() as conn:
            conn.execute('UPDATE incidents SET help_radius_m=? WHERE id=?', (help_radius_m, incident_id))

    call = place_control_station_call(incident_id, phone_number, analysis['category'], analysis['priority']) if phone_number else {
        'status': 'NOT_REQUESTED', 'sid': None, 'error': 'No phone number was provided.'
    }
    with DB_LOCK:
        with get_db() as conn:
            conn.execute(
                'UPDATE incidents SET control_call_status=?, control_call_sid=?, control_call_error=? WHERE id=?',
                (call['status'], call['sid'], call['error'], incident_id),
            )

    notify(
        f'New {analysis["category"]} incident #{incident_id} reported. Priority: {analysis["priority"]}.',
        incident_id=incident_id,
    )
    for match in matches:
        notify(
            f'Nearby emergency #{incident_id}: {analysis["category"]}, {analysis["priority"]}, approximately {match["distance_km"]} km away. Review and respond.',
            incident_id=incident_id,
            responder_id=match['id'],
        )

    return {
        'incident_id': incident_id,
        'analysis': analysis,
        'matches': matches,
        'help_radius_m': help_radius_m,
        'responders_in_radius': responder_count,
        'hospital_routes': hospital_routes(lat, lon),
        'reporter_id': reporter_id,
        'phone_number': phone_number,
        'low_bandwidth': low_bandwidth,
        'voice_note_url': voice_url,
        'control_call': call,
        'message': 'Emergency incident created successfully.',
    }


class Handler(BaseHTTPRequestHandler):
    server_version = 'RESQ/2.0'

    def log_message(self, format_string, *args):
        print('%s - %s' % (self.address_string(), format_string % args))

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, body, content_type='text/html; charset=utf-8', status=200):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 1 * 1024 * 1024:
            raise ValueError('JSON payload is too large.')
        raw = self.rfile.read(size) if size else b'{}'
        return json.loads(raw.decode('utf-8') or '{}')

    def read_incident_payload(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size > 6 * 1024 * 1024:
            raise ValueError('Voice recordings must be shorter than 5 MB.')
        raw = self.rfile.read(size) if size else b'{}'
        content_type = self.headers.get('Content-Type', '')
        if not content_type.lower().startswith('multipart/form-data'):
            return json.loads(raw.decode('utf-8') or '{}'), None

        message = BytesParser(policy=default).parsebytes(
            b'MIME-Version: 1.0\r\nContent-Type: ' + content_type.encode('ascii', 'ignore') + b'\r\n\r\n' + raw
        )
        if not message.is_multipart():
            raise ValueError('The voice report upload could not be read.')

        fields = {}
        voice_note = None
        allowed_audio = {
            'audio/webm': 'webm',
            'audio/ogg': 'ogg',
            'audio/mp4': 'm4a',
            'audio/wav': 'wav',
            'audio/mpeg': 'mp3',
        }
        for part in message.iter_parts():
            field = part.get_param('name', header='content-disposition')
            if not field:
                continue
            payload = part.get_payload(decode=True) or b''
            if field == 'voice_note':
                if not payload:
                    continue
                mime = part.get_content_type()
                if mime not in allowed_audio:
                    raise ValueError('Unsupported voice recording format. Try Chrome or Edge.')
                if len(payload) > 5 * 1024 * 1024:
                    raise ValueError('Voice recordings must be shorter than 5 MB.')
                voice_note = {
                    'content': payload,
                    'mime_type': mime,
                    'extension': allowed_audio[mime],
                }
            else:
                fields[field] = payload.decode('utf-8', errors='replace')
        return fields, voice_note

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        page_map = {
            '/': 'index.html',
            '/index.html': 'index.html',
            '/control-room': 'control_room.html',
            '/responder': 'responder.html',
            '/hospital': 'hospital.html',
        }
        if path in page_map:
            return self.serve_file(BASE_DIR / 'templates' / page_map[path])

        if path.startswith('/static/'):
            rel = path[len('/static/'):]
            safe = (BASE_DIR / 'static' / rel).resolve()
            if BASE_DIR / 'static' in safe.parents:
                return self.serve_file(safe)
            return self.send_text('Not found', 'text/plain', 404)

        try:
            voice_match = re.fullmatch(r'/api/incidents/(\d+)/voice', path)
            if voice_match:
                with get_db() as conn:
                    row = conn.execute('SELECT voice_note_file FROM incidents WHERE id=?', (int(voice_match.group(1)),)).fetchone()
                if not row or not row['voice_note_file']:
                    return self.send_json({'error': 'Voice recording not found.'}, 404)
                voice_path = (DB_DIR / 'voice_notes' / Path(row['voice_note_file']).name).resolve()
                if DB_DIR / 'voice_notes' not in voice_path.parents:
                    return self.send_text('Not found', 'text/plain', 404)
                return self.serve_file(voice_path)

            if path == '/api/config':
                return self.send_json({
                    'demo_location': {'latitude': DEMO_LAT, 'longitude': DEMO_LON},
                    'default_help_radius_m': int(DEFAULT_HELP_RADIUS_KM * 1000),
                    'max_help_radius_m': int(MAX_HELP_RADIUS_KM * 1000),
                    'live_routing_enabled': os.getenv('ENABLE_LIVE_ROUTING', 'false').lower() == 'true',
                    'control_call_configured': twilio_credentials_ready(),
                    'control_station_name': os.getenv('CONTROL_STATION_NAME', 'RESQ Control Station'),
                })

            if path == '/api/responders':
                with get_db() as conn:
                    return self.send_json(rows_to_dict(conn.execute('SELECT * FROM responders ORDER BY name').fetchall()))

            if path == '/api/hospitals':
                with get_db() as conn:
                    return self.send_json(rows_to_dict(conn.execute('SELECT * FROM hospitals ORDER BY name').fetchall()))

            if path == '/api/incidents':
                with get_db() as conn:
                    rows = conn.execute(
                        '''SELECT i.*, r.name AS responder_name, r.latitude AS responder_latitude,
                                  r.longitude AS responder_longitude, h.name AS hospital_name
                           FROM incidents i
                           LEFT JOIN responders r ON i.assigned_responder_id=r.id
                           LEFT JOIN hospitals h ON i.hospital_id=h.id
                           ORDER BY i.created_at DESC'''
                    ).fetchall()
                incidents = rows_to_dict(rows)
                for incident in incidents:
                    voice_file = incident.pop('voice_note_file', None)
                    incident['voice_note_available'] = bool(voice_file)
                    _, radius_m, count = find_matches(
                        incident['latitude'], incident['longitude'], incident['emergency_type'] or 'GENERAL'
                    )
                    incident['help_radius_m'] = max(incident.get('help_radius_m') or 0, radius_m)
                    incident['responders_in_radius'] = count
                    incident['hospital_routes'] = hospital_routes(incident['latitude'], incident['longitude'])
                return self.send_json(incidents)

            if path == '/api/incident-responses':
                responder_id = qs.get('responder_id', [None])[0]
                with get_db() as conn:
                    if responder_id:
                        rows = conn.execute(
                            'SELECT incident_id,decision FROM incident_responses WHERE responder_id=?',
                            (responder_id,),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            'SELECT incident_id,responder_id,decision FROM incident_responses'
                        ).fetchall()
                return self.send_json(rows_to_dict(rows))

            if path == '/api/notifications':
                with get_db() as conn:
                    rows = conn.execute(
                        '''SELECT n.*, r.name AS responder_name
                           FROM notifications n
                           LEFT JOIN responders r ON n.responder_id=r.id
                           ORDER BY n.created_at DESC LIMIT 50'''
                    ).fetchall()
                return self.send_json(rows_to_dict(rows))

            if path == '/api/route':
                a = (float(qs['from_lat'][0]), float(qs['from_lon'][0]))
                b = (float(qs['to_lat'][0]), float(qs['to_lon'][0]))
                return self.send_json(get_route(a, b, qs.get('mode', ['simulated'])[0]))

            return self.send_text('Not found', 'text/plain', 404)
        except Exception as exc:
            return self.send_json({'error': str(exc)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == '/api/incidents':
                data, voice_note = self.read_incident_payload()
                return self.send_json(create_incident(data, voice_note), 201)

            data = self.read_json()
            if path == '/api/sos/low-bandwidth':
                if data.get('sos') is not True:
                    return self.send_json({'error': 'SOS confirmation is required.'}, 400)
                essential = {
                    'latitude': data.get('latitude'),
                    'longitude': data.get('longitude'),
                    'emergency_type': data.get('emergency_type'),
                    'reporter_id': data.get('user_id'),
                    'phone_number': data.get('phone_number'),
                    'low_bandwidth': True,
                    'name': data.get('name', 'Anonymous User'),
                }
                return self.send_json(create_incident(essential), 201)

            match = re.fullmatch(r'/api/incidents/(\d+)/respond', path)
            if match:
                decision = str(data.get('decision', '')).upper()
                if decision not in {'ACCEPTED', 'REJECTED'}:
                    return self.send_json({'error': 'Decision must be ACCEPTED or REJECTED.'}, 400)
                return self.respond(int(match.group(1)), data, decision)

            match = re.fullmatch(r'/api/incidents/(\d+)/call', path)
            if match:
                return self.retry_control_call(int(match.group(1)))

            match = re.fullmatch(r'/api/incidents/(\d+)/status', path)
            if match:
                return self.status_update(int(match.group(1)), data)

            return self.send_json({'error': 'Not found'}, 404)
        except ValueError as exc:
            return self.send_json({'error': str(exc)}, 400)
        except Exception as exc:
            return self.send_json({'error': str(exc)}, 500)

    def retry_control_call(self, incident_id):
        with get_db() as conn:
            incident = conn.execute('SELECT * FROM incidents WHERE id=?', (incident_id,)).fetchone()
        if not incident:
            return self.send_json({'error': 'Incident not found'}, 404)
        phone = incident['phone_number'] or ''
        if not phone:
            return self.send_json({'error': 'This incident has no phone number for callback.'}, 400)
        call = place_control_station_call(incident_id, phone, TYPE_LABELS.get(incident['emergency_type'], 'General emergency'), incident['priority'] or 'MEDIUM')
        with DB_LOCK:
            with get_db() as conn:
                conn.execute('UPDATE incidents SET control_call_status=?, control_call_sid=?, control_call_error=? WHERE id=?', (call['status'], call['sid'], call['error'], incident_id))
        return self.send_json({'incident_id': incident_id, 'control_call': call})

    def respond(self, incident_id, data, decision):
        responder_id = data.get('responder_id')
        if not responder_id:
            return self.send_json({'error': 'responder_id is required'}, 400)
        responder_id = int(responder_id)

        with DB_LOCK:
            with get_db() as conn:
                responder = conn.execute('SELECT * FROM responders WHERE id=?', (responder_id,)).fetchone()
                incident = conn.execute('SELECT * FROM incidents WHERE id=?', (incident_id,)).fetchone()
                if not responder or not incident:
                    return self.send_json({'error': 'Incident or responder not found'}, 404)
                if incident['status'] not in {'REPORTED', 'ASSIGNED', 'RESPONDING'}:
                    return self.send_json({'error': 'This incident is no longer accepting responses.'}, 409)

                timestamp = now_iso()
                if decision == 'ACCEPTED':
                    if responder['status'] != 'AVAILABLE' or not responder['is_available']:
                        return self.send_json({'error': 'Responder is not currently available'}, 409)
                    if incident['assigned_responder_id'] is not None:
                        return self.send_json({'error': 'Another responder has already accepted this incident.'}, 409)
                    distance = haversine_km(
                        incident['latitude'], incident['longitude'], responder['latitude'], responder['longitude']
                    )
                    _, radius_m, _ = find_matches(
                        incident['latitude'], incident['longitude'], incident['emergency_type'] or 'GENERAL'
                    )
                    if distance > radius_m / 1000:
                        return self.send_json({'error': 'This incident is outside the current help radius.'}, 403)
                    conn.execute(
                        'UPDATE incidents SET assigned_responder_id=?,status=?,assigned_at=? WHERE id=?',
                        (responder_id, 'ASSIGNED', timestamp, incident_id),
                    )
                    conn.execute(
                        "UPDATE responders SET is_available=0,status='BUSY' WHERE id=?",
                        (responder_id,),
                    )
                    message = f'{responder["name"]} accepted incident #{incident_id}. Responder is on the way.'
                else:
                    if incident['status'] != 'REPORTED' or incident['assigned_responder_id'] is not None:
                        return self.send_json({'error': 'This incident has already been accepted.'}, 409)
                    previous = conn.execute(
                        'SELECT id FROM incident_responses WHERE incident_id=? AND responder_id=?',
                        (incident_id, responder_id),
                    ).fetchone()
                    if previous:
                        return self.send_json({'message': 'Response already recorded.'})
                    message = f'{responder["name"]} declined incident #{incident_id}.'

                conn.execute(
                    'INSERT INTO incident_responses (incident_id,responder_id,decision,created_at) VALUES (?,?,?,?)',
                    (incident_id, responder_id, decision, timestamp),
                )
                conn.execute(
                    'INSERT INTO notifications (incident_id,responder_id,message,created_at) VALUES (?,?,?,?)',
                    (incident_id, responder_id, message, timestamp),
                )
                if decision == 'ACCEPTED':
                    conn.execute(
                        'INSERT INTO notifications (incident_id,responder_id,message,created_at) VALUES (?,?,?,?)',
                        (incident_id, None, message, timestamp),
                    )
        return self.send_json({'message': message, 'decision': decision})

    def status_update(self, incident_id, data):
        status = data.get('status')
        allowed = {'REPORTED', 'ASSIGNED', 'RESPONDING', 'ARRIVED', 'RESOLVED'}
        if status not in allowed:
            return self.send_json({'error': 'Invalid status'}, 400)

        with DB_LOCK:
            with get_db() as conn:
                incident = conn.execute('SELECT * FROM incidents WHERE id=?', (incident_id,)).fetchone()
                if not incident:
                    return self.send_json({'error': 'Incident not found'}, 404)
                conn.execute('UPDATE incidents SET status=? WHERE id=?', (status, incident_id))
                if status == 'RESOLVED' and incident['assigned_responder_id']:
                    conn.execute(
                        "UPDATE responders SET is_available=1,status='AVAILABLE' WHERE id=?",
                        (incident['assigned_responder_id'],),
                    )
                message = f'Incident #{incident_id} status changed to {status}.'
                conn.execute(
                    'INSERT INTO notifications (incident_id,responder_id,message,created_at) VALUES (?,?,?,?)',
                    (incident_id, incident['assigned_responder_id'], message, now_iso()),
                )
        return self.send_json({'message': 'Status updated.', 'status': status})

    def serve_file(self, path):
        if not path.exists() or not path.is_file():
            return self.send_text('Not found', 'text/plain', 404)
        data = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        if mime.startswith('audio/'):
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_text(data, mime)


def main():
    init_db()
    print(f'RESQ running at http://{HOST}:{PORT}')
    print('Reporter:    http://%s:%s/' % (HOST, PORT))
    print('Control:     http://%s:%s/control-room' % (HOST, PORT))
    print('Responder:   http://%s:%s/responder' % (HOST, PORT))
    print('Hospital:    http://%s:%s/hospital' % (HOST, PORT))
    if twilio_credentials_ready():
        print('Control call: ENABLED (Twilio credentials detected)')
    else:
        print('Control call: NOT CONFIGURED (see .env.example)')
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping RESQ...')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

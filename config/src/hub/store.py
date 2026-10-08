"""Transactional local persistence; evidence paths are generated internally."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'evidence').mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.path = self.root / 'hub.sqlite3'
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        with self.transaction() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cameras (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS camera_credentials (camera_id TEXT PRIMARY KEY, username TEXT NOT NULL, password TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, created REAL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS manifests (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, created REAL, actor TEXT, action TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY, salt TEXT, digest TEXT, role TEXT);
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, username TEXT, expires REAL);
            ''')

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def audit(self, db, actor, action, body):
        db.execute('INSERT INTO audit(created,actor,action,body) VALUES(?,?,?,?)',
                   (time.time(), actor, action, json.dumps(body, allow_nan=False)))

    def cameras(self):
        with self.transaction() as db:
            return [json.loads(r['body']) for r in db.execute('SELECT body FROM cameras ORDER BY id')]

    def camera_credentials(self, camera_id):
        with self.transaction() as db:
            row = db.execute('SELECT username,password FROM camera_credentials WHERE camera_id=?', (camera_id,)).fetchone()
            return dict(row) if row else None

    def save_camera(self, camera, actor, credentials=None, clear_credentials=False):
        with self.transaction() as db:
            exists = db.execute('SELECT 1 FROM cameras WHERE id=?', (camera['camera_id'],)).fetchone()
            if not exists and db.execute('SELECT COUNT(*) FROM cameras').fetchone()[0] >= 4:
                raise ValueError('Pilot supports a maximum of four cameras')
            db.execute('INSERT OR REPLACE INTO cameras VALUES(?,?)', (camera['camera_id'], json.dumps(camera)))
            if credentials is not None:
                db.execute('INSERT OR REPLACE INTO camera_credentials VALUES(?,?,?)',
                           (camera['camera_id'], credentials['username'], credentials['password']))
            elif clear_credentials:
                db.execute('DELETE FROM camera_credentials WHERE camera_id=?', (camera['camera_id'],))
            self.audit(db, actor, 'camera_saved', {'camera_id': camera['camera_id'],
                       'credentials_changed': credentials is not None or clear_credentials})

    def delete_camera(self, camera_id, actor):
        with self.transaction() as db:
            db.execute('DELETE FROM cameras WHERE id=?', (camera_id,))
            db.execute('DELETE FROM camera_credentials WHERE camera_id=?', (camera_id,))
            self.audit(db, actor, 'camera_deleted', {'camera_id': camera_id})

    def incident(self, body, actor='capture'):
        body = dict(body)
        body['event_id'] = body.get('event_id') or str(uuid.uuid4())
        # Callers cannot inject evidence file paths.
        body['created_at'] = time.time()
        body.pop('evidence', None)
        body['evidence_status'] = 'pending'
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM incidents WHERE id=?', (body['event_id'],)).fetchone():
                raise ValueError('Incident already exists')
            db.execute('INSERT INTO incidents VALUES(?,?,?)', (body['event_id'], time.time(), json.dumps(body, allow_nan=False)))
            self.audit(db, actor, 'incident_created', {'event_id': body['event_id']})
        return body

    def incidents(self, limit=100):
        with self.transaction() as db:
            return [json.loads(r['body']) for r in db.execute('SELECT body FROM incidents ORDER BY created DESC LIMIT ?', (limit,))]

    def get_incident(self, event_id):
        with self.transaction() as db:
            row = db.execute('SELECT body FROM incidents WHERE id=?', (event_id,)).fetchone()
            return json.loads(row['body']) if row else None

    def update_incident(self, event_id, changes, actor='capture'):
        with self.transaction() as db:
            row = db.execute('SELECT body FROM incidents WHERE id=?', (event_id,)).fetchone()
            if not row:
                raise KeyError(event_id)
            body = json.loads(row['body'])
            body.update(changes)
            db.execute('UPDATE incidents SET body=? WHERE id=?', (json.dumps(body, allow_nan=False), event_id))
            self.audit(db, actor, 'incident_updated', {'event_id': event_id, 'fields': list(changes)})
        return body

    def draft(self, body, actor):
        mid = str(uuid.uuid4())
        body = {**body, 'manifest_id': mid, 'status': 'DRAFT', 'created_by': actor}
        with self.transaction() as db:
            db.execute('INSERT INTO manifests VALUES(?,?)', (mid, json.dumps(body)))
            self.audit(db, actor, 'manifest_drafted', {'manifest_id': mid})
        return body

    def manifests(self):
        with self.transaction() as db:
            return [json.loads(r['body']) for r in db.execute('SELECT body FROM manifests')]

    def approve(self, mid, actor):
        with self.transaction() as db:
            row = db.execute('SELECT body FROM manifests WHERE id=?', (mid,)).fetchone()
            if not row:
                raise KeyError(mid)
            body = json.loads(row['body'])
            camera = db.execute('SELECT body FROM cameras WHERE id=?', (body['camera_id'],)).fetchone()
            if not camera:
                raise ValueError('Camera no longer exists')
            from src.hub.models import Camera
            config = json.loads(camera['body'])
            config['zones'] = body['zones']
            Camera.model_validate(config)
            body.update(status='APPROVED', approved_by=actor, approved_at=time.time())
            db.execute('UPDATE cameras SET body=? WHERE id=?', (json.dumps(config), body['camera_id']))
            db.execute('UPDATE manifests SET body=? WHERE id=?', (json.dumps(body), mid))
            self.audit(db, actor, 'manifest_approved', {'manifest_id': mid})
        return body

    def add_user(self, name, password, role):
        if not name or len(name) > 80 or role not in ('administrator', 'operator', 'viewer') or len(password) < 12:
            raise ValueError('Use a valid role and a password of at least 12 characters')
        salt = secrets.token_hex(16)
        digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO users VALUES(?,?,?,?)', (name, salt, digest, role))
            db.execute('DELETE FROM sessions WHERE username=?', (name,))
            self.audit(db, 'local-admin', 'user_saved', {'username': name, 'role': role})

    def login(self, name, password):
        with self.transaction() as db:
            user = db.execute('SELECT * FROM users WHERE name=?', (name,)).fetchone()
            # Same expensive password operation for unknown users.
            salt = user['salt'] if user else '0' * 32
            digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
            if not user or not hmac.compare_digest(digest, user['digest']):
                return None
            token = secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), name, time.time() + 28800))
            return token

    def user(self, token):
        with self.transaction() as db:
            row = db.execute('SELECT u.name,u.role FROM sessions s JOIN users u ON u.name=s.username WHERE s.token=? AND s.expires>?',
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.transaction() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def backup(self, destination):
        destination = Path(destination)
        destination.touch(mode=0o600, exist_ok=True)
        destination.chmod(0o600)
        with self.transaction() as db:
            target = sqlite3.connect(destination)
            try:
                db.backup(target)
            finally:
                target.close()

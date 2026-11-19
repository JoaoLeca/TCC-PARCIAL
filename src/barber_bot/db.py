import sqlite3
from pathlib import Path
from barber_bot.config import DATABASE_PATH

BARBERS = [
    ("Rafael", [1, 2, 3, 4, 5]),
    ("Caetano", [0, 1, 3, 4, 5]),
    ("Alecio", [0, 1, 2, 3, 4]),
]
SERVICES = [
    ("Corte de Cabelo", 50.0, 40),
    ("Cabelo + Barba", 80.0, 70),
    ("Cabelo + Barba + Sobrancelha", 100.0, 90),
    ("Pintar o Cabelo", 200.0, 120),
]
PACKAGES = [
    ("Cabelo 2x no mês", 90.0, "Dois cortes de cabelo no mês."),
    ("Cabelo + Barba 2x no mês", 145.0, "Dois atendimentos de cabelo + barba no mês."),
]


def phone_key(value: str) -> str:
    value = value.strip()
    digits = value.translate(str.maketrans("", "", " ()+.-\t\r\n"))
    return digits if digits.isascii() and digits.isdecimal() else value


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else DATABASE_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.create_function("phone_key", 1, phone_key, deterministic=True)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def initialize_database(path: str | Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS barbers (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            working_days TEXT NOT NULL, telegram_phone TEXT NOT NULL DEFAULT '',
            telegram_chat_id TEXT
        );
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            price REAL NOT NULL CHECK(price >= 0), duration_minutes INTEGER NOT NULL CHECK(duration_minutes > 0)
        );
        CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            price REAL NOT NULL CHECK(price >= 0), description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY, customer_name TEXT NOT NULL, customer_phone TEXT NOT NULL,
            barber_id INTEGER NOT NULL REFERENCES barbers(id), service_id INTEGER NOT NULL REFERENCES services(id),
            starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
            price_at_booking REAL,
            status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN ('confirmed','cancelled')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_appointments_time ON appointments(starts_at, ends_at, barber_id, status);
        CREATE TABLE IF NOT EXISTS appointment_access (
            appointment_id INTEGER PRIMARY KEY REFERENCES appointments(id) ON DELETE CASCADE,
            code_hash TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS booking_owners (
            appointment_id INTEGER PRIMARY KEY REFERENCES appointments(id) ON DELETE CASCADE,
            channel TEXT NOT NULL CHECK(channel IN ('web', 'telegram')),
            owner_hash TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_booking_owners ON booking_owners(channel, owner_hash);
        CREATE TABLE IF NOT EXISTS booking_access_grants (
            appointment_id INTEGER NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
            channel TEXT NOT NULL CHECK(channel IN ('web','telegram')),
            owner_hash TEXT NOT NULL, expires_at INTEGER NOT NULL,
            PRIMARY KEY(appointment_id,channel,owner_hash)
        );
        CREATE TABLE IF NOT EXISTS customer_recovery (
            challenge_hash TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, phone TEXT NOT NULL,
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
            code_hash TEXT, code_expires_at INTEGER, telegram_chat_id TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, consumed_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_customer_recovery_owner ON customer_recovery(owner_hash,created_at);
        CREATE INDEX IF NOT EXISTS idx_customer_recovery_phone ON customer_recovery(phone,created_at);
        CREATE TABLE IF NOT EXISTS customer_recovery_flows (
            owner_hash TEXT PRIMARY KEY, stage TEXT NOT NULL, challenge_id TEXT,
            requested_action TEXT NOT NULL DEFAULT '', expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS booking_conversations (
            channel TEXT NOT NULL CHECK(channel IN ('web', 'telegram')),
            owner_key_hash TEXT NOT NULL,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(channel, owner_key_hash)
        );
        CREATE TABLE IF NOT EXISTS staff_accounts (
            username TEXT PRIMARY KEY,
            password_hash BLOB NOT NULL,
            salt BLOB NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('owner', 'barber')),
            barber_id INTEGER REFERENCES barbers(id),
            CHECK((role='owner' AND barber_id IS NULL) OR (role='barber' AND barber_id IS NOT NULL))
        );
        CREATE TABLE IF NOT EXISTS staff_sessions (
            token_hash BLOB PRIMARY KEY,
            username TEXT NOT NULL REFERENCES staff_accounts(username) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS telegram_sessions (
            chat_id TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS schedule_blocks (
            id INTEGER PRIMARY KEY,
            barber_id INTEGER NOT NULL REFERENCES barbers(id),
            starts_at TEXT NOT NULL,
            ends_at TEXT NOT NULL CHECK(ends_at > starts_at),
            reason TEXT NOT NULL,
            released_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_schedule_blocks ON schedule_blocks(barber_id, starts_at, ends_at);
        CREATE TABLE IF NOT EXISTS shop_profile (
            id INTEGER PRIMARY KEY CHECK(id=1),
            name TEXT NOT NULL, address TEXT NOT NULL, phone TEXT NOT NULL, payment_methods TEXT NOT NULL,
            open_1 TEXT NOT NULL, close_1 TEXT NOT NULL, open_2 TEXT, close_2 TEXT
        );
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY, customer_name TEXT, customer_phone TEXT,
            reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS interaction_logs (
            id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            user_message TEXT NOT NULL, intent TEXT, tools_called TEXT, outcome TEXT, latency_ms INTEGER
        );
        CREATE TABLE IF NOT EXISTS support_notes (
            id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
            author TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS booking_requests (
            request_hash TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
            appointment_id INTEGER NOT NULL REFERENCES appointments(id) ON DELETE CASCADE
        );
        """)
        conn.execute("BEGIN IMMEDIATE")
        support_columns = {column["name"] for column in conn.execute("PRAGMA table_info(support_tickets)")}
        for name, definition in {
            "session_id": "TEXT", "channel": "TEXT NOT NULL DEFAULT 'legacy'",
            "assigned_to": "TEXT", "updated_at": "TEXT", "resolved_at": "TEXT",
        }.items():
            if name not in support_columns:
                conn.execute(f"ALTER TABLE support_tickets ADD COLUMN {name} {definition}")
        conn.execute("UPDATE support_tickets SET updated_at=created_at WHERE updated_at IS NULL")
        conn.execute("UPDATE support_tickets SET status='resolved' WHERE status='closed'")
        conn.execute("UPDATE support_tickets SET resolved_at=updated_at WHERE status='resolved' AND resolved_at IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_support_queue ON support_tickets(status, assigned_to, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_support_session ON support_tickets(session_id, channel, status)")
        if not any(column["name"] == "channel" for column in conn.execute("PRAGMA table_info(interaction_logs)")):
            conn.execute("ALTER TABLE interaction_logs ADD COLUMN channel TEXT NOT NULL DEFAULT 'web'")
        if not any(column["name"] == "released_at" for column in conn.execute("PRAGMA table_info(schedule_blocks)")):
            conn.execute("ALTER TABLE schedule_blocks ADD COLUMN released_at TEXT")
        barber_columns = {column["name"] for column in conn.execute("PRAGMA table_info(barbers)")}
        if "telegram_phone" not in barber_columns:
            conn.execute("ALTER TABLE barbers ADD COLUMN telegram_phone TEXT NOT NULL DEFAULT ''")
        if "telegram_chat_id" not in barber_columns:
            conn.execute("ALTER TABLE barbers ADD COLUMN telegram_chat_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_barbers_telegram_phone ON barbers(telegram_phone) WHERE telegram_phone <> ''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_barbers_telegram_chat ON barbers(telegram_chat_id) WHERE telegram_chat_id IS NOT NULL")
        conn.execute("""
            INSERT OR IGNORE INTO shop_profile VALUES
            (1, 'Barbearia Imperial', 'Avenida 330, Ribeirão Preto, SP', '(16) 91111-3322',
             'Dinheiro, Pix e cartão', '08:00', '12:00', '13:30', '20:00')
        """)
        if not any(column["name"] == "price_at_booking" for column in conn.execute("PRAGMA table_info(appointments)")):
            conn.execute("ALTER TABLE appointments ADD COLUMN price_at_booking REAL")
        conn.execute("""
            UPDATE appointments SET price_at_booking=(SELECT price FROM services WHERE id=appointments.service_id)
            WHERE price_at_booking IS NULL
        """)
        conn.execute("UPDATE barbers SET name='Caetano' WHERE name='Diego' AND NOT EXISTS (SELECT 1 FROM barbers WHERE name='Caetano')")
        for name, days in BARBERS:
            conn.execute("INSERT OR IGNORE INTO barbers(name,working_days) VALUES (?,?)", (name, ",".join(map(str, days))))
        for name, price, duration in SERVICES:
            conn.execute("INSERT OR IGNORE INTO services(name,price,duration_minutes) VALUES (?,?,?)", (name, price, duration))
        for name, price, description in PACKAGES:
            conn.execute("INSERT OR IGNORE INTO packages(name,price,description) VALUES (?,?,?)", (name, price, description))


import sqlite3
from pathlib import Path
from barber_bot.config import DATABASE_PATH

BARBERS = [
    ("Rafael", [1, 2, 3, 4, 5]),
    ("Diego", [0, 1, 3, 4, 5]),
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
            working_days TEXT NOT NULL
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
        """)
        conn.execute("BEGIN IMMEDIATE")
        if not any(column["name"] == "released_at" for column in conn.execute("PRAGMA table_info(schedule_blocks)")):
            conn.execute("ALTER TABLE schedule_blocks ADD COLUMN released_at TEXT")
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
        for name, days in BARBERS:
            conn.execute("INSERT OR IGNORE INTO barbers(name,working_days) VALUES (?,?)", (name, ",".join(map(str, days))))
        for name, price, duration in SERVICES:
            conn.execute("INSERT OR IGNORE INTO services(name,price,duration_minutes) VALUES (?,?,?)", (name, price, duration))
        for name, price, description in PACKAGES:
            conn.execute("INSERT OR IGNORE INTO packages(name,price,description) VALUES (?,?,?)", (name, price, description))


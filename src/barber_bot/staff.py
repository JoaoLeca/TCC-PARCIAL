import argparse
import getpass
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from barber_bot.db import connect, initialize_database


SESSION_SECONDS = 8 * 60 * 60
_DUMMY_SALT = secrets.token_bytes(16)


@dataclass(frozen=True)
class StaffMember:
    username: str
    role: str
    barber_id: int | None
    display_name: str


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**16, r=8, p=2, maxmem=96 * 1024**2)


def save_staff_account(username: str, password: str, role: str, barber_id: int | None = None, *, replace=False):
    username = username.strip().lower()
    if not username.isascii() or not username.replace("-", "").replace("_", "").isalnum() or len(username) > 60:
        raise ValueError("Use um usuário de até 60 letras, números, hífens ou sublinhados.")
    if not 12 <= len(password) <= 128:
        raise ValueError("A senha deve ter de 12 a 128 caracteres.")
    salt = secrets.token_bytes(16)
    password_hash = _password_hash(password, salt)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM staff_accounts WHERE username=?", (username,)).fetchone():
            if not replace:
                raise ValueError("Esse usuário já existe. Use --replace para redefinir o acesso.")
            conn.execute("DELETE FROM staff_accounts WHERE username=?", (username,))
        conn.execute("INSERT INTO staff_accounts VALUES (?, ?, ?, ?, ?)",
                     (username, password_hash, salt, role, barber_id))


def authenticate(username: str, password: str) -> str | None:
    username = username.strip().lower()
    with connect() as conn:
        account = conn.execute("SELECT password_hash, salt FROM staff_accounts WHERE username=?", (username,)).fetchone()
    candidate = _password_hash(password, account["salt"] if account else _DUMMY_SALT)
    if not account or not hmac.compare_digest(candidate, account["password_hash"]):
        return None

    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with connect() as conn:
        conn.execute("DELETE FROM staff_sessions WHERE expires_at<=?", (now,))
        # Vincula a sessão à senha verificada, inclusive durante uma redefinição concorrente.
        result = conn.execute("""
            INSERT INTO staff_sessions SELECT ?, username, ? FROM staff_accounts
            WHERE username=? AND password_hash=?
        """, (_token_hash(token), now + SESSION_SECONDS, username, account["password_hash"]))
        return token if result.rowcount else None


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def get_staff_member(token: str) -> StaffMember | None:
    with connect() as conn:
        row = conn.execute("""
            SELECT a.username, a.role, a.barber_id, COALESCE(b.name, a.username) AS display_name
            FROM staff_sessions s JOIN staff_accounts a ON a.username=s.username
            LEFT JOIN barbers b ON b.id=a.barber_id
            WHERE s.token_hash=? AND s.expires_at>?
        """, (_token_hash(token), int(time.time()))).fetchone()
    return StaffMember(**dict(row)) if row else None


def revoke_session(token: str):
    with connect() as conn:
        conn.execute("DELETE FROM staff_sessions WHERE token_hash=?", (_token_hash(token),))


def main():
    parser = argparse.ArgumentParser(description="Cria ou redefine um acesso local da equipe.")
    parser.add_argument("username")
    parser.add_argument("--barber", help="Nome do barbeiro cadastrado. Sem esta opção, cria um acesso de dono.")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    initialize_database()
    barber_id = None
    if args.barber:
        with connect() as conn:
            row = conn.execute("SELECT id FROM barbers WHERE name=? COLLATE NOCASE", (args.barber,)).fetchone()
        if not row:
            parser.error("Barbeiro não encontrado.")
        barber_id = row["id"]
    password = getpass.getpass("Senha (12 a 128 caracteres): ")
    if password != getpass.getpass("Confirme a senha: "):
        parser.error("As senhas não coincidem.")
    try:
        save_staff_account(args.username, password, "barber" if barber_id else "owner", barber_id, replace=args.replace)
    except ValueError as exc:
        parser.error(str(exc))
    print("Acesso da equipe salvo.")


if __name__ == "__main__":
    main()

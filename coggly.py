#!/usr/bin/env python3
"""Coggly - a bench tool for clock and watch regulation.

Records escapement beat error in seconds per day (positive means running fast),
estimates the pendulum length change that corrects a measured rate, and keeps a
per-movement-number service history.

Each workshop is owned by a signed-in watchmaker:

    coggly auth register   create an account (password stored as a PBKDF2 hash)
    coggly auth login      open a session for that account
    coggly auth whoami     who am I
    coggly auth logout     revoke the session, server side

Signing in records a session in that same file (the token is kept as a SHA-256
digest, never in clear); the beat and service commands act only on the signed-in
user's own records. The pendulum calculator reads nothing and needs no account.

Standard library only, one JSON store at ``~/.coggly.json`` (``--data PATH``).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

TOOL = "coggly"
VERSION = "2.0.0"
SCHEMA_VERSION = 2

MAX_ERROR_SPD = 600.0
SECONDS_PER_DAY = 86400.0
MM_PER_INCH = 25.4
ID_WIDTH = 4
OUTPUT_WIDTH = 80

PBKDF2_ITERATIONS = 210_000
SESSION_TTL_DAYS = 90
MIN_PASSWORD_LENGTH = 6

DEMO_EMAIL = "cenius@cenius.ai"
DEMO_USERNAME = "cenius"
DEMO_PASSWORD = "cenius"
DEMO_NAME = "Cenius (demo account)"

EXIT_OK = 0
EXIT_USER = 1          # argparse itself rejects unknown commands with exit code 2

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULT_DATA_PATH = Path.home() / ".coggly.json"

USAGE_BEAT_ADD = "coggly beat add --movement P-42 --error 3.2"
USAGE_SERVICE_ADD = 'coggly service add --movement P-42 --work "full service" --watchmaker "A. Bell"'
PENDULUM_USAGE = "usage: coggly pendulum correct --length MM --error S_PER_DAY"
SIGN_IN_HINT = (
    f"demo account: {DEMO_EMAIL} / {DEMO_PASSWORD} (username {DEMO_USERNAME}) - "
    "or sign up with: coggly auth register --email you@example.com --password-stdin"
)


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class CogglyError(Exception):
    """A user-facing failure: one readable line on stderr, never a traceback."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


def fail(exc: CogglyError) -> int:
    print(f"error: {exc}", file=sys.stderr)
    if exc.hint:
        print(f"hint: {exc.hint}", file=sys.stderr)
    return EXIT_USER


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------


def plural(count: int, singular: str, many: str | None = None) -> str:
    word = singular if count == 1 else (many or f"{singular}s")
    return f"{count} {word}"


def number(value: float, digits: int = 1) -> str:
    """Render a plain number without a leading plus sign (negatives keep the -)."""
    if value == 0:
        value = 0.0  # collapse -0.0
    return f"{value:.{digits}f}"


def direction_word(error_spd: float) -> str:
    if error_spd > 0:
        return "fast"
    if error_spd < 0:
        return "slow"
    return "in rate"


def describe_days(days: int) -> str:
    if days < 0:
        return plural(-days, "day") + " in the future"
    if days == 0:
        return "today"
    return plural(days, "day") + " ago"


def fmt_mm_in(mm: float) -> str:
    if abs(mm) < 5e-9:
        mm = 0.0
    return f"{mm:.2f} mm ({mm / MM_PER_INCH:.3f} in)"


def today_iso() -> str:
    return date.today().isoformat()


def days_since(iso_date: str) -> int:
    return (date.today() - date.fromisoformat(iso_date)).days


def render_table(
    headers: list[str],
    rows: list[list[str]],
    right: tuple[int, ...] = (),
    caps: dict[int, int] | None = None,
) -> list[str]:
    """One aligned block, never wider than OUTPUT_WIDTH; long cells end in '..'."""
    limits = caps or {}
    widths = [
        min(len(header), limits.get(index, OUTPUT_WIDTH)) for index, header in enumerate(headers)
    ]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], min(len(cell), limits.get(index, OUTPUT_WIDTH)))

    # Trim from the right until the widest row fits the terminal page.
    spare = sum(widths) + 2 * (len(widths) - 1) - OUTPUT_WIDTH
    index = len(widths) - 1
    while spare > 0 and index >= 0:
        if widths[index] > 6:
            widths[index] -= 1
            spare -= 1
        else:
            index -= 1

    lines = [_format_row(headers, widths, ()), "  ".join("-" * width for width in widths)]
    lines.extend(_format_row(row, widths, right) for row in rows)
    return lines


def _format_row(cells: list[str], widths: list[int], right: tuple[int, ...]) -> str:
    out = []
    for index, (cell, width) in enumerate(zip(cells, widths)):
        if len(cell) > width:
            cell = cell[: width - 2] + ".." if width > 3 else cell[:width]
        out.append(cell.rjust(width) if index in right else cell.ljust(width))
    return "  ".join(out).rstrip()


def print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)


def print_block(rows: list[tuple[str, str]], indent: str = "") -> None:
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{indent}{label.ljust(width)}  {value}")


# --------------------------------------------------------------------------
# input parsing (one readable message per rejected value)
# --------------------------------------------------------------------------


def parse_error_spd(raw: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise CogglyError(
            f"the beat error must be a number between -{MAX_ERROR_SPD:g} and "
            f"{MAX_ERROR_SPD:g} seconds per day (got {raw!r})"
        ) from None
    if not math.isfinite(value) or abs(value) > MAX_ERROR_SPD:
        raise CogglyError(
            f"the beat error must be between -{MAX_ERROR_SPD:g} and "
            f"{MAX_ERROR_SPD:g} seconds per day (got {raw})"
        )
    return round(value, 1)


def parse_length_mm(raw: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise CogglyError(
            f"the pendulum length must be a number of millimetres greater than zero (got {raw!r})"
        ) from None
    if not math.isfinite(value) or value <= 0:
        raise CogglyError(f"the pendulum length must be greater than zero millimetres (got {raw})")
    return value


def parse_cost(raw: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise CogglyError(f"the cost must be a number of 0 or more (got {raw!r})") from None
    if not math.isfinite(value) or value < 0:
        raise CogglyError(f"the cost must be a number of 0 or more (got {raw})")
    return round(value, 2)


def parse_iso_date(raw: str) -> str:
    if ISO_DATE_RE.match(raw):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            pass
    raise CogglyError(f"the date must be a real date in YYYY-MM-DD form (got {raw!r})")


def require_text(raw: str | None, message: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise CogglyError(message)
    return text


def parse_email(raw: str | None) -> str:
    email = (raw or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise CogglyError(f"the email address is not valid (got {raw!r})")
    return email


def password_from(args: argparse.Namespace, confirm: bool = False) -> str:
    """Read the password from --password-stdin, --password, or a TTY prompt."""
    if args.password_stdin:
        line = sys.stdin.readline()
        if not line:
            raise CogglyError(
                "no password arrived on stdin",
                hint="pipe it in: echo 'my-password' | coggly auth login --email you@example.com --password-stdin",
            )
        return line.rstrip("\n")
    if args.password:
        return args.password
    if sys.stdin.isatty():
        first = getpass.getpass("Password: ")
        if confirm and getpass.getpass("Repeat password: ") != first:
            raise CogglyError("the two passwords did not match")
        return first
    raise CogglyError(
        "a password is required",
        hint="pass --password-stdin (echo 'my-password' | coggly auth login ...) or --password",
    )


def check_password_length(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise CogglyError(f"the password must be at least {MIN_PASSWORD_LENGTH} characters long")
    return password


# --------------------------------------------------------------------------
# accounts: hashing, users, sessions
# --------------------------------------------------------------------------


def hash_password(password: str) -> tuple[str, str]:
    """Return (salt_hex, digest_hex) for a fresh per-user salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(candidate.hex(), expected_hex)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def session_expiry() -> str:
    return (datetime.now() + timedelta(days=SESSION_TTL_DAYS)).isoformat(timespec="seconds")


@dataclass
class User:
    id: str
    email: str
    username: str
    display_name: str
    password_salt: str
    password_hash: str
    role: str
    created_at: str

    @property
    def label(self) -> str:
        if self.display_name:
            return f"{self.display_name} <{self.email}>"
        return self.email

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "display_name": self.display_name,
            "password_salt": self.password_salt,
            "password_hash": self.password_hash,
            "role": self.role,
            "created_at": self.created_at,
        }


@dataclass
class Session:
    token_hash: str
    user_id: str
    created_at: str
    expires_at: str

    def expired(self) -> bool:
        try:
            return datetime.fromisoformat(self.expires_at) <= datetime.now()
        except ValueError:
            return True

    def as_dict(self) -> dict:
        return {
            "token_hash": self.token_hash,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


# --------------------------------------------------------------------------
# store records
# --------------------------------------------------------------------------


@dataclass
class BeatEntry:
    id: str
    owner: str
    movement: str
    recorded_on: str
    error_spd: float
    note: str = ""

    def as_dict(self) -> dict:
        data = {
            "id": self.id,
            "owner": self.owner,
            "movement": self.movement,
            "recorded_on": self.recorded_on,
            "error_spd": self.error_spd,
        }
        if self.note:
            data["note"] = self.note
        return data


@dataclass
class ServiceRecord:
    id: str
    owner: str
    movement: str
    service_on: str
    work: str
    watchmaker: str
    cost: float | None = None
    note: str = ""

    def as_dict(self) -> dict:
        data = {
            "id": self.id,
            "owner": self.owner,
            "movement": self.movement,
            "service_on": self.service_on,
            "work": self.work,
            "watchmaker": self.watchmaker,
        }
        if self.cost is not None:
            data["cost"] = self.cost
        if self.note:
            data["note"] = self.note
        return data


@dataclass
class Store:
    users: list[User] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    beat_entries: list[BeatEntry] = field(default_factory=list)
    service_records: list[ServiceRecord] = field(default_factory=list)
    next_ids: dict[str, int] = field(default_factory=dict)
    client_token: str = ""

    def as_dict(self) -> dict:
        data = {
            "schema_version": SCHEMA_VERSION,
            "users": [user.as_dict() for user in self.users],
            "sessions": [session.as_dict() for session in self.sessions],
            "beat_entries": [entry.as_dict() for entry in self.beat_entries],
            "service_records": [record.as_dict() for record in self.service_records],
            "next_ids": {
                "beat": self._next_id_value("beat"),
                "service": self._next_id_value("service"),
                "user": self._next_id_value("user"),
            },
        }
        if self.client_token:
            data["client_token"] = self.client_token
        return data

    def rows_for(self, kind: str) -> list:
        if kind == "beat":
            return self.beat_entries
        if kind == "service":
            return self.service_records
        if kind == "session":
            return self.sessions
        return self.users

    def _next_id_value(self, kind: str) -> int:
        existing = self._highest_existing_id(kind)
        return max(self.next_ids.get(kind, 1), existing + 1)

    def _highest_existing_id(self, kind: str) -> int:
        highest = 0
        for row in self.rows_for(kind):
            digits = "".join(char for char in getattr(row, "id", "") if char.isdigit())
            if digits:
                highest = max(highest, int(digits))
        return highest

    def allocate_id(self, kind: str) -> str:
        """Allocate the next short id; ids are never handed out twice."""
        value = self._next_id_value(kind)
        self.next_ids[kind] = value + 1
        prefix = "u" if kind == "user" else ""
        return f"{prefix}{value:0{ID_WIDTH}d}"


# --------------------------------------------------------------------------
# store I/O
# --------------------------------------------------------------------------


def data_path_from(args: argparse.Namespace) -> Path:
    raw = getattr(args, "data", None)
    if not raw:
        return DEFAULT_DATA_PATH
    return Path(raw).expanduser()


def corrupt_store(path: Path, why: str) -> CogglyError:
    return CogglyError(
        f"the data file {path} {why}",
        hint=f"fix it or move it aside (mv {path} {path}.bak) - Coggly will not overwrite it",
    )


def load_store(path: Path) -> Store:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return fresh_store()
    except OSError as exc:
        raise CogglyError(
            f"cannot read the data file {path} ({exc.strerror or exc})",
            hint="check the file permissions, or pass --data PATH to use another file",
        ) from None
    if not raw.strip():
        return fresh_store()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise corrupt_store(path, f"is not valid JSON ({exc.msg} at line {exc.lineno})") from None

    legacy = isinstance(data, dict) and data.get("schema_version") == 1
    store = store_from_dict(data, path)
    demo = seed_demo_user(store)
    adopted = adopt_orphan_rows(store, demo)
    if legacy or adopted:
        save_store(path, store)
    return store


def fresh_store() -> Store:
    """An empty store that already carries the seeded demo account (in memory)."""
    store = Store()
    seed_demo_user(store)
    return store


def store_from_dict(data: object, path: Path) -> Store:
    if not isinstance(data, dict):
        raise corrupt_store(path, "is not a Coggly data file (expected a JSON object)")
    version = data.get("schema_version")
    if not isinstance(version, int):
        raise corrupt_store(path, "has no schema_version - it is not a Coggly data file")
    if version > SCHEMA_VERSION:
        raise corrupt_store(
            path,
            f"uses schema_version {version}; this copy of Coggly understands {SCHEMA_VERSION}",
        )

    store = Store()
    for kind, key, builder in (
        ("user", "users", user_from_dict),
        ("session", "sessions", session_from_dict),
        ("beat", "beat_entries", beat_entry_from_dict),
        ("service", "service_records", service_record_from_dict),
    ):
        rows = data.get(key, [])
        if not isinstance(rows, list):
            raise corrupt_store(path, f"has {key} that is not a list")
        target = store.rows_for(kind)
        for row in rows:
            target.append(builder(row, path))

    token = data.get("client_token")
    store.client_token = token.strip() if isinstance(token, str) else ""
    counters = data.get("next_ids")
    if isinstance(counters, dict):
        store.next_ids = {
            kind: value
            for kind, value in counters.items()
            if isinstance(kind, str) and isinstance(value, int) and value > 0
        }
    return store


def _row_dict(row: object, path: Path, kind: str) -> dict:
    if not isinstance(row, dict):
        raise corrupt_store(path, f"has a {kind} entry that is not a JSON object")
    return row


def user_from_dict(row: object, path: Path) -> User:
    data = _row_dict(row, path, "user")
    try:
        return User(
            id=str(data["id"]),
            email=str(data["email"]).lower(),
            username=str(data.get("username") or ""),
            display_name=str(data.get("display_name") or ""),
            password_salt=str(data["password_salt"]),
            password_hash=str(data["password_hash"]),
            role=str(data.get("role") or "watchmaker"),
            created_at=str(data.get("created_at") or now_iso()),
        )
    except KeyError as exc:
        raise corrupt_store(path, f"has a user without a {exc.args[0]} field") from None


def session_from_dict(row: object, path: Path) -> Session:
    data = _row_dict(row, path, "session")
    try:
        return Session(
            token_hash=str(data["token_hash"]),
            user_id=str(data["user_id"]),
            created_at=str(data.get("created_at") or now_iso()),
            expires_at=str(data["expires_at"]),
        )
    except KeyError as exc:
        raise corrupt_store(path, f"has a session without a {exc.args[0]} field") from None


def beat_entry_from_dict(row: object, path: Path) -> BeatEntry:
    data = _row_dict(row, path, "beat")
    try:
        return BeatEntry(
            id=str(data["id"]),
            owner=str(data.get("owner") or ""),
            movement=str(data["movement"]),
            recorded_on=parse_iso_date(str(data["recorded_on"])),
            error_spd=float(data["error_spd"]),
            note=str(data.get("note") or ""),
        )
    except KeyError as exc:
        raise corrupt_store(path, f"has a beat entry without a {exc.args[0]} field") from None
    except (TypeError, ValueError) as exc:
        raise corrupt_store(path, f"has a beat entry with a bad value ({exc})") from None


def service_record_from_dict(row: object, path: Path) -> ServiceRecord:
    data = _row_dict(row, path, "service")
    try:
        return ServiceRecord(
            id=str(data["id"]),
            owner=str(data.get("owner") or ""),
            movement=str(data["movement"]),
            service_on=parse_iso_date(str(data["service_on"])),
            work=str(data["work"]),
            watchmaker=str(data.get("watchmaker") or ""),
            cost=None if data.get("cost") is None else float(data["cost"]),
            note=str(data.get("note") or ""),
        )
    except KeyError as exc:
        raise corrupt_store(path, f"has a service record without a {exc.args[0]} field") from None
    except (TypeError, ValueError) as exc:
        raise corrupt_store(path, f"has a service record with a bad value ({exc})") from None


def save_store(path: Path, store: Store) -> None:
    """Write the whole store through a temp file plus os.replace (atomic)."""
    payload = json.dumps(store.as_dict(), indent=2, ensure_ascii=False) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CogglyError(f"cannot create the directory {path.parent} ({exc.strerror or exc})") from None
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".coggly-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    except OSError as exc:
        raise CogglyError(
            f"cannot write the data file {path} ({exc.strerror or exc})",
            hint="check the directory exists and is writable, or pass --data PATH",
        ) from None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


# --------------------------------------------------------------------------
# the seeded demo account, user lookup and session resolution
# --------------------------------------------------------------------------


def seed_demo_user(store: Store) -> User:
    """Idempotently make the demo watchmaker exist, hashed like any other user."""
    for user in store.users:
        if user.email == DEMO_EMAIL:
            return user
    salt, digest = hash_password(DEMO_PASSWORD)
    demo = User(
        id=store.allocate_id("user"),
        email=DEMO_EMAIL,
        username=DEMO_USERNAME,
        display_name=DEMO_NAME,
        password_salt=salt,
        password_hash=digest,
        role="owner",
        created_at=now_iso(),
    )
    store.users.append(demo)
    return demo


def adopt_orphan_rows(store: Store, demo: User) -> bool:
    """Records written before accounts existed belong to the seeded demo user."""
    changed = False
    for row in [*store.beat_entries, *store.service_records]:
        if not row.owner:
            row.owner = demo.id
            changed = True
    return changed


def find_user_by_login(store: Store, login: str) -> User | None:
    wanted = login.strip().lower()
    for user in store.users:
        if user.email == wanted or (user.username and user.username == wanted):
            return user
    return None


def find_user_by_id(store: Store, user_id: str) -> User | None:
    for user in store.users:
        if user.id == user_id:
            return user
    return None


def active_session(store: Store) -> tuple[Session, User] | None:
    token = store.client_token
    if not token:
        return None
    digest = token_digest(token)
    for session in store.sessions:
        if hmac.compare_digest(session.token_hash, digest):
            user = find_user_by_id(store, session.user_id)
            if user is None:
                return None
            return session, user
    return None


def not_signed_in() -> CogglyError:
    return CogglyError(
        "not signed in - run: coggly auth login --email you@example.com",
        hint=SIGN_IN_HINT,
    )


def require_user(store: Store) -> User:
    found = active_session(store)
    if found is None:
        raise not_signed_in()
    session, user = found
    if session.expired():
        raise CogglyError(
            f"your session expired - run: coggly auth login --email {user.email}",
            hint=SIGN_IN_HINT,
        )
    return user


def open_workshop(args: argparse.Namespace) -> tuple[Path, Store, User]:
    path = data_path_from(args)
    store = load_store(path)
    return path, store, require_user(store)


def start_session(store: Store, path: Path, user: User) -> Session:
    store.sessions = [session for session in store.sessions if not session.expired()]
    token = secrets.token_urlsafe(32)
    session = Session(
        token_hash=token_digest(token),
        user_id=user.id,
        created_at=now_iso(),
        expires_at=session_expiry(),
    )
    store.sessions.append(session)
    store.client_token = token
    save_store(path, store)
    return session


def revoke_sessions(store: Store, user_id: str) -> int:
    before = len(store.sessions)
    store.sessions = [session for session in store.sessions if session.user_id != user_id]
    return before - len(store.sessions)


# --------------------------------------------------------------------------
# auth commands
# --------------------------------------------------------------------------


def cmd_auth_register(args: argparse.Namespace) -> int:
    path = data_path_from(args)
    store = load_store(path)
    email = parse_email(args.email)
    if find_user_by_login(store, email) is not None:
        raise CogglyError(
            f"an account already exists for {email}",
            hint=f"sign in with: coggly auth login --email {email}",
        )
    password = check_password_length(password_from(args, confirm=True))
    username = unique_username(store, args.username or email.split("@")[0])
    salt, digest = hash_password(password)
    user = User(
        id=store.allocate_id("user"),
        email=email,
        username=username,
        display_name=(args.name or "").strip(),
        password_salt=salt,
        password_hash=digest,
        role="watchmaker",
        created_at=now_iso(),
    )
    store.users.append(user)
    session = start_session(store, path, user)
    print(f"Registered {user.label} as '{user.username}' ({user.id}) - signed in")
    print(f"session valid until {session.expires_at}")
    return EXIT_OK


def unique_username(store: Store, raw: str) -> str:
    base = re.sub(r"[^a-z0-9._-]+", "-", raw.strip().lower()).strip("-.") or "watchmaker"
    candidate = base
    suffix = 2
    taken = {user.username for user in store.users}
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def cmd_auth_login(args: argparse.Namespace) -> int:
    path = data_path_from(args)
    store = load_store(path)
    login = require_text(
        args.email, "an email or username is required - e.g. --email you@example.com"
    )
    password = password_from(args)
    user = find_user_by_login(store, login)
    if user is None or not verify_password(password, user.password_salt, user.password_hash):
        # One message for both cases: never reveal whether an account exists.
        raise CogglyError(
            "the email or password is not correct",
            hint="new here? create an account with: coggly auth register --email you@example.com",
        )
    session = start_session(store, path, user)
    print(f"Signed in as {user.label} (username {user.username})")
    print(f"session valid until {session.expires_at}")
    return EXIT_OK


def cmd_auth_logout(args: argparse.Namespace) -> int:
    path = data_path_from(args)
    store = load_store(path)
    found = active_session(store)
    if found is None:
        raise CogglyError(
            "not signed in - there is no session to close", hint=SIGN_IN_HINT
        )
    _, user = found
    revoked = revoke_sessions(store, user.id)
    store.client_token = ""
    save_store(path, store)
    print(f"Signed out {user.label} - {plural(revoked, 'session')} revoked")
    return EXIT_OK


def cmd_auth_whoami(args: argparse.Namespace) -> int:
    store = load_store(data_path_from(args))
    user = require_user(store)
    session, _ = active_session(store) or (None, user)
    block = [
        ("Signed in as", user.label),
        ("Username", user.username or "-"),
        ("Role", user.role),
        ("Account", user.id),
    ]
    if session is not None:
        block.append(("Session until", session.expires_at))
    print_block(block)
    return EXIT_OK


# --------------------------------------------------------------------------
# beat commands
# --------------------------------------------------------------------------


def cmd_beat_add(args: argparse.Namespace) -> int:
    path, store, user = open_workshop(args)
    movement = require_text(args.movement, "a movement number is required - e.g. --movement P-42")
    error_spd = parse_error_spd(args.error)
    recorded_on = parse_iso_date(args.date) if args.date else today_iso()
    note = (args.note or "").strip()

    entry = BeatEntry(
        id=store.allocate_id("beat"),
        owner=user.id,
        movement=movement,
        recorded_on=recorded_on,
        error_spd=error_spd,
        note=note,
    )
    store.beat_entries.append(entry)
    save_store(path, store)

    line = (
        f"Added reading {entry.id} - {entry.movement} on {entry.recorded_on}: "
        f"{number(entry.error_spd)} s/day ({direction_word(entry.error_spd)})"
    )
    if entry.note:
        line += f" - note: {entry.note}"
    print(line)
    return EXIT_OK


def user_beat_entries(store: Store, user: User, movement: str | None) -> list[BeatEntry]:
    rows = [entry for entry in store.beat_entries if entry.owner == user.id]
    if movement:
        rows = [entry for entry in rows if entry.movement == movement]
    return sorted(rows, key=lambda entry: (entry.recorded_on, entry.id), reverse=True)


def cmd_beat_list(args: argparse.Namespace) -> int:
    _, store, user = open_workshop(args)
    movement = (args.movement or "").strip() or None
    entries = user_beat_entries(store, user, movement)

    if not entries:
        print(empty_beat_message(movement))
        return EXIT_OK

    show_note = any(entry.note for entry in entries)
    headers = ["ID", "DATE", "MOVEMENT", "S/DAY"] + (["NOTE"] if show_note else [])
    rows = []
    for entry in entries:
        row = [entry.id, entry.recorded_on, entry.movement, number(entry.error_spd)]
        if show_note:
            row.append(entry.note)
        rows.append(row)
    print_lines(render_table(headers, rows, right=(3,), caps={2: 24, 4: 40}))
    scope = f" for {movement}" if movement else ""
    print(f"\n{plural(len(entries), 'reading')}{scope}")
    return EXIT_OK


def empty_beat_message(movement: str | None) -> str:
    if movement:
        return (
            f"No readings for {movement} - add one with: "
            f"coggly beat add --movement {movement} --error 3.2"
        )
    return f"No readings yet - add one with: {USAGE_BEAT_ADD}"


def latest_service(movement: str, store: Store, owner: str) -> ServiceRecord | None:
    rows = [
        record
        for record in store.service_records
        if record.movement == movement and record.owner == owner
    ]
    if not rows:
        return None
    return max(rows, key=lambda record: (record.service_on, record.id))


def cmd_beat_summary(args: argparse.Namespace) -> int:
    _, store, user = open_workshop(args)
    movement = (args.movement or "").strip() or None
    entries = user_beat_entries(store, user, movement)
    if not entries:
        print(empty_beat_message(movement))
        return EXIT_OK

    groups: dict[str, list[BeatEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.movement, []).append(entry)

    blocks = []
    for name in sorted(groups):
        rows = groups[name]
        errors = [entry.error_spd for entry in rows]
        latest = rows[0]
        last_service = latest_service(name, store, user.id)
        block = [
            ("Movement", name),
            ("Readings", str(len(rows))),
            (
                "Latest",
                f"{number(latest.error_spd)} s/day ({direction_word(latest.error_spd)})"
                f" on {latest.recorded_on}",
            ),
            ("Mean", f"{number(sum(errors) / len(errors))} s/day"),
            ("Min", f"{number(min(errors))} s/day"),
            ("Max", f"{number(max(errors))} s/day"),
        ]
        if last_service is None:
            block.append(("Last service", "no service records for this movement"))
        else:
            block.append(
                (
                    "Last service",
                    f"{last_service.service_on}"
                    f" ({describe_days(days_since(last_service.service_on))})",
                )
            )
        blocks.append(block)

    for index, block in enumerate(blocks):
        if index:
            print()
        print_block(block)
    return EXIT_OK


def cmd_beat_remove(args: argparse.Namespace) -> int:
    path, store, user = open_workshop(args)
    wanted = (args.id or "").strip()
    entry = find_beat_entry(store, user, wanted)
    if entry is None:
        raise CogglyError(f"no reading has that id ({wanted})", hint="run: coggly beat list")
    store.beat_entries.remove(entry)
    save_store(path, store)
    print(
        f"Removed reading {entry.id} - {entry.movement} on {entry.recorded_on}: "
        f"{number(entry.error_spd)} s/day ({direction_word(entry.error_spd)})"
    )
    return EXIT_OK


def find_beat_entry(store: Store, user: User, wanted: str) -> BeatEntry | None:
    """Only the signed-in user's readings are ever considered (unowned = missing)."""
    mine = [entry for entry in store.beat_entries if entry.owner == user.id]
    for entry in mine:
        if entry.id == wanted:
            return entry
    if wanted.isdigit():
        for entry in mine:
            if entry.id.isdigit() and int(entry.id) == int(wanted):
                return entry
    return None


# --------------------------------------------------------------------------
# pendulum command (pure calculation: no account needed, nothing is read)
# --------------------------------------------------------------------------


def pendulum_change_mm(length_mm: float, error_spd: float) -> float:
    """dL = -2 * L * (error / 86400); a fast clock calls for a shorter pendulum."""
    return -2.0 * length_mm * (error_spd / SECONDS_PER_DAY)


def cmd_pendulum_correct(args: argparse.Namespace) -> int:
    if args.length is None or args.error is None:
        print(PENDULUM_USAGE, file=sys.stderr)
        print(
            "example: coggly pendulum correct --length 1000 --error 10\n"
            "         -> shorten by 0.23 mm (0.009 in); corrected length 999.77 mm (39.361 in)",
            file=sys.stderr,
        )
        return EXIT_USER

    length = parse_length_mm(args.length)
    error_spd = parse_error_spd(args.error)
    change = pendulum_change_mm(length, error_spd)
    corrected = length + change

    if change == 0:
        change_text = f"none, the clock is in rate: {fmt_mm_in(0.0)}"
    else:
        verb = "shorten" if change < 0 else "lengthen"
        change_text = f"{verb} by {fmt_mm_in(abs(change))}"

    print("Pendulum correction")
    print_block(
        [
            ("current length", fmt_mm_in(length)),
            ("rate error", f"{number(error_spd)} s/day ({direction_word(error_spd)})"),
            ("change", change_text),
            ("corrected length", fmt_mm_in(corrected)),
        ],
        indent="  ",
    )
    print("  regulating nut    turn it DOWN to lengthen, UP to shorten the pendulum")
    return EXIT_OK


# --------------------------------------------------------------------------
# service commands
# --------------------------------------------------------------------------


def cmd_service_add(args: argparse.Namespace) -> int:
    path, store, user = open_workshop(args)
    movement = require_text(args.movement, "a movement number is required - e.g. --movement P-42")
    service_on = parse_iso_date(args.date) if args.date else today_iso()
    work = require_text(args.work, 'the work performed is required - e.g. --work "full service"')
    watchmaker = require_text(
        args.watchmaker, 'the watchmaker is required - e.g. --watchmaker "A. Bell"'
    )
    cost = parse_cost(args.cost) if args.cost is not None else None
    note = (args.note or "").strip()

    record = ServiceRecord(
        id=store.allocate_id("service"),
        owner=user.id,
        movement=movement,
        service_on=service_on,
        work=work,
        watchmaker=watchmaker,
        cost=cost,
        note=note,
    )
    store.service_records.append(record)
    save_store(path, store)

    line = (
        f"Added service record {record.id} - {record.movement} on {record.service_on}: "
        f"{record.work} ({record.watchmaker})"
    )
    if record.cost is not None:
        line += f" - cost {record.cost:.2f}"
    if record.note:
        line += f" - note: {record.note}"
    print(line)
    return EXIT_OK


def movement_service_records(store: Store, user: User, movement: str) -> list[ServiceRecord]:
    rows = [
        record
        for record in store.service_records
        if record.movement == movement and record.owner == user.id
    ]
    return sorted(rows, key=lambda record: (record.service_on, record.id))


def cmd_service_list(args: argparse.Namespace) -> int:
    _, store, user = open_workshop(args)
    movement = require_text(args.movement, "a movement number is required - e.g. --movement P-42")
    records = movement_service_records(store, user, movement)
    if not records:
        print(
            f"No service records for {movement} - add one with: "
            f'coggly service add --movement {movement} --work "full service"'
        )
        return EXIT_OK

    show_cost = any(record.cost is not None for record in records)
    show_note = any(record.note for record in records)
    headers = ["ID", "DATE", "ELAPSED", "WORK", "WATCHMAKER"]
    if show_cost:
        headers.append("COST")
    if show_note:
        headers.append("NOTE")

    rows = []
    for record in records:
        row = [
            record.id,
            record.service_on,
            describe_days(days_since(record.service_on)).replace(" ago", ""),
            record.work,
            record.watchmaker or "-",
        ]
        if show_cost:
            row.append("-" if record.cost is None else f"{record.cost:.2f}")
        if show_note:
            row.append(record.note)
        rows.append(row)
    print_lines(render_table(headers, rows, caps={3: 40, 4: 24}))
    print()
    latest = records[-1]
    print(
        f"Latest service {latest.service_on} ({describe_days(days_since(latest.service_on))})"
        f" - {plural(len(records), 'record')} for {movement}"
    )
    return EXIT_OK


def cmd_service_movements(args: argparse.Namespace) -> int:
    _, store, user = open_workshop(args)
    mine = [record for record in store.service_records if record.owner == user.id]
    if not mine:
        print(f"No service records yet - add one with: {USAGE_SERVICE_ADD}")
        return EXIT_OK

    by_movement: dict[str, list[ServiceRecord]] = {}
    for record in mine:
        by_movement.setdefault(record.movement, []).append(record)

    headers = ["MOVEMENT", "RECORDS", "LAST SERVICE", "DAYS SINCE"]
    rows = []
    for movement in sorted(by_movement):
        records = by_movement[movement]
        latest = max(records, key=lambda record: (record.service_on, record.id))
        rows.append(
            [
                movement,
                str(len(records)),
                latest.service_on,
                describe_days(days_since(latest.service_on)).replace(" ago", ""),
            ]
        )
    print_lines(render_table(headers, rows, right=(1,), caps={0: 24}))
    print(f"\n{plural(len(rows), 'movement')}, {plural(len(mine), 'record')}")
    return EXIT_OK


# --------------------------------------------------------------------------
# command surface
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="use this data file instead of ~/.coggly.json",
    )
    credentials = argparse.ArgumentParser(add_help=False)
    credentials.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin (keeps it out of shell history)",
    )
    credentials.add_argument("--password", metavar="TEXT", help="the password (visible in ps/history)")

    parser = argparse.ArgumentParser(
        prog=TOOL,
        description=(
            "Coggly - clock beat error, pendulum correction and service history, "
            "for a signed-in watchmaker."
        ),
        epilog=(
            "examples:\n"
            f"  coggly auth login --email {DEMO_EMAIL} --password {DEMO_PASSWORD}\n"
            f"  {USAGE_BEAT_ADD}\n"
            "  coggly pendulum correct --length 1000 --error 10\n"
            f"  {USAGE_SERVICE_ADD}\n"
            "\ndata lives in ~/.coggly.json; pass --data PATH to use another file"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    parser.add_argument("--version", action="version", version=f"{TOOL} {VERSION}")
    parser.add_argument(
        "--data",
        metavar="PATH",
        default=None,
        help="use this data file instead of ~/.coggly.json",
    )

    groups = parser.add_subparsers(dest="group", metavar="{auth,beat,pendulum,service}")

    auth = groups.add_parser(
        "auth",
        parents=[common],
        help="create an account, sign in, sign out",
        description="Accounts for this workshop. Passwords are stored as PBKDF2 hashes.",
    )
    auth_commands = auth.add_subparsers(dest="command", metavar="{register,login,logout,whoami}")

    auth_register = auth_commands.add_parser(
        "register",
        parents=[common, credentials],
        help="create an account and sign in",
        description="Create a watchmaker account. The password is hashed, never stored.",
    )
    auth_register.add_argument("--email", metavar="EMAIL", help="the account's email address")
    auth_register.add_argument("--username", metavar="NAME", help="username (default: from the email)")
    auth_register.add_argument("--name", metavar="TEXT", help="display name")
    auth_register.set_defaults(func=cmd_auth_register)

    auth_login = auth_commands.add_parser(
        "login",
        parents=[common, credentials],
        help="sign in and open a session",
        epilog=(
            f"demo account: {DEMO_EMAIL} / {DEMO_PASSWORD} (username {DEMO_USERNAME})\n"
            "example: coggly auth login --email cenius@cenius.ai --password cenius"
        ),
    )
    auth_login.add_argument("--email", metavar="EMAIL", help="email address or username")
    auth_login.set_defaults(func=cmd_auth_login)

    auth_logout = auth_commands.add_parser(
        "logout", parents=[common], help="revoke the current session"
    )
    auth_logout.set_defaults(func=cmd_auth_logout)

    auth_whoami = auth_commands.add_parser(
        "whoami", parents=[common], help="show who is signed in"
    )
    auth_whoami.set_defaults(func=cmd_auth_whoami)

    beat = groups.add_parser(
        "beat",
        parents=[common],
        help="log escapement beat error in seconds per day",
        description="Record, review and prune beat-error readings (positive = running fast).",
    )
    beat_commands = beat.add_subparsers(dest="command", metavar="{add,list,summary,remove}")

    beat_add = beat_commands.add_parser(
        "add", parents=[common], help="record a reading for a movement"
    )
    beat_add.add_argument("--movement", metavar="NO", help="movement number, e.g. P-42")
    beat_add.add_argument("--error", metavar="S_PER_DAY", help="beat error in seconds per day")
    beat_add.add_argument("--date", metavar="YYYY-MM-DD", help="reading date (default: today)")
    beat_add.add_argument("--note", metavar="TEXT", help="optional note")
    beat_add.set_defaults(func=cmd_beat_add)

    beat_list = beat_commands.add_parser(
        "list", parents=[common], help="list readings newest first"
    )
    beat_list.add_argument("--movement", metavar="NO", help="only this movement number")
    beat_list.set_defaults(func=cmd_beat_list)

    beat_summary = beat_commands.add_parser(
        "summary", parents=[common], help="latest, count, mean, min and max per movement"
    )
    beat_summary.add_argument("--movement", metavar="NO", help="only this movement number")
    beat_summary.set_defaults(func=cmd_beat_summary)

    beat_remove = beat_commands.add_parser(
        "remove", parents=[common], help="delete one reading by id"
    )
    beat_remove.add_argument("id", metavar="ID", help="reading id, as shown by beat list")
    beat_remove.set_defaults(func=cmd_beat_remove)

    pendulum = groups.add_parser(
        "pendulum",
        parents=[common],
        help="estimate the pendulum length change for a measured rate",
        description=(
            "Turn a rate error into a pendulum length adjustment (millimetres, inches "
            "alongside). Needs no account: it reads and writes nothing."
        ),
    )
    pendulum_commands = pendulum.add_subparsers(dest="command", metavar="{correct}")
    pendulum_correct = pendulum_commands.add_parser(
        "correct", parents=[common], help="length change for a length and rate error"
    )
    pendulum_correct.add_argument("--length", metavar="MM", help="current pendulum length in mm")
    pendulum_correct.add_argument(
        "--error", metavar="S_PER_DAY", help="measured error in seconds per day"
    )
    pendulum_correct.set_defaults(func=cmd_pendulum_correct)

    service = groups.add_parser(
        "service",
        parents=[common],
        help="keep a service history per movement number",
        description="Add and review service records per movement number, oldest first.",
    )
    service_commands = service.add_subparsers(dest="command", metavar="{add,list,movements}")

    service_add = service_commands.add_parser(
        "add", parents=[common], help="record a service for a movement"
    )
    service_add.add_argument("--movement", metavar="NO", help="movement number, e.g. P-42")
    service_add.add_argument("--date", metavar="YYYY-MM-DD", help="service date (default: today)")
    service_add.add_argument("--work", metavar="TEXT", help='work performed, e.g. "full service"')
    service_add.add_argument("--watchmaker", metavar="NAME", help="who did the work")
    service_add.add_argument("--cost", metavar="AMOUNT", help="optional cost")
    service_add.add_argument("--note", metavar="TEXT", help="optional note")
    service_add.set_defaults(func=cmd_service_add)

    service_list = service_commands.add_parser(
        "list", parents=[common], help="one movement's records, oldest first"
    )
    service_list.add_argument("--movement", metavar="NO", help="movement number, e.g. P-42")
    service_list.set_defaults(func=cmd_service_list)

    service_movements = service_commands.add_parser(
        "movements", parents=[common], help="every movement with a record and its record count"
    )
    service_movements.set_defaults(func=cmd_service_movements)

    parser.group_parsers = {  # type: ignore[attr-defined]
        "auth": auth,
        "beat": beat,
        "pendulum": pendulum,
        "service": service,
    }
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "group", None):
        parser.print_help()
        return EXIT_OK
    if not getattr(args, "command", None):
        # `coggly beat` on its own: show that group's help rather than failing.
        group_parser = getattr(parser, "group_parsers", {}).get(args.group)
        (group_parser or parser).print_help()
        return EXIT_OK

    try:
        return args.func(args)
    except CogglyError as exc:
        return fail(exc)
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return EXIT_OK
    except KeyboardInterrupt:
        print("error: cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

"""Identity storage with a single atomic JSON transaction and Linux file locking."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import tempfile
from threading import RLock
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHash


ROLES = {"admin", "operator", "viewer"}
USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


class AccessError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def public_user(user):
    return {key: user[key] for key in (
        "id", "username", "display_name", "role", "enabled", "revision", "created_at", "last_login"
    )}


def validate_password(password):
    if not 12 <= len(password) <= 128:
        raise AccessError("Пароль має містити від 12 до 128 символів.")


class IdentityStore:
    def __init__(self, directory, session_seconds=28800):
        self.directory = Path(directory)
        self.path = self.directory / "accounts.json"
        self.lock = RLock()
        self.session_seconds = session_seconds
        self.audit = logging.getLogger("ip-media-tool.audit." + hashlib.sha256(str(self.directory.resolve()).encode()).hexdigest()[:16])

    def initialize(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.audit.handlers:
            audit_path = self.directory / "audit.jsonl"
            fd = os.open(audit_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            os.close(fd)
            handler = RotatingFileHandler(audit_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.audit.addHandler(handler)
            self.audit.setLevel(logging.INFO)
            self.audit.propagate = False
        with self.transaction():
            pass

    @contextmanager
    def transaction(self, write=True):
        with self.lock:
            fd = os.open(self.directory / "accounts.lock", os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(fd, "r+") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
                if self.path.exists():
                    # Fail closed on malformed or unsupported state; never reset user data.
                    with self.path.open(encoding="utf-8") as source:
                        state = json.load(source)
                    if state.get("schema_version") != 1 or not isinstance(state.get("users"), list):
                        raise RuntimeError("Unsupported account file; restore or migrate it manually.")
                    if not isinstance(state.get("sessions"), dict) or not isinstance(state.get("attempts"), dict):
                        raise RuntimeError("Invalid account state.")
                else:
                    state = {"schema_version": 1, "users": [], "sessions": {}, "attempts": {}}
                yield state
                if write:
                    descriptor, name = tempfile.mkstemp(prefix=".accounts-", dir=self.directory)
                    try:
                        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                            json.dump(state, target, ensure_ascii=False, indent=2)
                            target.write("\n")
                            target.flush()
                            os.fsync(target.fileno())
                        os.replace(name, self.path)
                        directory_fd = os.open(self.directory, os.O_DIRECTORY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                    finally:
                        if os.path.exists(name):
                            os.unlink(name)

    def record(self, action, actor, **fields):
        self.audit.info(json.dumps({"time": utc_now(), "action": action, "actor": actor, **fields}, ensure_ascii=False))

    def ready(self):
        with self.transaction(write=False) as state:
            return bool(state["users"])

    def _create(self, state, username, display_name, password, role):
        username = username.strip().lower()
        display_name = display_name.strip()
        if not USERNAME.fullmatch(username):
            raise AccessError("Логін: 3–32 символи, латинські малі літери, цифри, крапка, дефіс або підкреслення.")
        if not display_name or len(display_name) > 80 or role not in ROLES:
            raise AccessError("Перевірте ім’я та роль користувача.")
        validate_password(password)
        if any(user["username"] == username for user in state["users"]):
            raise AccessError("Цей логін уже використовується.", 409)
        if len(state["users"]) >= 200:
            raise AccessError("Досягнуто ліміт 200 облікових записів.", 409)
        user = {
            "id": secrets.token_hex(12), "username": username, "display_name": display_name,
            "password_hash": PASSWORDS.hash(password), "role": role, "enabled": True,
            "revision": 1, "created_at": utc_now(), "last_login": None,
        }
        state["users"].append(user)
        return user

    def bootstrap(self, username, display_name, password):
        with self.transaction() as state:
            if state["users"]:
                raise AccessError("Адміністратора вже створено. Використовуйте reset-password для відновлення.", 409)
            user = self._create(state, username, display_name, password, "admin")
            result = public_user(user)
        self.record("admin.bootstrap", "server-console", user_id=result["id"])
        return result

    def login(self, username, password, address):
        username = username.strip().lower()
        now = time.time()
        error = None
        result = None
        with self.transaction() as state:
            state["sessions"] = {key: value for key, value in state["sessions"].items() if value["expires"] > now}
            state["attempts"] = {key: value for key, value in state["attempts"].items() if value["until"] > now}
            keys = [hashlib.sha256(value.encode()).hexdigest() for value in ("ip:" + address, "user:" + username)]
            if any(state["attempts"].get(key, {}).get("count", 0) >= 10 for key in keys):
                error = AccessError("Забагато спроб входу. Спробуйте пізніше (до 15 хвилин).", 429)
            else:
                user = next((value for value in state["users"] if value["username"] == username), None)
                valid = False
                try:
                    PASSWORDS.verify(user["password_hash"] if user else _DUMMY_HASH, password)
                    valid = bool(user and user["enabled"])
                except (VerificationError, InvalidHash):
                    pass
                if not valid:
                    for key in keys:
                        bucket = state["attempts"].setdefault(key, {"count": 0, "until": now + 900})
                        bucket["count"] += 1
                    # Bound unauthenticated state growth from arbitrary usernames/addresses.
                    if len(state["attempts"]) > 4000:
                        state["attempts"] = dict(sorted(state["attempts"].items(), key=lambda item: item[1]["until"], reverse=True)[:4000])
                    error = AccessError("Невірний логін або пароль.", 401)
                else:
                    state["attempts"].pop(keys[1], None)
                    if PASSWORDS.check_needs_rehash(user["password_hash"]):
                        user["password_hash"] = PASSWORDS.hash(password)
                    user["last_login"] = utc_now()
                    # Limit sessions per user and globally, expiring oldest sessions first.
                    existing = [(key, value) for key, value in state["sessions"].items() if value["user_id"] == user["id"]]
                    for key, _ in sorted(existing, key=lambda item: item[1]["expires"])[:-4]:
                        state["sessions"].pop(key, None)
                    if len(state["sessions"]) >= 1000:
                        oldest = min(state["sessions"], key=lambda key: state["sessions"][key]["expires"])
                        del state["sessions"][oldest]
                    token = secrets.token_urlsafe(32)
                    csrf = secrets.token_urlsafe(32)
                    state["sessions"][hashlib.sha256(token.encode()).hexdigest()] = {
                        "user_id": user["id"], "csrf": csrf, "expires": now + self.session_seconds,
                    }
                    result = (token, csrf, public_user(user))
        if error:
            self.record("auth.login_failed", username, address=address)
            raise error
        self.record("auth.login", username, address=address)
        return result

    def identity(self, token):
        if not token or len(token) > 128:
            raise AccessError("Увійдіть до системи.", 401)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.transaction(write=False) as state:
            session = state["sessions"].get(digest)
            if not session or session["expires"] <= time.time():
                raise AccessError("Сесію завершено. Увійдіть знову.", 401)
            user = next((user for user in state["users"] if user["id"] == session["user_id"] and user["enabled"]), None)
            if not user:
                raise AccessError("Обліковий запис недоступний.", 401)
            return public_user(user), session["csrf"]

    @staticmethod
    def revoke(state, user_id):
        state["sessions"] = {key: value for key, value in state["sessions"].items() if value["user_id"] != user_id}

    def logout(self, token, username):
        with self.transaction() as state:
            state["sessions"].pop(hashlib.sha256(token.encode()).hexdigest(), None)
        self.record("auth.logout", username)

    @staticmethod
    def administrator(state, actor_id):
        actor = next((user for user in state["users"] if user["id"] == actor_id), None)
        if not actor or not actor["enabled"] or actor["role"] != "admin":
            raise AccessError("Потрібні права адміністратора.", 403)
        return actor

    def list_users(self, actor_id):
        with self.transaction(write=False) as state:
            self.administrator(state, actor_id)
            return [public_user(user) for user in state["users"]]

    def create_user(self, actor_id, **fields):
        with self.transaction() as state:
            actor = self.administrator(state, actor_id)
            result = public_user(self._create(state, **fields))
        self.record("user.create", actor["username"], user_id=result["id"], role=result["role"])
        return result

    def modify_user(self, actor_id, user_id, revision, display_name, role, enabled, delete=False):
        with self.transaction() as state:
            actor = self.administrator(state, actor_id)
            target = next((user for user in state["users"] if user["id"] == user_id), None)
            if not target:
                raise AccessError("Користувача не знайдено.", 404)
            if target["revision"] != revision:
                raise AccessError("Запис змінено в іншому вікні. Оновіть список.", 409)
            if not delete and (role not in ROLES or not display_name.strip() or len(display_name) > 80):
                raise AccessError("Перевірте ім’я та роль.")
            if actor_id == user_id and (delete or not enabled or role != "admin"):
                raise AccessError("Не можна видалити, вимкнути або позбавити прав адміністратора власний обліковий запис.", 409)
            removes_admin = target["enabled"] and target["role"] == "admin" and (delete or not enabled or role != "admin")
            if removes_admin and sum(user["enabled"] and user["role"] == "admin" for user in state["users"]) <= 1:
                raise AccessError("Потрібен хоча б один активний адміністратор.", 409)
            if delete:
                state["users"].remove(target)
            else:
                target.update(display_name=display_name.strip(), role=role, enabled=enabled, revision=revision + 1)
            # Revoke existing sessions whenever administrative user settings change.
            if actor_id != user_id:
                self.revoke(state, user_id)
            result = None if delete else public_user(target)
        self.record("user.delete" if delete else "user.update", actor["username"], user_id=user_id)
        return result

    def change_password(self, user_id, new_password, old_password=None, actor_id=None, console=False):
        validate_password(new_password)
        with self.transaction() as state:
            actor = self.administrator(state, actor_id) if actor_id else None
            target = next((user for user in state["users"] if user["id"] == user_id), None)
            if not target:
                raise AccessError("Користувача не знайдено.", 404)
            if not console and actor_id == user_id:
                raise AccessError("Для власного пароля використовуйте форму зміни пароля.")
            if not actor and not console:
                try:
                    PASSWORDS.verify(target["password_hash"], old_password or "")
                except (VerificationError, InvalidHash):
                    raise AccessError("Поточний пароль невірний.", 403) from None
            target["password_hash"] = PASSWORDS.hash(new_password)
            target["revision"] += 1
            self.revoke(state, user_id)
        self.record("user.password_changed", "server-console" if console else actor["username"] if actor else target["username"], user_id=user_id)

    def console_reset(self, username, password):
        with self.transaction(write=False) as state:
            user = next((user for user in state["users"] if user["username"] == username.lower()), None)
            if not user:
                raise AccessError("Користувача не знайдено.", 404)
            user_id = user["id"]
        self.change_password(user_id, password, console=True)

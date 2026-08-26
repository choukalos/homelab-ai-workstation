# Quality Test: Refactoring
## Prompt
Refactor Python class to use dependency injection...

## Output

```python
<think>
The user wants me to refactor a Python class to use dependency injection instead of hardcoded dependencies. Let me analyze the current class and identify the issues:

1. **Hardcoded dependencies**: `PostgreSQLConnection` and `RedisClient` are instantiated directly in `__init__`
2. **Hardcoded configuration**: SMTP host, port are hardcoded
3. **SQL injection vulnerability**: The query uses string concatenation
4. **Tight coupling**: The class creates its own dependencies rather than receiving them

The refactoring should:
- Accept dependencies through the constructor (constructor injection)
- Use interfaces/abstract classes for the dependencies
- Keep configuration injectable
- Fix the SQL injection issue while refactoring
- Make the class testable

Let me write a clean, well-structured refactored version with:
- Abstract base classes / protocols for the dependencies
- Constructor injection
- Configuration as a separate injectable object
- Fix the SQL injection
- Keep the caching logic but make it injectable
- Show how to wire it up

I'll provide a comprehensive refactoring with explanations.
</think>

# Refactoring with Dependency Injection

## Problems with the Original

| Issue | Location |
|---|---|
| Hardcoded infra dependencies | `PostgreSQLConnection(...)`, `RedisClient(...)` |
| Hardcoded config | `smtp_host`, `smtp_port` |
| SQL injection | string concatenation in `db.query(...)` |
| Untestable | can't swap in mocks without editing the class |
| Violates SRP | the class *creates* its own collaborators |

## Refactored Code

### 1. Define interfaces (abstractions)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


# ── Interfaces ──────────────────────────────────────────────

class Database(ABC):
    """Abstract contract for any data-store implementation."""

    @abstractmethod
    def get_user(self, user_id: int) -> dict: ...

    @abstractmethod
    def get_template_content(self, template_name: str) -> Optional[str]: ...


class Cache(ABC):
    """Abstract contract for any caching backend."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def get(self, key: str) -> Optional[str]: ...

    @abstractmethod
    def set(self, key: str, value: str, ttl: int) -> None: ...


class MailTransport(ABC):
    """Abstract contract for any email-sending mechanism."""

    @abstractmethod
    def send(self, to: str, subject: str, body: str) -> None: ...


# ── Configuration (injected, not hardcoded) ─────────────────

@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


@dataclass(frozen=True)
class CacheConfig:
    host: str
    port: int
    db: int = 0
```

### 2. Concrete implementations (live *outside* the service)

```python
class PostgreSQLDatabase(Database):
    def __init__(self, config: DbConfig):
        self._conn = psycopg2.connect(
            host=config.host, port=config.port,
            dbname=config.database, user=config.user,
            password=config.password,
        )

    def get_user(self, user_id: int) -> dict:
        # Parameterised query – no SQL injection
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else {}

    def get_template_content(self, template_name: str) -> Optional[str]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT content FROM templates WHERE name = %s", (template_name,)
        )
        row = cur.fetchone()
        return row[0] if row else None


class RedisCache(Cache):
    def __init__(self, config: CacheConfig):
        self._client = redis.Redis(
            host=config.host, port=config.port, db=config.db
        )

    def exists(self, key: str) -> bool:
        return self._client.exists(key) is not None

    def get(self, key: str) -> Optional[str]:
        return self._client.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self._client.setex(key, ttl, value)


class SmtpMailTransport(MailTransport):
    def __init__(self, config: SmtpConfig):
        self._config = config

    def send(self, to: str, subject: str, body: str) -> None:
        # real SMTP send logic here
        ...
```

### 3. The refactored service (receives everything via constructor)

```python
class EmailService:
    """
    No hardcoded dependencies.
    Every collaborator is injected through __init__.
    """

    def __init__(
        self,
        db: Database,
        cache: Cache,
        mail: MailTransport,
        smtp_config: SmtpConfig,
    ) -> None:
        self._db    = db
        self._cache = cache
        self._mail  = mail
        self._smtp  = smtp_config          # config is data, not a live connection

    # ── public API ────────────────────────────────────────

    def send_notification(self, user_id: int, template: str) -> None:
        user = self._db.get_user(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")

        content = self._resolve_template(template)
        self._mail.send(to=user["email"], subject=template, body=content)

    # ── private helpers ───────────────────────────────────

    def _resolve_template(self, template: str) -> str:
        cache_key = f"template:{template}"

        if self._cache.exists(cache_key):
            return self._cache.get(cache_key)

        content = self._db.get_template_content(template)
        if content is None:
            raise KeyError(f"Template '{template}' not found in database")

        self._cache.set(cache_key, content, ttl=3600)
        return content
```

### 4. Wiring / composition root (done *once*, at startup)

```python
def build_email_service() -> EmailService:
    """
    The ONLY place that knows about concrete classes.
    Swap implementations here without touching EmailService.
    """
    db_config    = DbConfig("localhost", 5432, "app_db", "user", "secret")
    cache_config = CacheConfig("localhost", 6379)
    smtp_config  = SmtpConfig("smtp.example.com", 587, "app", "token")

    return EmailService(
        db    = PostgreSQLDatabase(db_config),
        cache = RedisCache(cache_config),
        mail  = SmtpMailTransport(smtp_config),
        smtp_config = smtp_config,
    )
```

### 5. Testing becomes trivial (no real DB / Redis needed)

```python
class FakeDatabase(Database):
    def get_user(self, user_id: int) -> dict:
        return {"email": "test@example.com"}

    def get_template_content(self, template_name: str) -> Optional[str]:
        return f"Hello, this is {template_name}."


class FakeCache(Cache):
    def __init__(self):
        self._store: dict = {}

    def exists(self, key: str) -> bool:
        return key in self._store

    def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self._store[key] = value


class FakeMail(MailTransport):
    def __init__(self):
        self.sent: list = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))


def test_send_notification():
    mail = FakeMail()
    svc  = EmailService(
        db    = FakeDatabase(),
        cache = FakeCache(),
        mail  = mail,
        smtp_config = SmtpConfig("localhost", 25),
    )

    svc.send_notification(user_id=1, template="welcome")

    assert mail.sent == [("test@example.com", "welcome", "Hello, this is welcome.")]
```

## What changed and why

```
BEFORE (hardcoded)                    AFTER (injected)
─────────────────────────────────    ─────────────────────────────────
self.db  = PostgreSQLConnection(...)  self._db    = db          ← passed in
self.cache = RedisClient(...)        self._cache = cache       ← passed in
self
```

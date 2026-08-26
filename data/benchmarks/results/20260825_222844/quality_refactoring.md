# Quality Test: Refactoring
## Prompt
Refactor Python class to use dependency injection...

## Output

```python
<think>
The user wants me to refactor a Python class to use dependency injection instead of hardcoded dependencies. Let me analyze the current code:

1. `EmailService` has hardcoded dependencies:
   - `smtp_host` and `smtp_port` are hardcoded strings/numbers
   - `PostgreSQLConnection("localhost", 5432)` is instantiated directly
   - `RedisClient("localhost", 6379)` is instantiated directly

2. The `send_notification` method uses these dependencies directly.

To refactor with dependency injection, I should:
- Define interfaces/abstract base classes for the dependencies (database, cache, SMTP config)
- Inject them through the constructor
- This makes the class testable, flexible, and follows the Dependency Inversion Principle

Let me think about the best approach:

Option 1: Constructor injection with abstract base classes
- Define `Database` ABC, `Cache` ABC
- Pass concrete implementations via constructor
- SMTP config can be passed as parameters or a config object

Option 2: Use a configuration object + injected services
- A `Config` dataclass for SMTP settings
- Abstract interfaces for DB and Cache

I'll go with a clean approach:
- Define abstract base classes (or Protocol classes) for the dependencies
- Use constructor injection
- Keep it practical and readable
- Also fix the SQL injection vulnerability while I'm at it (parameterized queries)

Let me write a clean, well-structured refactoring.
</think>

# Refactored with Dependency Injection

Here's the refactored version using constructor injection with abstract interfaces:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


# ─── Dependency Interfaces ────────────────────────────────────────────

class Database(ABC):
    """Abstract interface for database access."""

    @abstractmethod
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        ...

    @abstractmethod
    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        ...


class Cache(ABC):
    """Abstract interface for cache operations."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def get(self, key: str) -> str:
        ...

    @abstractmethod
    def set(self, key: str, value: str, ttl: int) -> None:
        ...


class EmailTransport(ABC):
    """Abstract interface for email delivery."""

    @abstractmethod
    def send(self, to: str, subject: str, body: str) -> None:
        ...


# ─── Configuration ────────────────────────────────────────────────────

@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int


# ─── The Service (now fully decoupled) ────────────────────────────────

class EmailService:
    def __init__(
        self,
        db: Database,
        cache: Cache,
        transport: EmailTransport,
        smtp_config: SmtpConfig,
    ):
        self._db = db
        self._cache = cache
        self._transport = transport
        self._smtp_config = smtp_config

    def send_notification(self, user_id: int, template: str) -> None:
        # Parameterized query — no more SQL injection
        user = self._db.query_one(
            "SELECT * FROM users WHERE id = %s", (user_id,)
        )
        if user is None:
            raise ValueError(f"User {user_id} not found")

        cache_key = f"template:{template}"
        if self._cache.exists(cache_key):
            content = self._cache.get(cache_key)
        else:
            row = self._db.query_one(
                "SELECT content FROM templates WHERE name = %s", (template,)
            )
            if row is None:
                raise ValueError(f"Template '{template}' not found")
            content = row["content"]
            self._cache.set(cache_key, content, ttl=3600)

        self._transport.send(
            to=user["email"],
            subject=f"Notification: {template}",
            body=content,
        )


# ─── Concrete Implementations (wired up elsewhere) ────────────────────

class PostgreSQLDatabase(Database):
    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        # self._conn = psycopg2.connect(...)

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        ...

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        ...


class RedisCache(Cache):
    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        # self._client = redis.Redis(...)

    def exists(self, key: str) -> bool:
        ...

    def get(self, key: str) -> str:
        ...

    def set(self, key: str, value: str, ttl: int) -> None:
        ...


class SmtpEmailTransport(EmailTransport):
    def __init__(self, config: SmtpConfig):
        self._config = config

    def send(self, to: str, subject: str, body: str) -> None:
        # smtplib logic using self._config.host / port
        print(f"[SMTP {self._config.host}:{self._config.port}] Sent to {to}")


# ─── Composition Root (wiring) ────────────────────────────────────────

def build_email_service() -> EmailService:
    """Single place where concrete dependencies are chosen."""
    smtp_config = SmtpConfig(host="smtp.example.com", port=587)
    db = PostgreSQLDatabase(host="localhost", port=5432)
    cache = RedisCache(host="localhost", port=6379)
    transport = SmtpEmailTransport(config=smtp_config)

    return EmailService(db=db, cache=cache, transport=transport, smtp_config=smtp_config)


# ─── Usage ────────────────────────────────────────────────────────────

service = build_email_service()
service.send_notification(user_id=42, template="welcome")
```

## What changed and why

| Concern | Before | After |
|---|---|---|
| **DB / Cache / SMTP** | Instantiated inside `__init__` with hardcoded hosts | Injected via constructor as interfaces |
| **SMTP settings** | Magic strings in the class body | `SmtpConfig` dataclass, passed in |
| **Email sending** | Inline `print` (implicit dependency) | `EmailTransport` interface |
| **SQL injection** | String concatenation (`"… id = " + str(user_id)`) | Parameterized queries (`%s` placeholders) |
| **Testability** | Impossible to unit-test without a live Postgres/Redis | Swap in `FakeDatabase`, `InMemoryCache`, `MockTransport` |

## Example unit test (no real infra needed)

```python
class FakeDatabase(Database):
    def __init__(self, users, templates):
        self._users = users
        self._templates = templates

    def query(self, sql, params=()):
        return []

    def query_one(self, sql, params=()):
        if "users" in sql:
            return self._users.get(params[0])
        if "templates" in sql:
            return self._templates.get(params[0])
        return None


class FakeCache(Cache):
    def __init__(self):
        self._store = {}

    def exists(self, key): return key in self._store
    def get(self, key):    return self._store[key]
    def set(self, key, value, ttl): self._store[key] = value


class RecordingTransport(EmailTransport):
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))


def test_send_notification():
    transport = RecordingTransport()
    service = EmailService(
        db=FakeDatabase(
            users={42: {"email": "a@b.com"}},
            templates={"welcome": {"content": "Hi!"}},
        ),
        cache=FakeCache(),
        transport=transport,
        smtp_config=SmtpConfig("localhost", 25),
    )

    service.send_notification(42, "welcome")

    assert transport.sent == [("a@b.com", "Notification: welcome", "Hi!")]
```

The key principle: **`EmailService` now depends on abstractions, not concrete classes.** Swapping Postgres for SQLite, Redis for Memcached, or SMTP for a transactional email API requires zero changes to the service itself—only the composition root (`build_email_service`) changes.
```

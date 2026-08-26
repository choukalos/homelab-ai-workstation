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

To refactor with dependency injection:
- Define interfaces/abstract base classes for the dependencies (database, cache, SMTP config)
- Inject them through the constructor
- This makes the class testable, flexible, and loosely coupled

Let me also note some issues in the original code:
- SQL injection vulnerability (string concatenation for queries)
- The refactored version should use parameterized queries

Let me design the refactored version:

1. Define abstract interfaces:
   - `Database` (abstract) with `query` method
   - `Cache` (abstract) with `exists`, `get`, `set` methods
   - SMTP configuration could be a dataclass or just parameters

2. Refactor `EmailService` to accept these via constructor

3. Show concrete implementations

4. Show how to wire them up (composition root / factory)

Let me write clean, well-documented code.
</think>

# Refactoring with Dependency Injection

## The Problems with the Original

- **Hardcoded infrastructure** — you can't swap the DB, cache, or SMTP server without editing the class.
- **Untestable** — unit-testing `send_notification` requires a live PostgreSQL and Redis.
- **SQL injection** — string-concatenated queries.
- **Tight coupling** — `EmailService` *creates* its own collaborators (a "God Object" anti-pattern).

## Refactored Code

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass


# ─── 1. Define the contracts (interfaces) ────────────────────────────────

class Database(ABC):
    """Abstraction over any relational data store."""

    @abstractmethod
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        ...

    @abstractmethod
    def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        ...


class Cache(ABC):
    """Abstraction over any key-value cache."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def get(self, key: str) -> str | None:
        ...

    @abstractmethod
    def set(self, key: str, value: str, ttl: int) -> None:
        ...


@dataclass(frozen=True)
class SmtpConfig:
    """Value object – immutable SMTP connection settings."""
    host: str
    port: int


# ─── 2. The service now *receives* its dependencies ──────────────────────

class EmailService:
    def __init__(
        self,
        db: Database,
        cache: Cache,
        smtp: SmtpConfig,
    ) -> None:
        # Dependencies are injected – no "new" inside the class.
        self._db = db
        self._cache = cache
        self._smtp = smtp

    def send_notification(self, user_id: int, template: str) -> None:
        # Parameterised query – no SQL injection.
        user = self._db.fetch_one(
            "SELECT email FROM users WHERE id = %s", (user_id,)
        )
        if user is None:
            raise ValueError(f"User {user_id} not found")

        cache_key = f"template:{template}"

        if self._cache.exists(cache_key):
            content = self._cache.get(cache_key)
        else:
            row = self._db.fetch_one(
                "SELECT content FROM templates WHERE name = %s", (template,)
            )
            if row is None:
                raise ValueError(f"Template '{template}' not found")
            content = row["content"]
            self._cache.set(cache_key, content, ttl=3600)

        # Actual SMTP send would go here using self._smtp
        print(f"Sent to {user['email']} via {self._smtp.host}:{self._smtp.port}")


# ─── 3. Concrete implementations (lives in a separate module in practice) ─

class PostgreSQLConnection(Database):
    def __init__(self, host: str, port: int, dsn: str = ""):
        self._host = host
        self._port = port
        # self._conn = psycopg2.connect(...)  # real driver

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        ...  # real implementation

    def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        ...  # real implementation


class RedisClient(Cache):
    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        # self._conn = redis.Redis(...)  # real driver

    def exists(self, key: str) -> bool:
        ...

    def get(self, key: str) -> str | None:
        ...

    def set(self, key: str, value: str, ttl: int) -> None:
        ...


# ─── 4. Composition root – the ONLY place that knows concrete types ──────

def build_email_service(env: str = "production") -> EmailService:
    """Factory / wiring function.  Call it once at application startup."""
    if env == "production":
        db    = PostgreSQLConnection("db.prod.internal", 5432)
        cache = RedisClient("cache.prod.internal", 6379)
        smtp  = SmtpConfig("smtp.example.com", 587)
    else:  # dev / staging
        db    = PostgreSQLConnection("localhost", 5432)
        cache = RedisClient("localhost", 6379)
        smtp  = SmtpConfig("smtp.dev.example.com", 1025)

    return EmailService(db=db, cache=cache, smtp=smtp)


# ─── 5. Usage ─────────────────────────────────────────────────────────────

service = build_email_service()
service.send_notification(user_id=42, template="welcome")
```

## Why This Is Better

| Concern | Before | After |
|---|---|---|
| **Testability** | Needs live Postgres + Redis | Inject `FakeDB` / `FakeCache` in tests |
| **Swapping infra** | Edit the class source | Change one line in `build_email_service` |
| **Single Responsibility** | `EmailService` creates *and* uses deps | `EmailService` only *uses* deps |
| **SQL safety** | String concatenation | Parameterised queries |
| **Config** | Hardcoded literals | `SmtpConfig` dataclass / env vars / config file |

## Example Unit Test (the payoff)

```python
class FakeDB(Database):
    def __init__(self, users, templates):
        self._users = users
        self._templates = templates

    def fetch_one(self, sql, params=()):
        if "users" in sql:
            return self._users.get(params[0])
        if "templates" in sql:
            return self._templates.get(params[0])
        return None

    def query(self, sql, params=()):
        return []


class FakeCache(Cache):
    def __init__(self):
        self._store: dict[str, str] = {}

    def exists(self, key): return key in self._store
    def get(self, key):    return self._store.get(key)
    def set(self, key, value, ttl): self._store[key] = value


def test_send_notification_uses_cache():
    db    = FakeDB(users={1: {"email": "a@b.com"}},
                   templates={"welcome": {"content": "Hi!"}})
    cache = FakeCache()
    svc   = EmailService(db, cache, SmtpConfig("localhost", 25))

    svc.send_notification(1, "welcome")          # populates cache
    svc.send_notification(1, "welcome")          # hits cache

    assert cache._store["template:welcome"] == "Hi!"
```

No database, no Redis, no network — just plain objects. That's the core benefit of dependency injection.
```

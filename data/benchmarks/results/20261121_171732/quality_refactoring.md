# Quality Test: Refactoring
## Prompt
Refactor Python class to use dependency injection...

## Output

```python
<think>
The user wants me to refactor a Python class to use dependency injection instead of hardcoded dependencies. Let me analyze the current code:

1. `EmailService` has hardcoded dependencies:
   - `smtp_host` and `smtp_port` are hardcoded strings/numbers
   - `PostgreSQLConnection` is instantiated directly with hardcoded host/port
   - `RedisClient` is instantiated directly with hardcoded host/port

2. The `send_notification` method uses these dependencies directly.

To refactor with dependency injection, I need to:
- Define interfaces/protocols for the dependencies (Database, Cache, SMTP configuration)
- Accept these dependencies through the constructor (constructor injection)
- Use type hints with Protocol or ABC to define the contracts
- Make the class testable and flexible

Let me think about the best approach:

1. Define Protocols (or ABCs) for:
   - A database interface (with `query` method)
   - A cache interface (with `exists`, `get`, `set` methods)
   - SMTP configuration could be a dataclass or just parameters

2. Inject these via `__init__`

3. I should also note the SQL injection vulnerability in the original code and fix it while refactoring.

Let me write a clean, well-structured refactoring.
</think>

# Refactored with Dependency Injection

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


# ─── Dependency Interfaces ───────────────────────────────────────────────

class Database(ABC):
    """Abstract interface for database access."""

    @abstractmethod
    def query(self, sql: str, params: tuple = ()) -> Any:
        ...


class Cache(ABC):
    """Abstract interface for cache access."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def get(self, key: str) -> Any:
        ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl: int) -> None:
        ...


@dataclass(frozen=True)
class SmtpConfig:
    """Immutable SMTP configuration."""
    host: str
    port: int


# ─── Refactored Service ──────────────────────────────────────────────────

class EmailService:
    def __init__(
        self,
        db: Database,
        cache: Cache,
        smtp_config: SmtpConfig,
    ) -> None:
        # Dependencies are injected — no hardcoding, no instantiation here.
        self._db = db
        self._cache = cache
        self._smtp = smtp_config

    def send_notification(self, user_id: int, template: str) -> None:
        # Parameterized query (fixes the SQL-injection bug in the original)
        user = self._db.query(
            "SELECT * FROM users WHERE id = %s", (user_id,)
        )

        cache_key = f"template:{template}"
        if self._cache.exists(cache_key):
            content = self._cache.get(cache_key)
        else:
            content = self._db.query(
                "SELECT content FROM templates WHERE name = %s", (template,)
            )
            self._cache.set(cache_key, content, ttl=3600)

        # send email logic...
        print(f"Sent to {user['email']}")


# ─── Concrete Implementations (lived elsewhere, e.g. adapters/) ─────────

class PostgreSQLConnection(Database):
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        # real connection setup...

    def query(self, sql: str, params: tuple = ()) -> Any:
        # real query execution...
        ...


class RedisClient(Cache):
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        # real connection setup...

    def exists(self, key: str) -> bool: ...
    def get(self, key: str) -> Any: ...
    def set(self, key: str, value: Any, ttl: int) -> None: ...


# ─── Composition Root (wiring happens in ONE place) ─────────────────────

def build_email_service() -> EmailService:
    """Factory / composition root — the only place that knows concrete types."""
    return EmailService(
        db=PostgreSQLConnection("localhost", 5432),
        cache=RedisClient("localhost", 6379),
        smtp_config=SmtpConfig(host="smtp.example.com", port=587),
    )


# ─── Usage ───────────────────────────────────────────────────────────────

service = build_email_service()
service.send_notification(user_id=42, template="welcome")
```

## What changed and why

| Concern | Before | After |
|---|---|---|
| **DB / Cache / SMTP** | Instantiated inside `__init__` with hardcoded hosts | Injected via constructor; concrete types live in a single *composition root* |
| **Testability** | Impossible to unit-test without a real Postgres + Redis | Pass in `FakeDB` / `FakeCache` stubs in tests |
| **Configuration** | Scattered magic strings | Centralized in `SmtpConfig` dataclass + the factory |
| **SQL injection** | `"... WHERE id = " + str(user_id)` | Parameterized query `"... WHERE id = %s"` with a params tuple |
| **Encapsulation** | Public `self.db`, `self.cache` | Private `self._db`, `self._cache` (read-only from outside) |

### Example unit test (the payoff)

```python
class FakeDB(Database):
    def query(self, sql, params=()):
        if "users" in sql:
            return {"email": "alice@example.com"}
        return "Welcome, {name}!"

class FakeCache(Cache):
    def __init__(self):
        self.store = {}
    def exists(self, key): return key in self.store
    def get(self, key):    return self.store[key]
    def set(self, key, v, ttl): self.store[key] = v

def test_send_notification():
    svc = EmailService(
        db=FakeDB(),
        cache=FakeCache(),
        smtp_config=SmtpConfig("test", 25),
    )
    svc.send_notification(1, "welcome")  # no network, no DB, no Redis
```

The key principle: **`EmailService` declares *what* it needs (interfaces); the composition root decides *how* to provide them (concrete classes).** Swap implementations, change hosts, or inject mocks without touching the service code.
```

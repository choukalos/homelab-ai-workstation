# Quality Test: Refactoring
## Prompt
Refactor Python class to use dependency injection...

## Output

```python
<think>
The user wants me to refactor a Python class to use dependency injection instead of hardcoded dependencies. Let me analyze the current class:

1. `EmailService` has hardcoded dependencies:
   - `self.smtp_host = "smtp.example.com"` - hardcoded config
   - `self.smtp_port = 587` - hardcoded config
   - `self.db = PostgreSQLConnection("localhost", 5432)` - hardcoded DB connection
   - `self.cache = RedisClient("localhost", 6379)` - hardcoded cache client

2. The `send_notification` method uses these dependencies directly.

To refactor with dependency injection, I should:
- Accept dependencies through the constructor (constructor injection)
- Define interfaces/protocols for the dependencies to allow for different implementations
- Separate configuration from the service
- Make the class testable by allowing mock dependencies to be injected

Let me think about the best approach:

1. **Constructor injection** - Pass `db`, `cache`, and SMTP config through `__init__`
2. **Define protocols/interfaces** - Use Python's `Protocol` or ABC to define what the dependencies should look like
3. **Separate configuration** - SMTP host/port could be a config object or passed directly
4. **Keep it practical** - Don't over-engineer, but make it clean

I'll use:
- `Protocol` classes (or ABCs) for the database and cache interfaces
- Constructor injection for all dependencies
- A config dataclass for SMTP settings
- Type hints for clarity

Let me write a clean, well-structured refactoring.
</think>

# Dependency Injection Refactoring

Here's the refactored version using constructor injection with protocol-based interfaces:

```python
from dataclasses import dataclass
from typing import Protocol, Any


# ─── Interfaces (Protocols) ───────────────────────────────────────────────────

class Database(Protocol):
    """Abstraction over any relational database."""
    def query(self, sql: str) -> Any: ...


class Cache(Protocol):
    """Abstraction over any key-value cache."""
    def exists(self, key: str) -> bool: ...
    def get(self, key: str) -> Any: ...
    def set(self, key: str, value: Any, ttl: int) -> None: ...


# ─── Configuration ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int


# ─── Service (now fully injectable) ───────────────────────────────────────────

class EmailService:
    def __init__(
        self,
        db: Database,
        cache: Cache,
        smtp_config: SmtpConfig,
    ) -> None:
        self._db = db
        self._cache = cache
        self._smtp_config = smtp_config

    def send_notification(self, user_id: int, template: str) -> None:
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

        # send email via SMTP using self._smtp_config
        print(f"Sent to {user['email']}")


# ─── Concrete implementations (unchanged, but now swappable) ──────────────────

class PostgreSQLConnection:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    def query(self, sql: str, params: tuple = ()) -> Any:
        # ... real psycopg2 / asyncpg call ...
        ...


class RedisClient:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    def exists(self, key: str) -> bool: ...
    def get(self, key: str) -> Any: ...
    def set(self, key: str, value: Any, ttl: int) -> None: ...


# ─── Wiring / Composition Root ────────────────────────────────────────────────

def build_email_service() -> EmailService:
    """Single place where concrete dependencies are chosen."""
    db = PostgreSQLConnection("localhost", 5432)
    cache = RedisClient("localhost", 6379)
    smtp = SmtpConfig(host="smtp.example.com", port=587)
    return EmailService(db=db, cache=cache, smtp_config=smtp)


# ─── Usage ────────────────────────────────────────────────────────────────────

service = build_email_service()
service.send_notification(user_id=42, template="welcome")
```

## What changed and why

| Concern | Before | After |
|---|---|---|
| **DB / Cache** | Instantiated inside `__init__` with hardcoded hosts | Injected via constructor; any object satisfying the `Protocol` works |
| **SMTP settings** | Magic strings/ints in the class body | Grouped into an immutable `SmtpConfig` dataclass, injected |
| **Testability** | Impossible to unit-test without a live Postgres + Redis | Pass in `FakeDB` / `FakeCache` in tests |
| **SQL injection** | f-string / string concatenation | Parameterised queries (`%s` + tuple) |
| **Wiring** | Scattered across the class | Centralised in `build_email_service()` (composition root) |

## Example unit test (the payoff)

```python
class FakeDB:
    def query(self, sql, params=()):
        if "users" in sql:
            return {"email": "alice@test.com"}
        return "Welcome, {name}!"

class FakeCache:
    def __init__(self): self.store = {}
    def exists(self, key): return key in self.store
    def get(self, key): return self.store[key]
    def set(self, key, value, ttl): self.store[key] = value

def test_send_notification():
    svc = EmailService(
        db=FakeDB(),
        cache=FakeCache(),
        smtp_config=SmtpConfig("localhost", 25),
    )
    svc.send_notification(1, "welcome")  # no network, no DB, no Redis
```

The key principle: **`EmailService` no longer *creates* its collaborators—it *receives* them.** That single shift is what makes the class independently testable, swappable, and free of hidden infrastructure assumptions.
```

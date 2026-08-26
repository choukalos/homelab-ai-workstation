# Quality Test: Refactoring
## Prompt
Refactor Python class to use dependency injection...

## Output

```python
<think>
The user wants me to refactor a Python class to use dependency injection instead of hardcoded dependencies. Let me analyze the current class:

1. `EmailService` has hardcoded dependencies:
   - `smtp_host` and `smtp_port` are hardcoded strings/numbers
   - `PostgreSQLConnection` is instantiated directly with hardcoded values
   - `RedisClient` is instantiated directly with hardcoded values

2. The `send_notification` method uses these dependencies directly.

To refactor with dependency injection, I need to:
- Define interfaces/abstract base classes for the dependencies (database, cache, SMTP config)
- Inject them through the constructor
- Make the class depend on abstractions rather than concrete implementations
- This makes it testable, flexible, and follows the Dependency Inversion Principle

Let me also note some issues in the original code:
- SQL injection vulnerability (string concatenation for queries)
- I should fix that while refactoring (use parameterized queries)

Let me structure the refactored code:

1. Define abstract interfaces:
   - `Database` (or `DBConnection`) - abstract with `query` method
   - `Cache` - abstract with `exists`, `get`, `set` methods
   - SMTP configuration could be a dataclass or just parameters

2. Refactor `EmailService` to accept these via constructor

3. Show concrete implementations that can be injected

4. Show a factory or composition root that wires everything together

Let me write clean, well-documented code.
</think>

# Refactored with Dependency Injection

Here's the refactored code using constructor-based dependency injection, with abstractions for each dependency:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass


# ─── Abstractions (interfaces) ───────────────────────────────────────────────

class Database(ABC):
    @abstractmethod
    def query(self, sql: str, params: tuple = ()) -> dict:
        ...

    @abstractmethod
    def query_one(self, sql: str, params: tuple = ()) -> dict:
        ...


class Cache(ABC):
    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def get(self, key: str) -> str:
        ...

    @abstractmethod
    def set(self, key: str, value: str, ttl: int) -> None:
        ...


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int


# ─── Concrete implementations (lived elsewhere / in separate modules) ───────

class PostgreSQLConnection(Database):
    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        # establish connection...

    def query(self, sql: str, params: tuple = ()) -> dict:
        # use parameterized query to prevent SQL injection
        ...

    def query_one(self, sql: str, params: tuple = ()) -> dict:
        ...


class RedisClient(Cache):
    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        # establish connection...

    def exists(self, key: str) -> bool:
        ...

    def get(self, key: str) -> str:
        ...

    def set(self, key: str, value: str, ttl: int) -> None:
        ...


# ─── Refactored service ─────────────────────────────────────────────────────

class EmailService:
    def __init__(
        self,
        db: Database,
        cache: Cache,
        smtp_config: SmtpConfig,
    ):
        self._db = db
        self._cache = cache
        self._smtp_config = smtp_config

    def send_notification(self, user_id: int, template: str) -> None:
        user = self._db.query_one(
            "SELECT * FROM users WHERE id = %s", (user_id,)
        )

        cache_key = f"template:{template}"
        if self._cache.exists(cache_key):
            content = self._cache.get(cache_key)
        else:
            content = self._db.query_one(
                "SELECT content FROM templates WHERE name = %s", (template,)
            )["content"]
            self._cache.set(cache_key, content, ttl=3600)

        # send email via self._smtp_config...
        print(f"Sent to {user['email']}")


# ─── Composition root (wiring) ──────────────────────────────────────────────

def build_email_service(config: dict) -> EmailService:
    """Factory that constructs the object graph. Called once at app startup."""
    db = PostgreSQLConnection(
        host=config["db_host"],
        port=config["db_port"],
    )
    cache = RedisClient(
        host=config["cache_host"],
        port=config["cache_port"],
    )
    smtp = SmtpConfig(
        host=config["smtp_host"],
        port=config["smtp_port"],
    )
    return EmailService(db=db, cache=cache, smtp_config=smtp)


# ─── Usage ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    config = {
        "db_host": "localhost",
        "db_port": 5432,
        "cache_host": "localhost",
        "cache_port": 6379,
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
    }
    service = build_email_service(config)
    service.send_notification(user_id=42, template="welcome")
```

## What changed and why

| Concern | Before | After |
|---|---|---|
| **Dependencies** | Hardcoded in `__init__` | Injected via constructor |
| **Coupling** | Tied to concrete `PostgreSQLConnection` / `RedisClient` | Tied to `Database` / `Cache` abstractions |
| **Config** | Baked into the class | Externalized via `SmtpConfig` dataclass / config dict |
| **Testability** | Would need real DB + Redis | Pass in `MockDatabase` / `MockCache` |
| **SQL safety** | String concatenation (injection risk) | Parameterized queries (`%s` placeholders) |
| **Wiring** | Implicit (class creates its own deps) | Explicit in `build_email_service` (composition root) |

## Example unit test (the payoff)

```python
from unittest.mock import MagicMock

def test_send_notification_uses_cache():
    db = MagicMock(spec=Database)
    cache = MagicMock(spec=Cache)
    smtp = SmtpConfig("smtp.test", 25)

    cache.exists.return_value = True
    cache.get.return_value = "Hello {{name}}"

    service = EmailService(db=db, cache=cache, smtp_config=smtp)
    service.send_notification(user_id=1, template="welcome")

    # DB was never queried for the template — cache hit
    db.query_one.assert_not_called()
    cache.get.assert_called_once_with("template:welcome")
```

The key principle at work: **`EmailService` now depends on abstractions, not concretions** (the Dependency Inversion Principle). Swapping PostgreSQL for SQLite, Redis for Memcached, or the SMTP host for a sandbox server requires zero changes to `EmailService` itself.
```

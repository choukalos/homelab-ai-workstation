# Quality Test: Refactoring
## Prompt
Refactor Python class to use dependency injection...

## Output

```python
<think>

</think>

Here is the refactored `EmailService` class using **constructor-based dependency injection**.

### Key Changes:
1. **Dependencies are injected via constructor parameters** (`smtp_host`, `smtp_port`, `db`, `cache`) instead of being hardcoded.
2. **Type hints** are added for clarity (using `typing` for compatibility).
3. **SQL Injection vulnerability fixed**: The original code used string concatenation for SQL queries, which is dangerous. This version uses **parameterized queries** (assuming the `db` interface supports them, which is standard for `PostgreSQLConnection`-like interfaces).
4. **Separation of concerns**: The class no longer knows *how* to create its dependencies, only how to *use* them.

```python
from typing import Any, Optional

class EmailService:
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        db: Any,  # Replace 'Any' with your actual PostgreSQLConnection type
        cache: Any  # Replace 'Any' with your actual RedisClient type
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.db = db
        self.cache = cache

    def send_notification(self, user_id: int, template: str) -> None:
        # Use parameterized query to prevent SQL injection
        user = self.db.query("SELECT * FROM users WHERE id = %s", (user_id,))
        
        cache_key = f"template:{template}"
        
        if self.cache.exists(cache_key):
            content = self.cache.get(cache_key)
        else:
            # Use parameterized query to prevent SQL injection
            content = self.db.query("SELECT content FROM templates WHERE name = %s", (template,))
            self.cache.set(cache_key, content, 3600)
        
        # send email logic...
        print(f"Sent to {user['email']}")
```

### Example Usage (Composition Root):

```python
# In your application's main entry point (e.g., main.py)
def main():
    # Create concrete dependencies
    db = PostgreSQLConnection("localhost", 5432)
    cache = RedisClient("localhost", 6379)
    
    # Inject dependencies into EmailService
    email_service = EmailService(
        smtp_host="smtp.example.com",
        smtp_port=587,
        db=db,
        cache=cache
    )
    
    email_service.send_notification(user_id=123, template="welcome")
```

### Benefits of This Refactoring:
- **Testability**: You can easily mock `db` and `cache` in unit tests.
- **Flexibility**: You can swap out `PostgreSQLConnection` for `MySQLConnection` or `RedisClient` for `MemcachedClient` without modifying `EmailService`.
- **Security**: Parameterized queries prevent SQL injection attacks.
- **Maintainability**: Configuration is centralized in the composition root, not scattered inside the service.

> **Note**: Replace `Any` with the actual type hints for `PostgreSQLConnection` and `RedisClient` if you have them defined, for better IDE support and static analysis. If your `db.query()` method does not support parameterized queries, you should update it to do so, as string interpolation in SQL is a critical security risk.
```

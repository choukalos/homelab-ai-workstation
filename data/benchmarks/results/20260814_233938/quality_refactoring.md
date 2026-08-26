# Quality Test: Refactoring
## Prompt
Refactor Python class to use dependency injection...

## Output

```python
<think>
Here's a thinking process:

1.  **Analyze the User Request:**
   - **Input:** A Python class `EmailService` with hardcoded dependencies (`smtp_host`, `smtp_port`, `PostgreSQLConnection`, `RedisClient`).
   - **Goal:** Refactor to use dependency injection (DI) instead of hardcoded dependencies.
   - **Key Issues in Original Code:**
     - Hardcoded configuration values (`smtp_host`, `smtp_port`)
     - Direct instantiation of dependencies (`PostgreSQLConnection`, `RedisClient`)
     - SQL injection vulnerability in `self.db.query("SELECT * FROM users WHERE id = " + str(user_id))` and `self.db.query(f"SELECT content FROM templates WHERE name = '{template}'")`
     - Tight coupling makes testing and configuration difficult.

2.  **Identify Dependencies:**
   - SMTP configuration (host, port)
   - Database connection/client (`PostgreSQLConnection`)
   - Cache client (`RedisClient`)
   - (Implicit) Email sending mechanism (though not fully implemented, I should abstract it or keep it as a method)

3.  **Determine DI Approach:**
   - Constructor injection is the most common and Pythonic approach.
   - Dependencies should be passed as parameters to `__init__`.
   - Use type hints for better readability and IDE support.
   - Consider using interfaces/protocols or abstract base classes for better testability, but for simplicity, I'll stick to passing concrete types or duck-typed objects, with clear type hints. I'll also mention that using `typing.Protocol` or `abc.ABC` is best practice for production.

4.  **Refactor Step-by-Step:**
   - Change `__init__` to accept dependencies: `db`, `cache`, `smtp_host`, `smtp_port`.
   - Remove hardcoded values.
   - Fix SQL injection vulnerabilities (use parameterized queries).
   - Keep the core logic but make it cleaner.
   - Add type hints.

   Draft:
   ```python
   from typing import Any, Dict

   class EmailService:
       def __init__(self, db: Any, cache: Any, smtp_host: str = "smtp.example.com", smtp_port: int = 587):
           self.db = db
           self.cache = cache
           self.smtp_host = smtp_host
           self.smtp_port = smtp_port

       def send_notification(self, user_id: int, template: str) -> None:
           # Fixed SQL injection by using parameterized queries
           user = self.db.query("SELECT * FROM users WHERE id = %s", (user_id,))
           if not user:
               raise ValueError(f"User {user_id} not found")

           cache_key = f"template:{template}"
           if self.cache.exists(cache_key):
               content = self.cache.get(cache_key)
           else:
               content = self.db.query("SELECT content FROM templates WHERE name = %s", (template,))
               self.cache.set(cache_key, content, 3600)

           # send email logic...
           print(f"Sent to {user['email']}")
   ```

5.  **Evaluate & Improve:**
   - **DI:** Constructor injection is implemented. Dependencies are passed in.
   - **Type Hints:** Added, but `Any` is weak. Better to define protocols/interfaces for `db` and `cache` to show best practices.
   - **SQL Injection:** Fixed. This is a critical improvement beyond just DI.
   - **Defaults:** Kept defaults for SMTP config for convenience, but they can be overridden.
   - **Testing/Usability:** Now easily testable by mocking `db` and `cache`.
   - **Structure:** I'll provide a clean version with `typing.Protocol` for better type safety, explain the changes, and show how to instantiate it.

   Let's refine with Protocols (Python 3.8+):
   ```python
   from typing import Protocol, Any, Dict

   class DatabaseClient(Protocol):
       def query(self, sql: str, params: tuple) -> Any: ...

   class CacheClient(Protocol):
       def exists(self, key: str) -> bool: ...
       def get(self, key: str) -> Any: ...
       def set(self, key: str, value: Any, ttl: int) -> None: ...

   class EmailService:
       def __init__(
           self,
           db: DatabaseClient,
           cache: CacheClient,
           smtp_host: str = "smtp.example.com",
           smtp_port: int = 587
       ):
           self.db = db
           self.cache = cache
           self.smtp_host = smtp_host
           self.smtp_port = smtp_port

       def send_notification(self, user_id: int, template: str) -> None:
           user = self.db.query("SELECT * FROM users WHERE id = %s", (user_id,))
           if not user:
               raise ValueError(f"User {user_id} not found")

           cache_key = f"template:{template}"
           if self.cache.exists(cache_key):
               content = self.cache.get(cache_key)
           else:
               content = self.db.query("SELECT content FROM templates WHERE name = %s", (template,))
               self.cache.set(cache_key, content, 3600)

           # send email logic...
           print(f"Sent to {user['email']}")
   ```

   Wait, `Protocol` is good, but might be overkill for a simple refactoring request. I'll stick to simpler type hints but mention `Protocol`/`ABC` as a best practice. Actually, using `typing.Any` or just omitting strict types is fine for the core example, but I'll add a note about interfaces. I'll keep it clean and Pythonic.

   Also, I should address the SQL injection explicitly since it's a major flaw in the original. I'll use parameterized queries as a bonus improvement.

   Let's structure the response:
   - Provide the refactored code.
   - Explain the changes (DI via constructor, removed hardcoded deps, fixed SQL injection, added type hints).
   - Show how to instantiate/use it.
   - Mention best practices (interfaces/protocols, configuration management).

   Refined Code:
   ```python
   from typing import Any

   class EmailService:
       def __init__(
           self,
           db: Any,
           cache: Any,
           smtp_host: str = "smtp.example.com",
           smtp_port: int = 587
       ):
           self.db = db
           self.cache = cache
           self.smtp_host = smtp_host
           self.smtp_port = smtp_port

       def send_notification(self, user_id: int, template: str) -> None:
           # Use parameterized queries to prevent SQL injection
           user = self.db.query("SELECT * FROM users WHERE id = %s", (user_id,))
           if not user:
               raise ValueError(f"User {user_id} not found")

           cache_key = f"template:{template}"
           if self.cache.exists(cache_key):
               content = self.cache.get(cache_key)
           else:
               content = self.db.query("SELECT content FROM templates WHERE name = %s", (template,))
               self.cache.set(cache_key, content, 3600)

           # send email logic...
           print(f"Sent to {user['email']}")
   ```

   Actually, I'll use `typing.Protocol` in the final answer because it's the modern Python way to handle DI type hints without forcing concrete classes. It shows professionalism. But I'll keep it simple enough.

   Let's think about the exact output. I'll provide:
   1. The refactored class
   2. Usage example
   3. Key improvements explained
   4. Note on SQL injection fix (critical)
   5. Note on type hints/protocols

   I'll make sure the code is production-ready-ish.

   One thing: `self.db.query` and `self.cache` methods are assumed. I'll add a comment that in production, you'd define interfaces/protocols.

   Let's draft the response carefully.
   Structure:
   - Refactored code block
   - Explanation of changes
   - How to use it
   - Important security fix noted
   - Best practices for DI in Python

   All steps look solid. I'll generate it now. 
   Self-Correction/Refinement during thought:
   - The original code uses string concatenation for SQL. I must fix it and explicitly mention it.
   - I'll use `typing.Protocol` for `DBClient` and `CacheClient` to make it robust, but I'll keep it optional in the main code to avoid clutter, or just use `Any` with a strong recommendation. Actually, `typing.Any` is fine for brevity, but I'll show a clean version with type hints.
   - I'll stick to constructor injection as requested.
   - I'll add a usage example showing how to inject dependencies.
   - I'll mention that configuration values can also be injected or loaded from env vars/config files.

   Final code structure:
   ```python
   from typing import Any

   class EmailService:
       def __init__(
           self,
           db: Any,
           cache:
```

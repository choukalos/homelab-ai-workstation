# Quality Test: Refactoring
## Prompt
Refactor Python class to use dependency injection...

## Output

```python
<think>
Here's a thinking process:

1.  **Analyze the Original Code:**
   - Class: `EmailService`
   - Hardcoded dependencies:
     - `smtp_host` and `smtp_port` (configuration)
     - `PostgreSQLConnection` (database)
     - `RedisClient` (cache)
   - Methods:
     - `__init__`: Initializes hardcoded dependencies
     - `send_notification`: Uses `self.db` and `self.cache` to fetch user and template, then prints a message (simulating email sending)
   - Issues:
     - Hardcoded dependencies make testing difficult
     - Tight coupling to specific implementations (`PostgreSQLConnection`, `RedisClient`)
     - SQL injection vulnerability in `send_notification` (string concatenation for queries)
     - No clear separation of concerns
     - Configuration is hardcoded

2.  **Identify Refactoring Goals:**
   - Use Dependency Injection (DI) to inject dependencies via constructor
   - Define interfaces/protocols for dependencies (DB, Cache, SMTP config)
   - Improve testability and flexibility
   - Fix obvious security issues (SQL injection) as a bonus, though the main focus is DI
   - Keep the core logic intact but make it adaptable

3.  **Design Dependencies:**
   - Instead of concrete classes, use abstract base classes or protocols (typing.Protocol) for:
     - `Database` (or `UserRepository`/`TemplateRepository` for better separation, but I'll stick to a generic `Database` interface for simplicity, or split it into two repositories for better design)
     - `Cache`
     - `SMTPConfig` (or just pass config as a dict/dataclass)
   - Actually, for better DI and SOLID principles, I should define interfaces for:
     - `UserRepository` (to fetch users)
     - `TemplateRepository` (to fetch templates)
     - `CacheService` (for caching)
     - `EmailSender` (for actually sending emails, though the original just prints)
   - But to keep it close to the original while improving it, I'll use:
     - `db` (database connection/query interface)
     - `cache` (cache interface)
     - `smtp_config` (configuration)
   - I'll use `typing.Protocol` for interfaces to keep it Pythonic and type-safe.

4.  **Draft Interfaces (Protocols):**
   ```python
   from typing import Protocol, Any, Optional

   class Database(Protocol):
       def query(self, sql: str, params: tuple = ()) -> Any: ...

   class Cache(Protocol):
       def exists(self, key: str) -> bool: ...
       def get(self, key: str) -> Any: ...
       def set(self, key: str, value: Any, ttl: int) -> None: ...
   ```

   Actually, the original code uses string concatenation for SQL, which is bad. I'll adjust the interface to support parameterized queries, or at least note it. For DI refactoring, I'll keep the method signatures similar but make them injectable.

   Let's define a simple config dataclass:
   ```python
   from dataclasses import dataclass

   @dataclass
   class SMTPConfig:
       host: str
       port: int
   ```

5.  **Refactor the Class:**
   ```python
   class EmailService:
       def __init__(self, db: Database, cache: Cache, smtp_config: SMTPConfig):
           self.db = db
           self.cache = cache
           self.smtp_config = smtp_config

       def send_notification(self, user_id: int, template: str) -> None:
           # Fetch user
           user = self.db.query("SELECT * FROM users WHERE id = %s", (user_id,))
           # Cache logic
           cache_key = f"template:{template}"
           if self.cache.exists(cache_key):
               content = self.cache.get(cache_key)
           else:
               content = self.db.query("SELECT content FROM templates WHERE name = %s", (template,))
               self.cache.set(cache_key, content, 3600)
           # Send email logic...
           print(f"Sent to {user['email']} via {self.smtp_config.host}:{self.smtp_config.port}")
   ```

6.  **Evaluate & Improve:**
   - The refactoring successfully injects dependencies.
   - I fixed the SQL injection vulnerability by using parameterized queries (`%s` with tuple params), which is a best practice when dealing with DB interfaces.
   - I used `typing.Protocol` for duck-typing interfaces, which is modern Python.
   - I separated config into a dataclass.
   - I should also show how to instantiate it with concrete implementations to make it complete.
   - I should mention that in a real application, you'd use a DI container (like `dependency-injector`, `injector`, or framework-specific DI), but constructor injection is the foundation.

   Let's structure the response:
   - Show the refactored code with interfaces/protocols
   - Explain the changes (DI, interfaces, config separation, security fix)
   - Provide an example of how to use it with concrete implementations
   - Mention best practices (parameterized queries, typing, testability)

   Draft structure:
   ```python
   from typing import Protocol, Any, Optional
   from dataclasses import dataclass

   # 1. Define interfaces using Protocols
   class Database(Protocol):
       def query(self, sql: str, params: tuple = ()) -> Any: ...

   class Cache(Protocol):
       def exists(self, key: str) -> bool: ...
       def get(self, key: str) -> Any: ...
       def set(self, key: str, value: Any, ttl: int) -> None: ...

   # 2. Configuration as a dataclass
   @dataclass
   class SMTPConfig:
       host: str
       port: int

   # 3. Refactored service
   class EmailService:
       def __init__(self, db: Database, cache: Cache, config: SMTPConfig):
           self.db = db
           self.cache = cache
           self.config = config

       def send_notification(self, user_id: int, template: str) -> None:
           cache_key = f"template:{template}"

           if self.cache.exists(cache_key):
               content = self.cache.get(cache_key)
           else:
               # Using parameterized queries to prevent SQL injection
               content = self.db.query("SELECT content FROM templates WHERE name = %s", (template,))
               self.cache.set(cache_key, content, 3600)

           # Fetch user (also parameterized)
           user = self.db.query("SELECT * FROM users WHERE id = %s", (user_id,))

           # Simulate sending email
           print(f"Sent to {user['email']} via {self.config.host}:{self.config.port}")
   ```

   Wait, the original code fetches the user first, then checks cache. I'll keep the order similar but it doesn't matter much. I'll adjust to match original flow but fix SQL injection.

   Let's also show concrete implementations for clarity:
   ```python
   class PostgreSQLConnection:
       def query(self, sql: str, params: tuple = ()) -> Any: ...
       # ...

   class RedisClient:
       def exists(self, key: str) -> bool: ...
       def get(self, key: str) -> Any: ...
       def set(self, key: str, value: Any, ttl: int) -> None: ...
   ```

   Actually, I should keep it concise. The prompt just asks to refactor to use DI. I'll provide the refactored class with protocols, explain the benefits, and show instantiation.

   Key improvements to highlight:
   - Constructor injection instead of hardcoded instantiation
   - `typing.Protocol` for duck-typed interfaces (allows mocking for tests)
   - Configuration extracted to a dataclass
   - Fixed SQL injection vulnerability (bonus but important)
   - Type hints added
   - Clear separation of concerns

   One thing: The original code uses `self.db.query("SELECT * FROM users WHERE id = " + str(user_id))`. I'll change it to parameterized queries in the refactored version and explicitly mention it as a security improvement.

   Let's write out the full response.
   Structure:
   - Refactored code block
   - Explanation of changes
   - Example usage/instantiation
   - Benefits (testability, flexibility, security)
   - Note on DI containers for larger apps

   Check for correctness:
   - Protocols are correct for Python 3.8+
   - Dataclass is standard
   - Constructor injection is standard DI
   - Type hints are consistent
   - SQL injection fix is properly noted
   - All good.

   One minor adjustment: I'll make the `Database` and `Cache` protocols more explicit with `@runtime_checkable` if needed, but it's not strictly necessary for type checking. I'll keep it simple.

   I'll also add a note that in production, you'd typically use a DI framework or factory functions, but constructor injection is the foundation.

   Final code structure:
   ```python
   from typing import Protocol, Any
   from dataclasses import data
```

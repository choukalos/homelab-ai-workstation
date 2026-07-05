# Quality Test: Refactoring
## Prompt
Refactor Python class to use dependency injection...

## Output

```python
<think>

</think>

```python
class EmailService:
    def __init__(self, db, cache, smtp_host="smtp.example.com", smtp_port=587):
        self.db = db
        self.cache = cache
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port

    def send_notification(self, user_id, template):
        user = self.db.query("SELECT * FROM users WHERE id = " + str(user_id))
        if self.cache.exists(f"template:{template}"):
            content = self.cache.get(f"template:{template}")
        else:
            content = self.db.query(f"SELECT content FROM templates WHERE name = '{template}'")
            self.cache.set(f"template:{template}", content, 3600)
        # send email logic...
        print(f"Sent to {user['email']}")
```
```

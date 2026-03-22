---
name: block-vaultwarden-sqlite-writes
enabled: true
event: bash
action: block
pattern: sqlite3.*UPDATE.*users|sqlite3.*users.*UPDATE
---

BLOCKED: Direct SQLite write to Vaultwarden database detected.

Writing directly to the Vaultwarden SQLite database (especially the `users` table) is the root cause of PBKDF2/Argon2 hash format mismatches. Vaultwarden manages its own password hashing internally; raw SQL writes bypass this and can lock out all users.

Use the vault-sync API instead:

  /rotate-credential "<Item Name> to <new-password>"

This calls vault-sync POST /update which uses the Vaultwarden API to update credentials safely with proper hash computation.

If you need to inspect the database (read-only), use:
  ssh root@187.77.208.197 "sqlite3 /path/to/db.sqlite3 'SELECT id, email FROM users;'"

Never run UPDATE, INSERT, or DELETE against the Vaultwarden users table directly.

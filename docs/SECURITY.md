# Security

Secrets must exist only in `.env` or supported local token storage. Git ignores `.env`, token files, `secrets/`, `credentials/`, private data, SQLite databases, generated reports, and Obsidian metadata. No browser credential automation is implemented. Perform a secret scan before every push.

# Self-hosted fonts

Real, unmodified WOFF2 binaries for Inter and JetBrains Mono, both
distributed under the SIL Open Font License 1.1 (free to bundle and
self-host). Downloaded from the `@fontsource/inter` and
`@fontsource/jetbrains-mono` npm packages via jsdelivr:

- https://cdn.jsdelivr.net/npm/@fontsource/inter@5/files/
- https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5/files/

Bundled here (Command Center UI redesign, 2026-09-06) so the
dashboard's typography does not depend on an external Google Fonts
request at render time -- served by `monitoring/live_status_server.py`
via a real, read-only, GET-only `/static/fonts/<name>` route.

Upstream project pages (full license text):
- https://github.com/rsms/inter (Inter, SIL OFL 1.1)
- https://github.com/JetBrains/JetBrainsMono (JetBrains Mono, SIL OFL 1.1)

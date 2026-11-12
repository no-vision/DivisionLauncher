# Public client repository

Read docs/private-state-policy.txt before launcher/account changes. Keep this
checkout independently buildable; no sibling private repository is a dependency.
Only client-facing DTOs belong in src/Core. Preserve their
server wire/JSON contracts. Gameplay, admin, persistence, content and capture
sources belong in the private repositories. Never copy operator state or secrets.

Use tools/build.py and tools/verify.py. Release builds use path normalization and
omit GUI PDBs. Run tools/audit_publication.py before publication. Build/cache outputs
and adapter dependencies are ignored. Report native platform/GUI acceptance
separately from compile, fixture and cross-publish checks. Do not commit or push
without user authorization.

Keep task logs, generated review reports and workspace history outside this
repository. Repository documentation should describe operation and development.

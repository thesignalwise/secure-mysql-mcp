# Repository Guidelines

## Project Structure & Module Organization
Runtime code lives in `secure_mysql_mcp_server.py`, which exposes the MCP server, connection pools, and tool handlers. `encrypt_password.py` houses encryption helpers and safety checks, while `test_client.py` provides an interactive CLI harness for manual verification. Configuration stays in `config/` (keep `servers.json` private); `servers.example.json` is the canonical template. Tests sit alongside the tooling in `test_encryption.py` and the interactive client, and `requirements.txt` locks dependency parity.

## Build, Test, and Development Commands
```bash
pip install -r requirements.txt        # sync dependencies
python secure_mysql_mcp_server.py      # launch MCP server (uses config/servers.json)
python secure_mysql_mcp_server.py config/dev.json  # point to an alternate config
python test_client.py                  # start interactive client; type `test` inside to run scripted checks
python test_encryption.py              # run standalone encryption validation suite
python encrypt_password.py config/servers.json    # encrypt plaintext passwords in-place
```

## Coding Style & Naming Conventions
Use 4-space indentation, Python type hints, and module-level `logging` (see `logging.basicConfig` in the server). Prefer snake_case for functions, async methods, and variables; reserve PascalCase for classes such as `PasswordManager`. Document coroutine side effects succinctly, and when adding tools register them in `_setup_handlers()` with names that mirror the user-facing MCP identifiers.

## Testing Guidelines
`test_encryption.py` is the regression suite for password handling; ensure it passes after changes touching `encrypt_password.py` or config parsing. The interactive `test_client.py` doubles as a smoke test—exercise `list`, `connect`, `sql`, and `test` flows against a disposable database. Add scenario-specific helpers near the top of `test_client.py` instead of writing new scripts, and document notable output changes in PRs.

## Commit & Pull Request Guidelines
Follow the concise, imperative style already in history (e.g., `add pool metrics`). Scope commits narrowly, reference affected modules in the body, and never commit populated `config/*.json`. PRs should include a purpose summary, notable commands run (`python test_encryption.py`, `test` in client), and screenshots/log snippets when UX or logging changes. Link issues or MCP tickets when applicable and flag any security-impacting change explicitly.

## Security & Configuration Tips
Always run the server with encrypted credentials: `python encrypt_password.py` updates configs and toggles the `encrypted` flag. Store encryption keys outside the repo, tighten permissions (`chmod 600 config/servers.json`), and ensure default configs keep `enabled` false for nonlocal servers. Confirm new tooling enforces `permissions` checks so Claude agents cannot escalate privileges.

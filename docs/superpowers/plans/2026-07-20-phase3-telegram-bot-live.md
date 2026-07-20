# Phase 3 Telegram Bot Live Implementation Plan

> **For agentic workers:** Execute task-by-task, verifying tests/boot after each. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the already-built Telegram Mini App integration actually work in production — the bot's `/start` command and its "Open Copy Trading" WebApp button currently have no deployment target, so the whole flow is dead despite being fully coded.

**Context (investigation, not brainstorming — per standing instruction to keep phases lean):**
- `server/bot.py` already implements `/start`: creates a wallet via `ensure_user_wallet`, sends a WebApp button pointing at `WEBAPP_URL`.
- `frontend/index.html` already detects Telegram Mini App mode (`isTelegramMode()`) and sends `X-Telegram-Init-Data` for auth.
- `server/api.py`'s `verify_privy_token` already verifies that header via HMAC (`_verify_telegram_init_data`), from before Phase 1.
- **The gap:** `render.yaml` has exactly two services (`arctic-whale-agent-service`, `arctic-whale-api`) — no worker runs `server/bot.py`. `bot.polling()` (blocking, no auto-retry) is also the wrong call for an unattended worker — `infinite_polling()` is telebot's documented production-safe variant (catches and retries on network errors instead of dying).
- Frontend loading/error-state coverage was checked and found already solid (22 catch blocks / 21 fetch call sites) — no separate "frontend polish" task needed this phase.

**Architecture:** Add a third Render service (`worker`, not `web` — no HTTP port) running `python server/bot.py`, switch to `infinite_polling()`, and guard startup the same way other scripts in this repo guard on missing config (fail loud, not silently no-op).

**Tech Stack:** Python 3.12, pyTelegramBotAPI, Render background worker.

---

### Task 1: Bot resilience — `infinite_polling`, explicit config guard

**Files:**
- Modify: `server/bot.py`
- Test: `tests/test_bot_startup.py` (create)

- [ ] **Step 1: Write a failing test**

Create `tests/test_bot_startup.py`:

```python
import pytest


def test_bot_module_requires_bot_token(monkeypatch):
    monkeypatch.setattr("backend.config.BOT_TOKEN", "")
    import importlib
    import server.bot as bot_module
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        importlib.reload(bot_module)
```

- [ ] **Step 2: Run it**

Run: `venv/bin/python -m pytest tests/test_bot_startup.py -v`
Expected: this should currently PASS already (the `if not BOT_TOKEN: raise RuntimeError(...)` guard already exists at module level) — this step is a regression-pin, not a new-behavior test. Confirm it passes before continuing; if it fails, stop and investigate rather than proceeding.

- [ ] **Step 3: Switch `bot.polling()` to `bot.infinite_polling()`**

In `server/bot.py`, replace the last line:

```python
bot.polling()
```

with:

```python
bot.infinite_polling()
```

- [ ] **Step 4: Verify import still works, run full suite**

Run: `venv/bin/python -c "import ast; ast.parse(open('server/bot.py').read()); print('parses ok')"`
Run: `venv/bin/python -m pytest -q` — expected: all pass (110 + 1 new).

- [ ] **Step 5: Commit**

```bash
git add server/bot.py tests/test_bot_startup.py
git commit -m "fix: use infinite_polling for production-safe Telegram bot resilience"
```

---

### Task 2: Deploy the bot as a Render worker

**Files:**
- Modify: `render.yaml`
- Modify: `README.md`

- [ ] **Step 1: Add the worker service to `render.yaml`**

Add a third service under `services:`, after `arctic-whale-api`:

```yaml
  - type: worker
    name: arctic-whale-telegram-bot
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: python server/bot.py
    envVars:
      - key: PYTHON_VERSION
        value: 3.11.0
      - key: BOT_TOKEN
        sync: false
      - key: WEBAPP_URL
        sync: false            # Set to the arctic-whale-api or Vercel frontend URL after first deploy
      - key: DATABASE_URL
        fromDatabase:
          name: arctic-whale-db
          property: connectionString
      - key: CIRCLE_API_KEY
        sync: false
      - key: CIRCLE_ENTITY_SECRET
        sync: false
      - key: AGENT_SERVICE_URL
        sync: false
      - key: AGENT_SERVICE_SECRET
        sync: false
```

(`type: worker` — not `web` — since this is a long-lived polling process with no HTTP port to bind; Render worker services don't need a `$PORT`.)

- [ ] **Step 2: Document it in README**

In the "Deployment Split" → "Telegram Bot" section of `README.md`, replace the existing paragraph:

```markdown
Run one production bot only on Render.

- Point `WEBAPP_URL` at the production web app URL
- Keep staging testing inside the browser or directly through the staging web URL
- Do not run a second bot token unless you later want Telegram staging
```

with:

```markdown
Run one production bot only, as the `arctic-whale-telegram-bot` Render **worker** service
(not a web service — it has no HTTP port, it long-polls Telegram).

- Point `WEBAPP_URL` at the production web app URL
- Keep staging testing inside the browser or directly through the staging web URL
- Do not run a second bot token unless you later want Telegram staging
- The worker needs its own `DATABASE_URL` and Circle credentials since `ensure_user_wallet`
  provisions a wallet directly on `/start`, the same as the API service does on signup
```

- [ ] **Step 3: Verify and commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add render.yaml README.md
git commit -m "feat: deploy Telegram bot as a Render worker service"
```

---

## Self-review

- **Spec coverage:** the concrete gap found (bot has no deployment target) is fully addressed — Task 1 makes the process resilient, Task 2 gives it somewhere to run.
- **Scope discipline:** deliberately did NOT bundle a "frontend polish" task — investigation showed the frontend's loading/error-state coverage is already solid, so inventing polish work would be scope creep for its own sake, not a real gap. If the user wants frontend work, that's a distinct, separately-scoped follow-up.
- **No placeholders:** both tasks have complete code and exact commands.

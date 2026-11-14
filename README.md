# Corporate Event Management API

A REST API for **Acme Corp's internal event system**: employees can create and manage events (workshops, product launches, team-building activities, conferences), register for them, and cancel their registrations.

Built with **FastAPI**, **PostgreSQL**, **SQLAlchemy 2.0 (async)**, **Pydantic v2** and **JWT** authentication, and tested with **pytest**.

| Feature | Status |
|---|---|
| Event CRUD (`/events`) | ✅ |
| Registrations with business rules (no duplicates, capacity limit, 404 for unknown events) | ✅ |
| Pydantic validation, correct HTTP status codes, Swagger docs | ✅ |
| **Bonus:** pagination (`skip`, `limit`) | ✅ |
| **Bonus:** search and filtering (`title`, `date`) | ✅ |
| **Bonus:** JWT authentication | ✅ |
| **Bonus:** asynchronous database operations (async engine and `AsyncSession`) | ✅ |
| **Bonus:** pytest unit tests (75 tests, real PostgreSQL) | ✅ |
| Optional demo frontend (`/app`) | ✅ |

---

## 1. Project structure

```
event-management-api/
├── db.py              # Settings (.env), async engine, session factory, get_db, init_db
├── models.py          # SQLAlchemy models: User, Event, Registration
├── schemas.py         # Pydantic request/response schemas and validation rules
├── services.py        # Business logic and DB operations, domain exceptions
├── auth.py            # Password hashing, JWT create/verify, get_current_user dependency
├── main.py            # FastAPI app: routes, exception handlers, OpenAPI metadata
├── frontend/
│   └── index.html     # Optional demo UI (plain HTML/JS), served at /app
├── tests/             # pytest suite (runs against a PostgreSQL test database)
├── docs/              # Code walkthrough, demo-video script, AI usage, screenshots
├── requirements.txt       # Runtime dependencies
├── requirements-dev.txt   # + test dependencies
├── pytest.ini
└── .env.example       # Template for your local .env
```

**How a request flows:**
`main.py` (route: validates input, calls one service) → `services.py` (business rules, raises domain exceptions) → `models.py` / `db.py` (SQL through the async session) → back to `main.py`, where `response_model` shapes the JSON. Exception handlers in `main.py` turn domain exceptions into 404 / 409 / 401.

Routes contain no business logic, and services contain no HTTP. That separation is why the service functions can be tested and reused independently of FastAPI.

---

## 2. Requirements

- Python **3.11+** (developed with 3.13)
- PostgreSQL **14+** (developed with 16)
- Git

---

## 3. Installation

```bash
git clone https://github.com/<your-username>/event-management-api.git
cd event-management-api

python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -r requirements.txt          # app only
pip install -r requirements-dev.txt      # app + test tools
```

---

## 4. Database configuration

1. **Create the databases** (one for the app, one for the tests) in `psql` or pgAdmin:

   ```sql
   CREATE DATABASE event_management;
   CREATE DATABASE event_management_test;
   ```

2. **Create your `.env`** from the template and edit it:

   ```bash
   cp .env.example .env        # Windows: copy .env.example .env
   ```

   | Variable | Meaning | Example |
   |---|---|---|
   | `DATABASE_URL` | Async SQLAlchemy URL of the app database | `postgresql+asyncpg://postgres:postgres@localhost:5432/event_management` |
   | `JWT_SECRET_KEY` | Secret used to sign tokens. **Must be long and random.** | output of `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
   | `JWT_ALGORITHM` | Signing algorithm | `HS256` |
   | `ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime | `60` |
   | `APP_TIMEZONE` | Time zone used to decide the calendar day for `?date=` | `UTC` or `Europe/Rome` |
   | `SQL_ECHO` | Print every SQL statement | `false` |

3. **Tables are created automatically** when the server starts (`init_db()` in `db.py`), so there is no manual SQL to run.

---

## 5. Running the API

```bash
uvicorn main:app --reload
```

| URL | What |
|---|---|
| http://127.0.0.1:8000/docs | Swagger UI (interactive documentation) |
| http://127.0.0.1:8000/redoc | ReDoc documentation |
| http://127.0.0.1:8000/app/ | Demo frontend (`/` redirects here) |

### Using protected endpoints in Swagger

1. `POST /auth/register` with an e-mail, full name and password (8+ characters).
2. Click **Authorize** (top right), enter your e-mail as *username* and your password, then click *Authorize*.
3. Swagger now sends `Authorization: Bearer <token>` with every request, so `POST/PUT/DELETE /events` work.

---

## 6. API reference

| Method | Endpoint | Auth | Success | Errors |
|---|---|---|---|---|
| POST | `/auth/register` | – | 201 | 409 e-mail taken, 422 |
| POST | `/auth/login` (form: `username`, `password`) | – | 200 token | 401 |
| GET | `/auth/me` | 🔒 | 200 | 401 |
| POST | `/events` | 🔒 | 201 | 401, 422 |
| GET | `/events?skip=&limit=&title=&date=` | – | 200 + `X-Total-Count` | 422 |
| GET | `/events/{id}` | – | 200 | 404, 422 |
| PUT | `/events/{id}` (partial update) | 🔒 | 200 | 401, 404, 409, 422 |
| DELETE | `/events/{id}` | 🔒 | 204 | 401, 404 |
| POST | `/events/{id}/registrations` | – | 201 | 404, 409, 422 |
| GET | `/events/{id}/registrations?skip=&limit=` | – | 200 + `X-Total-Count` | 404, 422 |
| DELETE | `/registrations/{id}` | – | 204 | 404 |

### Business rules

| Rule | Where | Response |
|---|---|---|
| Cannot register for a non-existent event | `services.register_for_event` | **404** |
| Same employee (e-mail, case-insensitive) cannot register twice for one event | service check and `UNIQUE(event_id, employee_email)` | **409** |
| No registrations beyond `max_capacity`, even under concurrent requests | `SELECT … FOR UPDATE` row lock and a count check | **409** |
| `max_capacity` cannot be lowered below the current number of registrations | `services.update_event` | **409** |
| Deleting an event deletes its registrations | `ON DELETE CASCADE` | 204 |

### Validation rules (→ 422)

- `title`, `location`: required, 1–200 characters, whitespace-only rejected
- `event_date`: valid ISO-8601 datetime **in the future**; a datetime without a time zone is treated as UTC
- `max_capacity`: integer, `> 0` (and ≤ 100,000)
- Unknown fields are rejected (`extra="forbid"`), so typos don't go unnoticed
- `PUT` needs at least one field and cannot set a required field to `null`
- Registration: `employee_name` 1–120 characters, `employee_email` a valid e-mail (stored in lower case)
- Pagination: `skip ≥ 0`, `1 ≤ limit ≤ 100` (default 10); `date` must be `YYYY-MM-DD`

### Examples

```bash
# Create an event (needs a token)
curl -X POST http://127.0.0.1:8000/events \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"Intro to FastAPI","description":"Hands-on workshop","event_date":"2026-12-01T10:00:00+01:00","location":"Room B","max_capacity":30}'

# Search and filter, combined with pagination
curl "http://127.0.0.1:8000/events?title=python&date=2026-12-01&skip=0&limit=10"

# Register
curl -X POST http://127.0.0.1:8000/events/1/registrations \
  -H "Content-Type: application/json" \
  -d '{"employee_name":"Ana Rossi","employee_email":"ana.rossi@acme.com"}'
```

---

## 7. Running the tests

The tests use a **real PostgreSQL database** (`event_management_test`), not SQLite, so PostgreSQL-specific behaviour (row locks, `ILIKE`, time zones) is what gets tested. Every test starts with empty tables. As a safety check, the suite refuses to run if the database name does not contain `test`.

```bash
pip install -r requirements-dev.txt

# Uses postgresql+asyncpg://postgres:postgres@localhost:5432/event_management_test by default.
# Override if your credentials differ:
export TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@localhost:5432/event_management_test"
# Windows PowerShell:  $env:TEST_DATABASE_URL = "postgresql+asyncpg://..."

pytest            # short summary
pytest -v         # one line per test
```

Expected result: **`75 passed`**.

| File | What it covers |
|---|---|
| `test_events.py` | CRUD happy paths, 404s, cascade delete, 401 on protected writes |
| `test_registrations.py` | register, list, cancel, duplicate (409), full event (409), unknown event (404), capacity reduction (409), **concurrency race** |
| `test_validation.py` | every validation rule returns 422 |
| `test_pagination_filtering.py` | skip/limit, bounds, `X-Total-Count`, title/date filters, time zones, combined filters |
| `test_auth.py` | sign-up, login, wrong password, expired / forged / malformed tokens |

---

## 8. Design decisions

- **`PUT` is a partial update.** Only the fields you send are changed. This is more convenient for clients and the frontend; the trade-off is that it is closer to the semantics of `PATCH`.
- **Registrations are identified by e-mail and are public.** The brief only requires authentication for event create/update/delete, so employees can register without logging in. In a real deployment registration would be tied to the logged-in user (see *Limitations*).
- **The row lock prevents over-booking.** Without it, two requests for the last seat could both read "1 seat left" and both insert. `test_concurrent_registrations_never_exceed_capacity` reproduces this race: it fails if the lock is removed.
- **Domain exceptions instead of `HTTPException` in services.** The service layer stays HTTP-agnostic, and status codes are decided in one place (`main.py`).
- **The registration count is computed inside the event query** (a correlated subquery, `column_property`), so listing N events costs one SQL query, not N+1.
- **Async all the way down:** `create_async_engine` + `asyncpg`, `AsyncSession`, `async def` services and routes. While one request waits on PostgreSQL, the server keeps handling others.

## 9. Limitations and possible improvements

- Uses `create_all` instead of **Alembic migrations**, so schema changes to existing tables are not applied automatically.
- Any logged-in user can edit or delete any event; there is no ownership or role model (e.g. an "organizer" role).
- Registration and cancellation are not tied to the authenticated user.
- No refresh tokens or token revocation (logout only deletes the token on the client).
- No rate limiting on `/auth/login`.

---

## 10. Submission material

- Swagger screenshots: [`docs/screenshots/`](docs/screenshots/)
- Code walkthrough (how every file works): [`docs/CODE_WALKTHROUGH.md`](docs/CODE_WALKTHROUGH.md)
- Demo-video outline: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)
- AI usage documentation: [`docs/AI_USAGE.md`](docs/AI_USAGE.md)

## Git workflow

`main` is the stable branch. Each feature was developed on its own branch and merged with `--no-ff`, so the merge history stays visible:
`feature/project-setup` → `feature/event-crud` → `feature/registrations` → `feature/pagination-filtering` → `feature/jwt-auth` → `feature/unit-tests` → `feature/frontend` → `feature/documentation`.

```bash
git log --oneline --graph --all
```

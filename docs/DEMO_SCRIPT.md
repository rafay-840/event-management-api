# Demo Video Outline (about 12–15 minutes)

The brief asks for eight points. Each one maps to a section below. Speak in your own words; this is a checklist, not text to read aloud.

**Before recording:** PostgreSQL running, `.env` set with a real `JWT_SECRET_KEY`, the app database empty (or with 2–3 nice events), two terminals open, Swagger and pgAdmin/psql ready, editor font enlarged.

---

### 1. Introduction (≈1 min) · *brief: "introduction and purpose"*
- Acme Corp needs an internal system for events: workshops, launches, team building, conferences.
- The API lets employees create and manage events, register, and cancel. Built with FastAPI, PostgreSQL, async SQLAlchemy 2.0, Pydantic and JWT; tested with pytest.
- It builds on the Bookstore project: same layered structure, plus business rules, auth and tests.

### 2. Project structure (≈2 min) · *"structure and how files work together"*
- Show the tree. One sentence per file: `db.py`, `models.py`, `schemas.py`, `services.py`, `auth.py`, `main.py`, `frontend/`, `tests/`.
- Trace **one request**: `POST /events/1/registrations` → route in `main.py` → `RegistrationCreate` validates → `get_db` gives a session → `services.register_for_event` → SQL → response or domain exception → handler → 409/404.
- Point out that routes have no logic and services have no HTTP. Show the exception classes and the three handlers.

### 3. Database and integration (≈2 min) · *"database and its integration"*
- `.env` → `Settings` → `create_async_engine(postgresql+asyncpg://…)` → `get_db`.
- Start the server and show the tables in pgAdmin/psql (`\d events`, `\d registrations`): the CHECK constraint, the UNIQUE constraint and the FK with ON DELETE CASCADE.
- Explain `init_db` / `create_all` and say that Alembic would be the production choice.

### 4. Key functions and business logic (≈3 min) · *"important functions and business logic"*
- `register_for_event`: the three rules in order. Explain **why `FOR UPDATE`** with the "last concert ticket" example.
- **Optional live proof (strong moment):** remove `.with_for_update()` and run
  `pytest tests/test_registrations.py -k concurrent` to watch it fail (ten 201s), then put it back and watch it pass.
- `update_event`: partial update with `exclude_unset`, and capacity cannot drop below the registration count.
- `list_events`: `ILIKE` with escaping, the half-open date range, stable `ORDER BY`, `X-Total-Count`.
- `auth.py`: bcrypt, the JWT structure, `get_current_user`.

### 5. Running the application (≈30 s) · *"application running"*
- `uvicorn main:app --reload` and open `/docs`.

### 6. Test every endpoint in Swagger (≈4 min) · *"all endpoints, requests and responses"*
Follow this order (it doubles as your screenshot list):
1. `POST /events` **without** a token → **401**
2. `POST /auth/register` → 201; register again → **409**
3. **Authorize** button → `POST /auth/login` with a wrong password → **401**, correct password → token. `GET /auth/me` → 200
4. `POST /events` → **201** (capacity 2); invalid body (empty title, past date, capacity 0) → **422**
5. `GET /events` → 200; `?skip=0&limit=1` and show the `X-Total-Count` header; `?title=fast`; `?date=YYYY-MM-DD`
6. `GET /events/{id}` → 200; `/events/9999` → **404**
7. `PUT /events/{id}` with `{"location": "Auditorium"}` → 200 (other fields unchanged)
8. `POST /events/{id}/registrations` ×2 → 201; same e-mail again → **409 duplicate**; a third person → **409 full**; `/events/9999/registrations` → **404**
9. `PUT` with `max_capacity: 1` → **409** (2 already registered)
10. `GET /events/{id}/registrations` → list; `GET /events/{id}` → `available_seats: 0`
11. `DELETE /registrations/{id}` → **204**; again → **404**
12. `DELETE /events/{id}` → **204**; then `GET` → **404**

### 7. Tests (≈1 min)
- `pytest -v` → **75 passed**. Name the test files and what each covers. Mention: real PostgreSQL, fresh tables per test, `dependency_overrides`.

### 8. Key concepts and decisions (≈1.5 min) · *"key concepts and implementation decisions"*
- Defence in depth (Pydantic → services → DB constraints)
- Domain exceptions vs `HTTPException`
- Avoiding N+1 with `column_property`
- Async: why `asyncpg`, why `expire_on_commit=False`, bcrypt in `asyncio.to_thread`
- PUT as a partial update (trade-off)
- Limitations and what you'd add next (Alembic, roles, refresh tokens)

### 9. Frontend (≈1 min) · *"frontend interacting with the API"*
- Open `/app/`: browse and search, log in, create an event, register people until it's full (the 409 toast), cancel one, edit, delete. Open the browser DevTools Network tab once to show the real API calls and the Bearer header.

### Closing (≈15 s)
- Point to the GitHub repo, the feature-branch history (`git log --oneline --graph`) and the README.

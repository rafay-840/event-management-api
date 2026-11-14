# Code Walkthrough: How the Event Management API Works

This is a study guide. The goal is that you can explain **every line** in the demo video and answer follow-up questions. Read it with the code open beside it. Each section goes: *intuition → mechanism → where in the code → what would break without it*.

---

## Part 0: The big picture in one paragraph

A client sends an HTTP request. **FastAPI** matches it to a route in `main.py`, parses and validates the input with **Pydantic** (`schemas.py`), opens a database session through a **dependency** (`db.get_db`), and calls one **service** function (`services.py`). The service applies the business rules and talks to **PostgreSQL** through **SQLAlchemy** models (`models.py`). It either returns ORM objects, which FastAPI converts to JSON using the route's `response_model`, or it raises a **domain exception**, which an exception handler in `main.py` converts to 404, 409 or 401. Protected routes first run `auth.get_current_user`, which verifies a **JWT**.

```
HTTP request
   │
   ▼
main.py  ── route: path/query/body validated by Pydantic (422 if invalid)
   │         Depends(get_db)            → one AsyncSession per request
   │         Depends(get_current_user)  → 401 if no/invalid token
   ▼
services.py ── business rules, SQL via SQLAlchemy, raises domain exceptions
   │
   ▼
models.py / db.py ── tables, async engine, asyncpg → PostgreSQL
   │
   ▼
main.py ── response_model shapes JSON  |  exception handlers → 404/409/401
```

**Why layers?** Each file has one reason to change. If tomorrow the API becomes a CLI tool, `services.py` stays as it is. If you swap PostgreSQL for MySQL, only the URL in `db.py` changes. This is the *separation of concerns* the rubric's M1 criterion checks.

---

## Part 1: `db.py` (configuration and connection)

### 1.1 `Settings` (pydantic-settings)
- **Intuition:** passwords and secrets must not live in the code (they would end up on GitHub).
- **Mechanism:** `BaseSettings` reads each field from an environment variable with the same name (case-insensitive), or from `.env`. `database_url` ← `DATABASE_URL`. Defaults are used only when the variable is missing.
- `.env` is in `.gitignore`; `.env.example` is the committed template.

### 1.2 The async engine
```python
engine = create_async_engine(settings.database_url, echo=..., pool_pre_ping=True)
```
- The **engine** owns a **connection pool**. Opening a TCP connection to PostgreSQL and authenticating is slow (milliseconds), so connections are reused.
- `postgresql+asyncpg://` selects the **asyncpg** driver, which speaks the PostgreSQL protocol without blocking the event loop.
- `pool_pre_ping=True`: before handing out a pooled connection, check that it is still alive (for example after a DB restart).

### 1.3 Sessions and `expire_on_commit=False`
- A **session** is a *unit of work*: it tracks the objects you load and change, then `commit()` writes them in one transaction.
- By default SQLAlchemy **expires** every object after commit, so the next attribute access reloads it from the DB. With `AsyncSession` that reload would be an *implicit synchronous I/O call*, which raises `MissingGreenlet`. `expire_on_commit=False` prevents that. When we really need fresh values we call `await db.refresh(obj)` explicitly.

### 1.4 `get_db`: a dependency with cleanup
```python
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```
- FastAPI runs the code **before** `yield` when the request starts, injects `session` into the route, and runs the code **after** `yield` (here: exiting `async with`, which closes the session) when the response is done, **even if an exception happened**. That guarantees no connection leaks.
- **One session per request** means requests never share transaction state.

### 1.5 `init_db`
- `Base.metadata.create_all` issues `CREATE TABLE IF NOT EXISTS …` for every model. It is a **sync** API, so it runs through `connection.run_sync(...)`.
- *Limitation to mention:* it never **alters** existing tables. Real projects use **Alembic** migrations.

---

## Part 2: `models.py` (tables)

### 2.1 SQLAlchemy 2.0 typed mapping
```python
title: Mapped[str] = mapped_column(String(200), index=True)
description: Mapped[str | None] = mapped_column(Text)
```
- `Mapped[str]` → `NOT NULL`; `Mapped[str | None]` → nullable. The Python type *is* the nullability declaration.
- `index=True` creates a B-tree index, which speeds up `WHERE`/`ORDER BY` on that column. We index `title`, `event_date` (we filter and sort by it) and `registrations.event_id` (we join and count by it).

### 2.2 Constraints: the database as the last line of defence
- `CheckConstraint("max_capacity > 0")`: even if someone bypasses the API (manual SQL), the DB refuses invalid capacity.
- `UniqueConstraint("event_id", "employee_email")`: the *duplicate registration* rule enforced by PostgreSQL itself. It still holds if two identical requests arrive within the same millisecond.
- `ForeignKey("events.id", ondelete="CASCADE")`: deleting an event deletes its registrations **inside PostgreSQL**. `passive_deletes=True` on the relationship tells SQLAlchemy "don't load the children to delete them one by one; the DB will do it".

**Principle (defence in depth):** Pydantic validates the *shape* of input, services enforce *business rules* and produce good error messages, and DB constraints guarantee *integrity* no matter what.

### 2.3 `TimestampMixin`
`server_default=func.now()` means PostgreSQL fills `created_at` (the clock of the DB, not of the Python process). `onupdate=func.now()` means SQLAlchemy adds `updated_at = now()` to every `UPDATE` it emits.

### 2.4 `registration_count` with `column_property` (the N+1 problem)
```python
Event.registration_count = column_property(
    select(func.count(Registration.id))
    .where(Registration.event_id == Event.id)
    .correlate_except(Registration)
    .scalar_subquery()
)
```
- **The problem:** listing 10 events and then counting registrations for each = 1 + 10 queries (**N+1**). With 100 events that is 101 round trips.
- **The solution:** a *correlated scalar subquery* embedded in the event's `SELECT`:
  ```sql
  SELECT events.*, (SELECT count(registrations.id) FROM registrations
                    WHERE registrations.event_id = events.id) AS registration_count
  FROM events ORDER BY event_date LIMIT 10;
  ```
  That is one query for any number of events.
- `correlate_except(Registration)` means "inside the subquery, `events` refers to the *outer* row".
- It is assigned **after** the `Registration` class because the expression needs that class to exist. (A bare `registration_count: Mapped[int]` annotation inside `Event` would have created a real column. That bug was caught during development by inspecting `Event.__table__.c`.)
- `available_seats` is a Pydantic `computed_field` in `EventResponse`: derived data, not stored.

---

## Part 3: `schemas.py` (validation)

### 3.1 Why separate Create / Update / Response schemas?
| Schema | Purpose | Why it differs |
|---|---|---|
| `EventCreate` | input of POST | all required fields; the client must **not** send `id`, `created_at` |
| `EventUpdate` | input of PUT | everything optional (partial update) |
| `EventResponse` | output | includes `id`, timestamps, seat counts; for users, **never** the password hash |

Using the ORM model as input would let clients set fields such as `id` (a *mass-assignment* vulnerability).

### 3.2 The validation tools used
| Rule | Tool |
|---|---|
| Title not empty, not just spaces | `str_strip_whitespace=True` + `Field(min_length=1)` |
| Capacity > 0 | `Field(gt=0)` |
| Date valid | the `datetime` type (Pydantic parses ISO 8601; "2026-02-30" fails) |
| Date in the future, time zone normalised | `FutureDatetime = Annotated[datetime, AfterValidator(validate_event_date)]` |
| Valid e-mail, lower case | `LowercaseEmail = Annotated[EmailStr, AfterValidator(str.lower)]` |
| No unknown fields | `ConfigDict(extra="forbid")` |
| Update has ≥ 1 field, no `null` on required columns | `@model_validator(mode="after")` |

- `Annotated[type, AfterValidator(fn)]` builds a **reusable type**: write the rule once and use it in several schemas (this is the DRY point for code quality).
- *After* validator = runs after Pydantic has already converted the string to a `datetime`.
- `model_validator(mode="after")` sees the **whole model**, which is needed for rules involving several fields or `model_fields_set` (the set of fields the client actually sent).

### 3.3 `model_fields_set` and `exclude_unset`: how partial update works
Body `{"location": "Auditorium"}` → `model_fields_set == {"location"}`. In the service, `data.model_dump(exclude_unset=True)` → `{"location": "Auditorium"}`. Only that attribute is set. Without `exclude_unset` every other field would be `None` and would overwrite the stored values.

`{"description": null}` **is** sent, so it clears the description (allowed, since that column is nullable). `{"title": null}` is rejected by the model validator.

### 3.4 Why naive datetimes become UTC
"2026-12-01T10:00" without an offset is ambiguous: 10:00 where? We pick UTC so that every stored value is an absolute instant. The column is `TIMESTAMP WITH TIME ZONE`, which PostgreSQL stores as UTC.

### 3.5 Where does the 422 come from?
FastAPI validates the body against the schema **before** calling your function. On failure it returns `422` with a list of errors (`loc`, `msg`, `type`). None of our code runs.

---

## Part 4: `services.py` (business logic)

### 4.1 Domain exceptions
```
ServiceError
├── NotFoundError        → 404   (EventNotFoundError, RegistrationNotFoundError)
├── ConflictError        → 409   (DuplicateRegistrationError, EventFullError,
│                                 CapacityBelowRegistrationsError, EmailAlreadyRegisteredError)
└── AuthenticationError  → 401   (InvalidCredentialsError)
```
- Services raise *meaningful* exceptions; `main.py` has **three handlers**, one per base class. Adding a new 404 case needs no change in `main.py`.
- *Alternative:* raising `HTTPException` directly in services is simpler, but it couples business logic to HTTP.

### 4.2 `register_for_event`: the most important function
The three rules, in order:
1. **Event exists?** `SELECT max_capacity FROM events WHERE id = :id FOR UPDATE` → `None` means 404.
2. **Already registered?** Look for a row with the same `(event_id, email)` → 409.
3. **Seat free?** `count(*) >= capacity` → 409.
Then insert and commit.

Duplicate is checked **before** capacity: if a registered employee retries on a full event, "already registered" is the more useful message.

### 4.3 The race condition and `SELECT … FOR UPDATE`
**Intuition:** two people grab the last concert ticket at the same moment. Both see "1 left", both pay, and the venue is over-sold.

**In our system without a lock** (READ COMMITTED, PostgreSQL's default isolation level):
```
Request A: count → 0 (capacity 1)       Request B: count → 0
Request A: INSERT, COMMIT               Request B: INSERT, COMMIT   → 2 people, capacity 1 ❌
```
The UNIQUE constraint doesn't help: they are *different* employees.

**With `FOR UPDATE`:** the first transaction to read the event row **locks** it. The second transaction's `SELECT … FOR UPDATE` on the same row **waits** until the first commits or rolls back. It then reads the updated state, its count sees 1, and it gets 409. Registrations for the *same* event become serialized; different events don't block each other (it is a row lock, not a table lock).

**Proof:** `test_concurrent_registrations_never_exceed_capacity` fires 10 concurrent requests at a 1-seat event and slows the gap between count and insert (via `monkeypatch`) to make the race window large. With the lock: `[201, 409×9]`. With `.with_for_update()` removed, the test **fails** with ten 201s. This was verified during development; you can repeat the experiment live in the demo video.

**Alternatives you could mention:**
- An atomic conditional insert: `INSERT … SELECT … WHERE (SELECT count(*) …) < capacity`. It needs no explicit lock but is harder to read.
- `SERIALIZABLE` isolation plus retry on serialization failure.
- A `seats_taken` counter column with `UPDATE events SET seats_taken = seats_taken + 1 WHERE id = :id AND seats_taken < max_capacity`, checking the row count. This is very scalable, but the counter is denormalized data that must be kept in sync.

`update_event` also locks the row (`db.get(..., with_for_update=True)`), so lowering the capacity cannot race with a registration.

### 4.4 `IntegrityError` safety net
If, despite the check, PostgreSQL's UNIQUE constraint fires, we catch `IntegrityError`, **roll back** (a failed transaction must be rolled back before the session can be used again) and raise the same 409. The same pattern is used in `create_user` for duplicate e-mails.

### 4.5 `list_events`: filtering and pagination
- **Title:** `Event.title.icontains(title, autoescape=True)` → `title ILIKE '%' || :p || '%' ESCAPE '/'`. `autoescape` makes `%` and `_` typed by the user match literally (searching "100%" doesn't match everything).
- **Date:** a *half-open interval* `start <= event_date < start + 1 day`, where `start` is midnight in `APP_TIMEZONE`.
  - Why not `DATE(event_date) = :d`? Applying a function to the column prevents PostgreSQL from using the index on `event_date`, and `DATE()` would use the DB session's time zone.
  - Why half-open? It contains 23:59:59.999 without double-counting the next midnight.
- **Pagination:** `OFFSET skip LIMIT limit` with a **stable order** (`event_date, id`). Without a deterministic `ORDER BY`, PostgreSQL may return rows in a different order on each query, so pages could repeat or skip rows. `id` breaks ties.
- **Total:** a separate `count(*)` with the same filters, returned in the `X-Total-Count` header, so the client can compute the number of pages while the response body stays a plain list.
- *Trade-off:* `OFFSET` gets slow for deep pages (PostgreSQL still walks the skipped rows). The alternative is **keyset/cursor pagination** (`WHERE (event_date, id) > (:last_date, :last_id)`).

---

## Part 5: `auth.py` (JWT authentication)

### 5.1 Password hashing with bcrypt
- **Never store passwords.** Store `bcrypt(password, random_salt)`.
- **Salt:** random bytes mixed in, so identical passwords produce different hashes. This defeats precomputed rainbow tables.
- **Slowness on purpose:** bcrypt has a cost factor (default 12 → 2¹² rounds). A login costs about 0.2 s, which is fine for a user and terrible for an attacker trying billions of guesses.
- bcrypt only uses the first **72 bytes**, so passwords are capped at 64 characters in `UserCreate`.

### 5.2 What a JWT is
`header.payload.signature`, each part base64url-encoded:
```
{"alg":"HS256","typ":"JWT"} . {"sub":"1","iat":...,"exp":...} . HMAC-SHA256(header.payload, SECRET)
```
- The payload is **readable by anyone** (it is encoded, not encrypted), so never put secrets in it.
- The **signature** proves the server issued it and nobody changed it: altering `sub` from 1 to 2 invalidates the signature.
- **Stateless:** the server stores no sessions. Validity = correct signature + `exp` in the future.
- `algorithms=[settings.jwt_algorithm]` in `decode` **pins the algorithm**, which blocks the classic `alg: none` attack.
- `options={"require": ["exp", "sub"]}` rejects tokens missing these claims.

### 5.3 The OAuth2 password flow in FastAPI
- `OAuth2PasswordBearer(tokenUrl="/auth/login")` does two things: (1) as a dependency it extracts the token from `Authorization: Bearer …` and returns **401** if the header is missing; (2) it adds the **Authorize** button to Swagger.
- `/auth/login` uses `OAuth2PasswordRequestForm` (form fields `username`, `password`) because the OAuth2 spec, and therefore Swagger, sends a form, not JSON.

### 5.4 `get_current_user`
Decode → user id → load user → return it. Any failure → **401 with `WWW-Authenticate: Bearer`** (the HTTP standard says a 401 should tell the client how to authenticate).

Routes are protected with `dependencies=[Depends(get_current_user)]`, which avoids unused parameters in functions that only need *a* user, not *which* user.

### 5.5 Two subtle security details
- **Same error for unknown e-mail and wrong password**, so an attacker cannot discover which e-mails have accounts (user enumeration).
- **Constant-time-ish login:** when the e-mail doesn't exist we still run `bcrypt.checkpw` against `DUMMY_PASSWORD_HASH`. Otherwise "unknown e-mail" would answer in 1 ms and "wrong password" in 200 ms, which leaks the same information through timing.

### 5.6 401 vs 403
**401 Unauthorized** means "I don't know who you are" (missing, invalid or expired token). **403 Forbidden** means "I know who you are, but you may not do this". We have no roles, so we only need 401.

---

## Part 6: `main.py` (routes)

- `lifespan`: code before `yield` runs at startup (create tables, warn if the JWT secret is the default); code after `yield` runs at shutdown.
- `Annotated` aliases (`DbSession`, `EventId`, `Skip`, `Limit`) keep signatures short and consistent. `Path(gt=0)` means `/events/0` returns 422 before touching the DB.
- `response_model=EventResponse` means FastAPI **filters and serializes** the returned ORM object through the schema. This is why returning a `User` never leaks `hashed_password`.
- `status_code=201` for creation, `204` for deletion (an empty body is required with 204).
- `responses={...}` only *documents* extra status codes in Swagger; it does not change behaviour.
- `alias="date"`: the query parameter is called `date` in the URL, but the Python variable is `event_date` (we do not want to shadow the `datetime.date` type).
- The demo frontend is mounted with `StaticFiles` at `/app`, the same origin as the API, so no CORS configuration is needed.

### Status-code map
| Code | When |
|---|---|
| 200 | GET, PUT, login |
| 201 | POST /events, POST registrations, POST /auth/register |
| 204 | DELETE event / registration |
| 401 | missing, invalid or expired token; wrong credentials |
| 404 | event or registration not found (domain exception) |
| 409 | duplicate registration, event full, capacity below registrations, e-mail taken |
| 422 | Pydantic validation of body, path or query |

---

## Part 7: Async, what it actually buys you

- **Event loop:** one thread runs many coroutines. When a coroutine hits `await` on I/O (a DB query), it **yields** control and the loop runs another request meanwhile.
- **Blocking call = disaster:** a synchronous DB driver inside `async def` freezes the whole loop, so *every* request waits. That is why we need the async driver (`asyncpg`) and `AsyncSession`.
- **Async ≠ parallel CPU.** Async helps with I/O-bound work (waiting on the DB or network). bcrypt is CPU-bound: about 0.2 s of pure computation. Called directly inside `async def`, it would freeze the loop for every other request during each login. That is why `services.create_user` and `services.authenticate_user` call it with `await asyncio.to_thread(...)`, which runs it in a worker thread while the loop keeps serving requests.
- **Where async is used here:** engine (`create_async_engine`), sessions (`AsyncSession`), every service (`await db.scalar/scalars/get/commit/refresh/delete`), every route (`async def`), and the tests (`httpx.AsyncClient`, `pytest-asyncio`).

---

## Part 8: The tests

- **Real PostgreSQL, not SQLite:** `FOR UPDATE`, `ILIKE` and time zones behave differently (or don't exist) in SQLite. Testing on a different DB engine can give false confidence.
- **Isolation:** the `db_engine` fixture drops and creates all tables for **each test**, so tests never depend on each other's data or order.
- **`app.dependency_overrides[get_db]`:** replaces the real session with a test-DB session for the duration of a test. This is FastAPI's dependency injection used for testing.
- **`NullPool`:** pytest-asyncio creates a new event loop per test, and asyncpg connections are bound to the loop that created them. Pooling them across tests causes "attached to a different loop" errors.
- **`ASGITransport`:** httpx calls the ASGI app directly in-process, with no real server or network. It is fast and deterministic.
- **Factory fixtures** (`create_event`, `register`) remove repetition.
- **Parametrized tests** run one test body against many inputs (for example 11 invalid event payloads).
- **The concurrency test** is a *meaningful* test: it fails if the protection is removed (a form of mutation testing).

---

## Part 9: Questions you may be asked, with short answers

1. **Why `services.py` and not logic in the routes?** It separates concerns: logic is reusable and testable without HTTP, and routes stay readable.
2. **What happens if two people register for the last seat at the same time?** The first locks the event row with `SELECT … FOR UPDATE`; the second waits, then sees the event as full and gets 409. Proven by the concurrency test.
3. **Why also a UNIQUE constraint if you check duplicates in code?** Defence in depth: the DB guarantees integrity even under races or if someone bypasses the API. The code check gives a nice message; the constraint gives a guarantee.
4. **Why 409 and not 400 for a duplicate?** The request is well-formed (so not 400 or 422) but conflicts with the current state of the resource. That is exactly what 409 Conflict means.
5. **Where does 422 come from?** FastAPI and Pydantic, before our function runs.
6. **What is in the JWT and is it encrypted?** `sub` (user id), `iat` and `exp`. It is signed, not encrypted: anyone can read it, but nobody can modify it without the secret.
7. **How do you log out with JWT?** The client deletes the token. Server-side revocation needs a blocklist or short-lived tokens plus refresh tokens. That is a known limitation.
8. **Why `expire_on_commit=False`?** To avoid implicit lazy reloads after commit, which fail with async sessions.
9. **Why is `skip/limit` paginated with a stable ORDER BY?** Without it, the row order isn't guaranteed and pages could overlap or skip rows.
10. **What is N+1 and how did you avoid it?** One query for the list plus one per item. We avoided it by computing the registration count in the same query with a correlated subquery.
11. **Why half-open date ranges?** They are correct at boundaries, can use the index, and respect the configured time zone.
12. **What would you add for production?** Alembic migrations, roles/ownership, tying registrations to logged-in users, refresh tokens, rate limiting on login, structured logging, Docker, CI running the tests.

---

## Part 10: One-page revision sheet

- **Layers:** route (HTTP) → service (rules) → model (DB). Exceptions flow up and become status codes.
- **Validation:** Pydantic → 422. Business rules → 404/409. Auth → 401.
- **Three registration rules:** event exists (404) → not duplicate (409) → seat free (409), under a `FOR UPDATE` row lock.
- **Integrity:** CHECK capacity > 0, UNIQUE(event, email), FK ON DELETE CASCADE.
- **Partial update:** `model_dump(exclude_unset=True)`; can't drop capacity below the registration count.
- **Pagination:** `OFFSET/LIMIT` + stable order + `X-Total-Count`. **Filters:** `ILIKE` with escaping; a half-open day range in `APP_TIMEZONE`.
- **Auth:** bcrypt (salted, slow), JWT HS256 (`sub`, `iat`, `exp`), algorithm pinned, same error for bad e-mail or password, dummy hash for timing.
- **Async:** asyncpg + AsyncSession; `await` frees the loop during I/O; never block the loop.
- **Tests:** real PostgreSQL, fresh tables per test, dependency override, NullPool, concurrency test proves the lock.

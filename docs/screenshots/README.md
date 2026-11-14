# Swagger UI Screenshots

Take these on **your own machine** while testing in Swagger (`http://127.0.0.1:8000/docs`), save them in this folder with these names, and commit them. Each screenshot should show the request *and* the response code and body.

| # | File name | What to show |
|---|---|---|
| 01 | `01-swagger-overview.png` | The whole /docs page with the Auth, Events and Registrations tags |
| 02 | `02-register-user-201.png` | `POST /auth/register` → 201 |
| 03 | `03-register-duplicate-409.png` | Same e-mail again → 409 |
| 04 | `04-login-token-200.png` | Authorize dialog or `POST /auth/login` → token |
| 05 | `05-create-event-no-token-401.png` | `POST /events` without authorizing → 401 |
| 06 | `06-create-event-201.png` | `POST /events` → 201 |
| 07 | `07-create-event-invalid-422.png` | Empty title / past date / capacity 0 → 422 |
| 08 | `08-list-events-200.png` | `GET /events` → 200 |
| 09 | `09-pagination-total-header.png` | `?skip=0&limit=1` with the `x-total-count` response header visible |
| 10 | `10-filter-title-date.png` | `?title=...&date=...` → filtered result |
| 11 | `11-get-event-200.png` | `GET /events/{id}` → 200 |
| 12 | `12-get-event-404.png` | `GET /events/9999` → 404 |
| 13 | `13-update-event-200.png` | `PUT /events/{id}` partial update → 200 |
| 14 | `14-register-201.png` | `POST /events/{id}/registrations` → 201 |
| 15 | `15-register-duplicate-409.png` | Same e-mail → 409 |
| 16 | `16-register-full-409.png` | Event full → 409 |
| 17 | `17-register-missing-event-404.png` | `/events/9999/registrations` → 404 |
| 18 | `18-capacity-below-registrations-409.png` | `PUT` with a lower `max_capacity` → 409 |
| 19 | `19-list-registrations-200.png` | `GET /events/{id}/registrations` → 200 |
| 20 | `20-cancel-registration-204.png` | `DELETE /registrations/{id}` → 204 |
| 21 | `21-delete-event-204.png` | `DELETE /events/{id}` → 204 |
| 22 | `22-pytest-75-passed.png` | Terminal output of `pytest -v` ending in `75 passed` |

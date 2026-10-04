# Gatherline | Event-Driven Production Monitoring

Gatherline is a local Flask + SQLite college project for event organisers and customers. Organisers manage events and inspect capacity/registration metrics; customers discover events, register, and cancel. Event actions are validated, committed with an audit log and notifications, then sent as best-effort JSON messages to an optional TCP socket server.

## Project at a glance

| Area | Details |
|---|---|
| Purpose | Replace spreadsheet-based event tracking with role-based event management and live dashboard summaries. |
| Organiser features | Create, edit, and delete events; inspect registrations; view capacity and registration analytics; download CSV reports. |
| Customer features | Browse, search, and filter events; register and cancel; view personal activity and notifications. |
| Event pipeline | Validate action -> save to SQLite -> write audit log and notifications -> attempt optional TCP socket message. |
| Monitoring | Dashboard cards poll for updated totals; multiprocessing workers calculate per-event metrics; events at 75% capacity are flagged. |
| Stack | Python 3.10+, Flask, SQLite, HTML/CSS/JavaScript, `socket`, `multiprocessing`, SymPy, and Python `csv`. |
| Data location | `instance/events.sqlite3`, created and seeded automatically on first run. |

## Local demo accounts

Use these accounts after starting the app at `http://127.0.0.1:5000`:

| Role | Email | Password |
|---|---|---|
| Organiser | `organiser@example.com` | `organiser123` |
| Organiser | `noah@example.com` | `organiser123` |
| Customer | `customer@example.com` | `customer123` |
| Customer | `leo@example.com` | `customer123` |

These are sample-only credentials seeded into a new local database. The passwords listed here are the values to type at sign-in; SQLite stores password hashes, not these plaintext values. Do not reuse these passwords or expose the demo database publicly.

## Quick start

```bash
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`, then sign in with one of the demo accounts above. For a fresh environment and test instructions, see [VS Code setup](#5-vs-code-setup).

## 1. Architecture

```text
Browser forms / polling
	|
Flask routes -- session authentication + role checks
	|
EventManager: validate -> SQLite transaction (data + log + notifications)
	|                                      |
best-effort TCP JSON client              SQLite queries
	|                                      |
optional socket_server.py          analytics / CSV / HTML dashboards
					       |
				    ProcessPool worker metrics
```

The web process is independent of the socket server. Socket errors are intentionally ignored after a short timeout; database persistence remains authoritative. Dashboard cards poll a JSON endpoint every 12 seconds. Per-event metrics use `multiprocessing` spawn workers when multiple events exist. SymPy derives the integer seat threshold for the 75% capacity alert.

## 2. Folder structure

```text
Project-1/
├── app.py
├── auth.py
├── database.py
├── event_manager.py
├── analytics.py
├── multiprocessing_worker.py
├── report_generator.py
├── session_manager.py
├── socket_client.py
├── socket_server.py
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── register.html
│   ├── organiser_dashboard.html
│   ├── customer_dashboard.html
│   ├── browse_events.html
│   ├── event_form.html
│   ├── analytics.html
│   ├── registrations.html
│   ├── notifications.html
│   └── error.html
├── static/
│   ├── css/style.css
│   └── js/dashboard.js
└── tests/test_app.py
```

## 3. Complete application files

The executable Python modules and full HTML, CSS, and JavaScript are included at the paths above. No pseudocode or generated source files are required.

## 4. Database initialization and sample data

`database.py` creates the SQLite schema and indexes on application startup. The default file is `instance/events.sqlite3`; it is created automatically and excluded from version control. On a new database it seeds two organisers, two customers, and two upcoming events.

Passwords are Werkzeug scrypt hashes. The fallback Flask secret and sample accounts are for local review only. Set a private `SECRET_KEY` before exposing the app beyond localhost.

## 5. VS Code setup

1. Open this folder in VS Code and select a Python 3.10+ interpreter.
2. Create and activate a virtual environment: `python -m venv .venv` followed by `source .venv/bin/activate` on Linux/macOS, or `.venv\\Scripts\\activate` on Windows.
3. Install packages: `python -m pip install -r requirements.txt`.
4. Start the app: `python app.py`.
5. Open `http://127.0.0.1:5000` and use a demo account or create an account.
6. Optional socket demonstration: in a second terminal run `python socket_server.py` before creating/registering/updating events. It prints each received JSON event; stop it with Ctrl+C.
7. Run acceptance tests: `python -m unittest discover -s tests -v`.

Set `SECRET_KEY` to a random value in the environment for local hardening. Set `COOKIE_SECURE=true` only when serving over HTTPS. `FLASK_DEBUG=true` enables Flask debug mode for development; leave it unset for review.

## 6. Role-based dashboard

Organisers land on `/organiser`, where they can create, edit, delete, and inspect attendees for their own events; see recent pipeline activity; open performance metrics; and download CSV. Customers land on `/customer`, browse and filter `/customer/events`, register, cancel their own registrations, and see notifications. Customers are explicitly rejected with HTTP 403 from organiser routes. Each POST is protected by a session CSRF token.

## 7. Architecture-to-file mapping

| Responsibility | Files |
|---|---|
| Flask routes and HTML response flow | `app.py`, `templates/` |
| SQLite schema, connections, seed data | `database.py` |
| Password authentication and RBAC | `auth.py`, `session_manager.py` |
| Event validation, transaction, audit, notification | `event_manager.py` |
| Optional event message transport | `socket_client.py`, `socket_server.py` |
| Parallel analytics and 75% alert | `analytics.py`, `multiprocessing_worker.py` |
| CSV output | `report_generator.py` |
| Live card polling and confirmation interactions | `static/js/dashboard.js` |
| Responsive interface | `static/css/style.css` |

## 8. Programming concepts

| Concept | Where used | File |
|---|---|---|
| Classes, constructors, encapsulation | `EventManager` owns validation and pipeline actions | `event_manager.py` |
| Functions and custom exceptions | Event validation raises `EventValidationError` | `event_manager.py` |
| Modules and imports | Domain responsibilities are split by module | Root Python modules |
| Parameterized SQL and CRUD | User/event/registration/log/notification operations | `database.py`, `auth.py`, `event_manager.py`, `app.py` |
| Password hashing | Werkzeug scrypt hash/verification | `auth.py`, `database.py` |
| Sessions and RBAC | Session identity and role decorators | `session_manager.py`, `auth.py` |
| Socket programming | newline-delimited JSON over TCP | `socket_client.py`, `socket_server.py` |
| Multiprocessing | spawned process pool for event metrics | `analytics.py`, `multiprocessing_worker.py` |
| SymPy | exact ceiling threshold for 75% capacity | `multiprocessing_worker.py` |
| File-style report generation | CSV written to an in-memory file and downloaded | `report_generator.py`, `app.py` |
| Browser fetch/polling | 12-second dashboard statistics refresh | `static/js/dashboard.js`, `app.py` |

The app does not need `map`, `filter`, or `reduce` for its core work; these are intentionally omitted rather than added as decorative examples.

## 9. Database schema

| Table | Key fields and relationships |
|---|---|
| `users` | `id`, name, unique email, password hash, role, creation time |
| `events` | `id`, organiser FK, name, description, date, time, location, capacity, category, status |
| `registrations` | `id`, event FK, user FK; unique `(event_id, user_id)` pair |
| `event_logs` | `id`, nullable event/user FKs, event type, detail, timestamp; deletion preserves audit text |
| `notifications` | `id`, user FK, message, read flag, timestamp |

Foreign keys are enabled on every connection. SQL values are parameterized. The event/user and registration/event lookup columns are indexed.

## 10. Data flow

1. A browser submits a CSRF-protected form to a Flask route.
2. Authentication/RBAC checks the session role; `EventManager` validates fields and ownership.
3. A SQLite transaction writes the event or registration, an audit log entry, and recipient notifications together.
4. After commit, the socket client attempts to send a JSON message; an unavailable server does not fail the request.
5. Dashboard HTML reads persisted data. JavaScript polls `/api/dashboard-stats`; organiser analytics send plain event dictionaries to worker processes.
6. Organisers can download a CSV containing event, registration, capacity, and utilization fields.

## 11. Test cases

Run with `python -m unittest discover -s tests -v`. Actual output below reflects the automated test suite.

| ID | Input | Expected output | Actual output | Status |
|---|---|---|---|---|
| TC01 | Valid organiser login | Redirect to organiser dashboard | Organiser overview rendered | Pass |
| TC02 | Valid customer login | Redirect to customer dashboard | Customer activity rendered | Pass |
| TC03 | Wrong password | Reject login without session | Error shown; no dashboard | Pass |
| TC04 | Valid organiser event form | Save event, log, notification | All three persisted | Pass |
| TC05 | Customer registers | Create unique registration | Registration persisted | Pass |
| TC06 | Customer cancels registration | Remove registration and log action | Registration removed; cancellation logged | Pass |
| TC07 | Organiser updates owned event | Persist new event fields | Updated name retrieved from SQLite | Pass |
| TC08 | Organiser deletes owned event | Delete event and retain audit detail | Event deleted; delete log retained | Pass |
| TC09 | Customer requests organiser URLs | Return HTTP 403 | Dashboard and analytics return 403 | Pass |
| TC10 | Analytics, CSV, stats endpoint | Return metrics, downloadable CSV, JSON | All endpoints return expected content | Pass |

Additional check: stored sample passwords are hashes, not plaintext. The socket server is optional and has a short connection timeout; socket delivery itself is demonstrated manually in the second-terminal step above.

## 12. Screenshot instructions for review

1. Start the app and sign in as the organiser. Capture the overview showing KPI cards, upcoming events, and recent activity.
2. Open **Performance** and capture event utilization, capacity alert state, and the CSV download control.
3. Open **New event** and capture the validated event form.
4. Sign out, log in as the customer, and capture **Discover events** with category/search controls and a registration action.
5. Register for an event, then capture **My activity** and the customer notification list.
6. Optional: start `socket_server.py`, perform an event action, and capture its received JSON message in the VS Code terminal.

Do not include passwords or the local SQLite file in screenshots. External Google Fonts and Lucide icons are progressive enhancements; the app remains functional if those CDNs are unavailable.
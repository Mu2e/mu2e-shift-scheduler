# Token API (`/api/v1`)

A small JSON API for machine clients such as the Mu2e Bulletin Board. It is
authenticated with bearer tokens minted by an administrator, so a consumer
never needs a Fermilab SSO session.

## Authentication

1. Sign in as an administrator and open **Admin > API Tokens**
   (`/admin/api-tokens`).
2. Enter a label that identifies the consumer (for example
   `mu2e-bulletin-board (prod)`) and press **Create token**.
3. Copy the token from the green banner. It is shown exactly once; the
   scheduler stores only its SHA-256 hash.
4. Send it on every request:

```
Authorization: Bearer mu2e_...
```

`X-API-Token: mu2e_...` is accepted as an alternative header. A missing,
unknown, or revoked token returns `401` with a JSON `error` field rather than
a redirect to the login page. Revoke a token from the same page; revocation is
immediate and needs no redeploy.

## Endpoints

| Method | Path | Token | Purpose |
|---|---|---|---|
| GET | `/api/v1/health` | no | Liveness check: `{"ok": true}` |
| GET | `/api/v1/classifications` | yes | The taxonomy: id, name, slug, sort order |
| GET | `/api/v1/schedules` | yes | Stored schedules with their classification |
| GET | `/api/v1/on-duty` | yes | Who is on duty at a given instant |

### `GET /api/v1/on-duty`

Query parameters:

| Name | Default | Meaning |
|---|---|---|
| `at` | now | ISO-8601 datetime. A naive value is local time (`America/Chicago`); an aware value is converted. |
| `classification` | all | Comma-separated classification slugs to keep, e.g. `run-coordinators,oncall-daq-experts`. |

A shift covers `at` when `starts_at <= at < ends_at`. A shift whose end time
is at or before its start time wraps past midnight, so a `20:00`–`04:00` night
shift dated `D` covers `D 20:00` through `D+1 04:00`. Multi-day blocks honour
their weekday filter. Unassigned shifts are omitted. Results are ordered by
classification sort order, then schedule name, then start.

Example response:

```json
{
  "at": "2026-11-06T13:05-06:00",
  "timezone": "America/Chicago",
  "on_duty": [
    {
      "classification": "Run Coordinators",
      "classification_slug": "run-coordinators",
      "schedule": "Fall 2026 RC",
      "schedule_id": 3,
      "shift_id": "rc-W02-B1-S1-day",
      "shift_name": "Day",
      "date": "2026-11-06",
      "date_end": "",
      "start_time": "08:00",
      "end_time": "16:00",
      "starts_at": "2026-11-06T08:00",
      "ends_at": "2026-11-06T16:00",
      "person": {
        "name": "Alice Example",
        "email": "alice@fnal.gov",
        "phone": "+1 630 555 1111",
        "institution": "Fermilab"
      }
    }
  ]
}
```

Contact fields come from the contacts table first, then from the person's
login account. Blank strings mean unknown.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `API_TIMEZONE` | `America/Chicago` | Zone used to interpret naive `at` values and to stamp the response |

## Diagnostics

`scripts/shifts_api_probe.py` (see `man/shifts-api-probe.1`) calls the health
check and then any endpoint with a token:

```bash
scripts/shifts_api_probe.py --base-url https://mu2e-shifts.fnal.gov --token mu2e_... 
scripts/shifts_api_probe.py --at 2026-11-06T22:00 --classification run-coordinators --json
```

Exit status 3 means the token was rejected.

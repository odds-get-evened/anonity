# GET `/api/logs`

Returns a slice of the in-memory log buffer starting from a caller-supplied index. Designed for efficient long-polling from a UI without re-downloading previously seen entries.

---

## Request

| Property | Value |
|---|---|
| **Method** | `GET` |
| **URL** | `/api/logs` |
| **Auth required** | No |
| **Request body** | None |

### Query Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `since` | `integer` | `0` | Index of the first log entry to return. Pass the `next` value from the previous response to fetch only new entries. |

### Headers

| Header | Value |
|---|---|
| `Accept` | `application/json` (optional) |

---

## Success Response — `200 OK`

```json
{
  "entries": [
    { "ts": "14:03:22", "msg": "Chain integrity OK (10 blocks)" },
    { "ts": "14:05:01", "msg": "Peer connected: 127.0.0.1:6001" },
    { "ts": "14:06:45", "msg": "Registration PoW solved in 2.3s" }
  ],
  "next": 3
}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `entries` | `array` | Log entries from index `since` up to the current end of the buffer |
| `entries[].ts` | `string` | Wall-clock timestamp of the event (`HH:MM:SS`, local time) |
| `entries[].msg` | `string` | Human-readable log message |
| `next` | `integer` | Pass this value as `?since=` on the next request to receive only new entries |

When there are no new entries since the requested index, `entries` will be an empty array and `next` will equal the value passed as `since`.

---

## Error Responses

This endpoint does not return application-level errors. Malformed `since` values (non-integer) are silently treated as `0`.

---

## Examples

### Initial fetch — get all logs

```bash
curl "http://localhost:5001/api/logs?since=0"
```

```json
{
  "entries": [
    { "ts": "13:00:01", "msg": "Node started on port 6000" },
    { "ts": "13:00:02", "msg": "Chain loaded: 5 blocks" }
  ],
  "next": 2
}
```

### Incremental fetch — get only new entries

```bash
curl "http://localhost:5001/api/logs?since=2"
```

```json
{
  "entries": [
    { "ts": "13:05:44", "msg": "Mining complete: block #6 added" }
  ],
  "next": 3
}
```

### No new entries

```bash
curl "http://localhost:5001/api/logs?since=3"
```

```json
{
  "entries": [],
  "next": 3
}
```

---

## Notes

- The log buffer is **in-memory only** and is cleared on node restart.
- Indices are zero-based and monotonically increasing within a single session.
- A recommended polling interval is **1–2 seconds** for live UIs.
- The Electron GUI (`gui/renderer/app.js`) uses this endpoint for its live log panel.

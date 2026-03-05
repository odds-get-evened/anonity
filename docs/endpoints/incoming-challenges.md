# GET `/api/incoming-challenges`

Returns an event stream of reputation challenges **directed at this node's identity**: received, solved, or expired. Designed for incremental polling — similar to [`GET /api/logs`](./logs.md).

---

## Request

| Property | Value |
|---|---|
| **Method** | `GET` |
| **URL** | `/api/incoming-challenges` |
| **Auth required** | No |
| **Request body** | None |

### Query Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `since` | `integer` | `0` | Index of the first event to return. Pass the `next` value from the previous response to fetch only new events. |

### Headers

| Header | Value |
|---|---|
| `Accept` | `application/json` (optional) |

---

## Success Response — `200 OK`

```json
{
  "events": [
    {
      "kind": "received",
      "issuer_pubkey": "04deadbeef...",
      "target_pubkey": "04a1b2c3...",
      "issued_at": 1710001234.567,
      "expires_at": 1710001534.567,
      "status": "solving"
    },
    {
      "kind": "solved",
      "issuer_pubkey": "04deadbeef...",
      "target_pubkey": "04a1b2c3...",
      "issued_at": 1710001234.567,
      "expires_at": null,
      "status": "solved"
    }
  ],
  "next": 2
}
```

Returns `{"events": [], "next": <same_as_since>}` when no new events exist.

### Field Reference

| Field | Type | Description |
|---|---|---|
| `events` | `array` | Challenge events from index `since` to current end of buffer |
| `events[].kind` | `string` | Event type: `"received"`, `"solved"`, or `"expired"` |
| `events[].issuer_pubkey` | `string` (hex) | Public key of the peer who issued the challenge |
| `events[].target_pubkey` | `string` (hex) | Public key of the challenged identity (this node) |
| `events[].issued_at` | `number` | Unix timestamp when the challenge was issued |
| `events[].expires_at` | `number \| null` | Unix timestamp of the challenge deadline; `null` after resolution |
| `events[].status` | `string` | Current status: `"solving"`, `"solved"`, or `"expired"` |
| `next` | `integer` | Pass this value as `?since=` on the next request |

### Event Kind Reference

| `kind` | `status` | Meaning |
|---|---|---|
| `received` | `solving` | Challenge arrived; background PoW solver has started |
| `solved` | `solved` | PoW solved in time; `REPUTATION_MINE` tx submitted |
| `expired` | `expired` | Time window closed before solving; `REPUTATION_IGNORE` penalty applied |

---

## Error Responses

This endpoint does not return application-level errors. Malformed `since` values are silently treated as `0`.

---

## Examples

### Initial fetch

```bash
curl "http://localhost:5001/api/incoming-challenges?since=0"
```

```json
{
  "events": [
    {
      "kind": "received",
      "issuer_pubkey": "04dead...",
      "target_pubkey": "04a1b2...",
      "issued_at": 1710001234.567,
      "expires_at": 1710001534.567,
      "status": "solving"
    }
  ],
  "next": 1
}
```

### Poll for new events

```bash
curl "http://localhost:5001/api/incoming-challenges?since=1"
```

```json
{
  "events": [
    {
      "kind": "solved",
      "issuer_pubkey": "04dead...",
      "target_pubkey": "04a1b2...",
      "issued_at": 1710001234.567,
      "expires_at": null,
      "status": "solved"
    }
  ],
  "next": 2
}
```

### No new events

```bash
curl "http://localhost:5001/api/incoming-challenges?since=2"
```

```json
{
  "events": [],
  "next": 2
}
```

---

## Notes

- This endpoint tracks only challenges for **this node's own identity**. To see challenges you have issued to others, see [`GET /api/issued-challenges`](./issued-challenges.md).
- The event buffer is in-memory and cleared on node restart.
- A recommended polling interval is **1–2 seconds** when a challenge is known to be active.
- Solving a challenge increases your balance by `+10.0`. Expiring a challenge reduces your balance by `-15.0`.

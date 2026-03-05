# GET `/api/issued-challenges`

Returns the list of reputation challenges that **this node has issued** and that are still pending (not yet resolved or expired).

---

## Request

| Property | Value |
|---|---|
| **Method** | `GET` |
| **URL** | `/api/issued-challenges` |
| **Auth required** | No |
| **Request body** | None |

### Headers

| Header | Value |
|---|---|
| `Accept` | `application/json` (optional) |

---

## Success Response — `200 OK`

```json
[
  {
    "target_pubkey": "04a1b2c3d4e5f6aa...",
    "issued_at": 1710001234.567,
    "expires_at": 1710001534.567,
    "resolved": false
  },
  {
    "target_pubkey": "04deadbeef0102...",
    "issued_at": 1710000800.0,
    "expires_at": 1710001100.0,
    "resolved": true
  }
]
```

Returns an empty array (`[]`) when no challenges have been issued this session.

### Field Reference

| Field | Type | Description |
|---|---|---|
| `target_pubkey` | `string` (hex) | The public key of the challenged identity |
| `issued_at` | `number` | Unix timestamp when the challenge was issued |
| `expires_at` | `number` | Unix timestamp when the 300-second window closes |
| `resolved` | `boolean` | `true` if the target solved the challenge before it expired |

---

## Error Responses

This endpoint does not return application-level errors.

---

## Example

```bash
curl http://localhost:5001/api/issued-challenges
```

```json
[
  {
    "target_pubkey": "04a1b2...",
    "issued_at": 1710001234.567,
    "expires_at": 1710001534.567,
    "resolved": false
  }
]
```

---

## Notes

- Only challenges **issued by this node** in the current session appear here. Challenges issued by other peers are not tracked.
- Once a challenge's `expires_at` passes and it is unresolved, the background expiry loop submits a `REPUTATION_IGNORE` transaction and removes it from this list.
- To issue a new challenge manually, see [`POST /api/challenge`](./challenge.md).
- To see challenges directed at your own identity, see [`GET /api/incoming-challenges`](./incoming-challenges.md).

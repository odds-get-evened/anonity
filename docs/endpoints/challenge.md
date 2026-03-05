# POST `/api/challenge`

Manually issues a **reputation challenge** to a specific identity. The target must solve a 14-bit Proof-of-Work within 300 seconds or suffer a reputation penalty (`-15.0` balance). Challenges are also issued automatically by the background challenge manager every ~2 minutes.

---

## Request

| Property | Value |
|---|---|
| **Method** | `POST` |
| **URL** | `/api/challenge` |
| **Auth required** | No |
| **Content-Type** | `application/json` |

### Headers

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |

### Request Body

```json
{
  "target_pubkey": "04a1b2c3d4e5f6aa..."
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `target_pubkey` | `string` (hex) | Yes | SECP256R1 public key of the identity to challenge |

---

## Success Response — `200 OK`

```json
{
  "ok": true
}
```

A `REP_CHALLENGE` message has been broadcast to the P2P network. The challenge is now tracked locally and will appear in [`GET /api/issued-challenges`](./issued-challenges.md).

---

## Error Responses

### `400 Bad Request` — missing field

```json
{
  "error": "target_pubkey required"
}
```

### `404 Not Found` — identity does not exist

```json
{
  "error": "identity not found"
}
```

The provided public key is not registered on the chain.

### `409 Conflict` — challenge already pending

```json
{
  "error": "challenge already pending for this identity"
}
```

A challenge for this identity is still open. Wait for it to resolve (solved or expired) before issuing another.

### `503 Service Unavailable` — node not initialised

```json
{
  "error": "not ready"
}
```

---

## Challenge Lifecycle

```
POST /api/challenge {"target_pubkey": "04..."}
  → REP_CHALLENGE broadcast (P2P)

[Target receives challenge, solves 14-bit PoW ~16K iterations]
  → REP_SOLUTION broadcast + REPUTATION_MINE tx submitted to mempool
  → Issuer marks challenge resolved

OR (if 300s timeout exceeded):
  → Issuer submits REPUTATION_IGNORE tx
  → Target balance -= 15.0
```

### PoW Parameters (Reputation Challenge)

| Parameter | Value |
|---|---|
| Algorithm | SHA-256 |
| Difficulty | 14 leading zero bits |
| Expected cost | ~16 K hash iterations |
| Challenge data | `SHA-256(target_pubkey_bytes ‖ random_16_bytes ‖ timestamp)` |
| Time window | 300 seconds |

---

## Example

```bash
curl -X POST http://localhost:5001/api/challenge \
  -H "Content-Type: application/json" \
  -d '{"target_pubkey": "04a1b2c3..."}'
```

```json
{ "ok": true }
```

---

## Notes

- You cannot challenge your own node's identity.
- Only **authenticated** (non-revoked) identities can be challenged.
- The background challenge manager also issues challenges automatically. Manual challenges are useful for testing or accelerating reputation changes in a development environment.
- Monitor open challenges you have issued via [`GET /api/issued-challenges`](./issued-challenges.md).
- Monitor challenges directed at your identity via [`GET /api/incoming-challenges`](./incoming-challenges.md).

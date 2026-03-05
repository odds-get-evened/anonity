# POST `/api/register`

Initiates identity registration for this node. Registration requires solving a **20-bit Proof-of-Work** (approximately 1–5 seconds on modern hardware). The PoW is solved in a background thread; this endpoint returns immediately.

---

## Request

| Property | Value |
|---|---|
| **Method** | `POST` |
| **URL** | `/api/register` |
| **Auth required** | No |
| **Content-Type** | `application/json` (body is optional) |

### Headers

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |

### Request Body

No body is required. An empty POST is valid.

---

## Success Responses

### `200 OK` — registration started

```json
{
  "started": true
}
```

The PoW solver has been launched in the background. The `IDENTITY_REGISTER` transaction will be submitted to the mempool once solved, and will appear on-chain after the next mine operation.

### `200 OK` — already registered

```json
{
  "already_registered": true,
  "balance": 112.5
}
```

Returned when the node's public key is already present in the on-chain identity registry. No new registration attempt is made.

---

## Error Responses

### `503 Service Unavailable` — node not initialised

```json
{
  "error": "not ready"
}
```

---

## Registration Lifecycle

```
POST /api/register
  → {"started": true}         ← returns immediately

[Background: PoW solving ~1–5 seconds]
  → IDENTITY_REGISTER tx added to mempool

POST /api/mine                ← mine the pending tx
  → {"started": true}

GET /api/my-identity          ← poll until registered: true
  → {"registered": true, "balance": 100.0, ...}
```

### PoW Parameters

| Parameter | Value |
|---|---|
| Algorithm | SHA-256 |
| Difficulty | 20 leading zero bits |
| Expected cost | ~1 M hash iterations / 1–5 seconds |
| Challenge data | `SHA-256(pubkey_bytes ‖ random_seed)` |

---

## Example

```bash
# 1. Trigger registration
curl -X POST http://localhost:5001/api/register

# 2. Watch logs to observe PoW progress
curl "http://localhost:5001/api/logs?since=0"

# 3. Mine the pending transaction
curl -X POST http://localhost:5001/api/mine

# 4. Confirm registration
curl http://localhost:5001/api/my-identity
```

---

## Notes

- A node can only register **once per public key**. Regenerating the key (deleting `~/.databox/identity/my_key.pkl`) creates a new identity.
- The starting balance after registration is `100.0`.
- If peers are connected before registering, they will replicate the `IDENTITY_REGISTER` transaction and mine it collaboratively.
- Monitoring registration progress is done via [`GET /api/logs`](./logs.md) and [`GET /api/my-identity`](./my-identity.md).

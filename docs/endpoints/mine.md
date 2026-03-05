# POST `/api/mine`

Starts a mining operation that bundles all pending mempool transactions into a new block and appends it to the local chain. Mining runs in a background thread; this endpoint returns immediately.

---

## Request

| Property | Value |
|---|---|
| **Method** | `POST` |
| **URL** | `/api/mine` |
| **Auth required** | No |
| **Content-Type** | `application/json` (body is optional) |

### Headers

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |

### Request Body

No body required.

---

## Success Response — `200 OK`

```json
{
  "started": true
}
```

Mining has started in the background. Monitor progress with [`GET /api/logs`](./logs.md) and confirm the new block count via [`GET /api/status`](./status.md).

---

## Error Responses

### `400 Bad Request` — empty mempool

```json
{
  "error": "mempool is empty"
}
```

There are no pending transactions to mine. Submit a transaction first (e.g., via [`POST /api/register`](./register.md) or the challenge system) and then call this endpoint again.

### `503 Service Unavailable` — node not initialised

```json
{
  "error": "not ready"
}
```

---

## Full Registration + Mining Flow

```bash
# 1. Register (submits IDENTITY_REGISTER tx to mempool after PoW)
curl -X POST http://localhost:5001/api/register

# 2. Watch logs until PoW is solved and tx appears in mempool
curl "http://localhost:5001/api/logs?since=0"

# 3. Confirm tx is pending
curl http://localhost:5001/api/mempool

# 4. Mine the block
curl -X POST http://localhost:5001/api/mine

# 5. Confirm registration
curl http://localhost:5001/api/my-identity
```

---

## Notes

- Mining difficulty is inherited from the base `request-chain` blockchain engine.
- Multiple transactions in the mempool are included in a single block per mine call.
- After a successful mine, the local chain height increments by 1. Connected peers receive the new block automatically via P2P gossip.
- Use [`GET /api/mempool`](./mempool.md) to inspect what transactions will be included before mining.

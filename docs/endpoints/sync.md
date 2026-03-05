# POST `/api/sync`

Triggers a chain synchronisation request to all connected peers. The node adopts the longest valid chain it receives (**longest-chain-wins** consensus rule). The sync operation runs in a background thread; this endpoint returns immediately.

---

## Request

| Property | Value |
|---|---|
| **Method** | `POST` |
| **URL** | `/api/sync` |
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
  "ok": true
}
```

The sync request has been dispatched to all connected peers. Monitor progress via [`GET /api/logs`](./logs.md) or [`GET /api/status`](./status.md).

---

## Error Responses

### `400 Bad Request` — no peers connected

```json
{
  "error": "no peers connected"
}
```

You must connect to at least one peer first via [`POST /api/connect`](./connect.md).

### `503 Service Unavailable` — node not initialised

```json
{
  "error": "not ready"
}
```

---

## Example

```bash
# 1. Connect to a peer
curl -X POST http://localhost:5001/api/connect \
  -H "Content-Type: application/json" \
  -d '{"host": "127.0.0.1", "port": 6001}'

# 2. Sync the chain
curl -X POST http://localhost:5001/api/sync

# 3. Check the updated block count
curl http://localhost:5001/api/status | jq .blocks
```

---

## Notes

- Auto-sync runs every **60 seconds** in the background; manual sync is useful immediately after connecting to a new peer.
- If a peer's chain is shorter than the local chain, no change occurs.
- After sync, new identities or balance updates from the peer's chain will be reflected in [`GET /api/identities`](./identities.md).

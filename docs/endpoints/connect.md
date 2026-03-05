# POST `/api/connect`

Establishes a P2P connection to another Anonity node. Once connected, nodes exchange chain data, relay transactions, and participate in the reputation challenge protocol.

---

## Request

| Property | Value |
|---|---|
| **Method** | `POST` |
| **URL** | `/api/connect` |
| **Auth required** | No |
| **Content-Type** | `application/json` |

### Headers

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |

### Request Body

```json
{
  "host": "192.168.1.10",
  "port": 6001
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `host` | `string` | Yes | Hostname or IP address of the remote node |
| `port` | `integer` | Yes | P2P port of the remote node (default P2P port is `6000`) |

---

## Success Response — `200 OK`

```json
{
  "ok": true
}
```

The TCP connection to the remote node has been established. The base blockchain layer will now exchange chain headers and begin peer discovery.

---

## Error Responses

### `400 Bad Request` — missing fields

```json
{
  "error": "host and port required"
}
```

### `500 Internal Server Error` — connection failed

```json
{
  "error": "connection refused"
}
```

Returned when the TCP handshake to the remote node fails (host unreachable, port closed, etc.).

### `503 Service Unavailable` — node not initialised

```json
{
  "error": "not ready"
}
```

---

## Example

```bash
# Connect to a peer running on the same machine, P2P port 6001
curl -X POST http://localhost:5001/api/connect \
  -H "Content-Type: application/json" \
  -d '{"host": "127.0.0.1", "port": 6001}'
```

```json
{ "ok": true }
```

---

## Notes

- After connecting, call [`POST /api/sync`](./sync.md) to pull the peer's chain immediately. The node also auto-syncs every 60 seconds in the background.
- Multiple peers can be added by calling this endpoint multiple times.
- Use [`GET /api/peers`](./peers.md) to verify the current peer list.
- The **P2P port** (default `6000`) is distinct from the **API port** (default `5001`). Always use the P2P port when connecting nodes together.

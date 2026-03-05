# GET `/api/peers`

Returns the list of P2P peers the local node is currently connected to.

---

## Request

| Property | Value |
|---|---|
| **Method** | `GET` |
| **URL** | `/api/peers` |
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
  "192.168.1.10:6000",
  "192.168.1.11:6001",
  "10.0.0.5:6000"
]
```

Returns a JSON array of strings in `"host:port"` format. An empty array (`[]`) means no peers are connected.

---

## Error Responses

This endpoint does not return application-level errors (it is always available once the node is running).

---

## Example

```bash
curl http://localhost:5001/api/peers
```

```json
["127.0.0.1:6001"]
```

### No peers connected

```bash
curl http://localhost:5001/api/peers
```

```json
[]
```

---

## Notes

- To add a new peer, use [`POST /api/connect`](./connect.md).
- At least one peer must be connected before calling [`POST /api/sync`](./sync.md).
- The list reflects **live** TCP connections; a peer that drops will disappear from this list automatically.

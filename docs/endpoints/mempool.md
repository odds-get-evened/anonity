# GET `/api/mempool`

Returns the list of pending (unconfirmed) transactions waiting to be included in the next mined block.

---

## Request

| Property | Value |
|---|---|
| **Method** | `GET` |
| **URL** | `/api/mempool` |
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
    "uid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "type": 10,
    "requester": "04a1b2c3d4e5f6aa...",
    "timestamp": 1710001234.567
  },
  {
    "uid": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "type": 11,
    "requester": "04deadbeef0102...",
    "timestamp": 1710001300.0
  }
]
```

Returns an empty array (`[]`) when there are no pending transactions.

### Field Reference

| Field | Type | Description |
|---|---|---|
| `uid` | `string` (UUID) | Unique transaction identifier |
| `type` | `integer` | Transaction type code (see table below) |
| `requester` | `string` (hex) | Public key of the identity that submitted the transaction |
| `timestamp` | `number` | Unix timestamp (seconds) when the transaction was created |

### Transaction Type Codes

| Code | Name | Description |
|---|---|---|
| `10` | `IDENTITY_REGISTER` | New identity registration with PoW proof |
| `11` | `REPUTATION_MINE` | Successfully solved reputation challenge |
| `12` | `REPUTATION_IGNORE` | Expired challenge penalty (submitted by the challenger) |
| `0` | `COINBASE` | Block reward (base chain) |
| `1` | `REQUEST` | Coin request (base chain) |
| `2` | `RELEASE` | Coin release (base chain) |
| `3` | `TRANSFER` | Coin transfer (base chain) |
| `4` | `BUYOUT_OFFER` | Buyout offer (base chain) |

---

## Error Responses

This endpoint does not return application-level errors.

---

## Example

```bash
curl http://localhost:5001/api/mempool
```

```json
[
  {
    "uid": "a1b2c3d4-...",
    "type": 10,
    "requester": "04a1b2...",
    "timestamp": 1710001234.567
  }
]
```

### Empty mempool

```json
[]
```

---

## Notes

- The mempool count is also visible in the `mempool` field of [`GET /api/status`](./status.md).
- Transactions remain in the mempool until they are either mined (via [`POST /api/mine`](./mine.md)) or expire.
- If the mempool is non-empty, calling [`POST /api/mine`](./mine.md) will include **all** pending transactions in the next block.

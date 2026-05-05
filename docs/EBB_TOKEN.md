# Deploying and operating a Java token on the Credits blockchain via the baba-credits MCP server

This is the end-to-end recipe used to ship the **EBB** token. It walks
through every step that touches the network — compile, pack, sign,
deploy, execute, verify — with the exact request and response shapes
the `baba-credits` MCP server speaks, and the gotchas we hit on the way.

The example contract is `contracts/EBB.java` (a 1 000 000-supply,
18-decimal token implementing `BasicStandard` with `transfer`, `burn`,
and an owner-only `setFrozen` pause).

> All payloads below are **MCP tool input/output** — i.e. the JSON each
> tool receives in `arguments` and returns in its single `TextContent`
> block. They are *not* gateway HTTP routes; the MCP server fronts the
> gateway and the gateway fronts the Credits node Thrift API.

---

## 0. Prerequisites

| Thing | Value used here |
|---|---|
| MCP server URL | configured in `.env.local` (`EBB_MCP_URL`) — HTTP+SSE legacy transport |
| MCP bearer | `EBB_MCP_BEARER` — sent on **every** request (GET `/sse` and POST `/messages/?session_id=…`) |
| Owner public key (base58) | `BABA_PUBLIC_KEY` |
| Owner private key (base58, 64 bytes = seed‖pub) | `BABA_PRIVATE_KEY` |
| Minimum CS balance for a token deploy | ≥ 0.5 CS — actual fee was 0.0087 CS, but the node validates against `feeAsString` as the *max* |

The Python helpers in `scripts/_ebb_mcp_client.py` and
`scripts/_ebb_signing.py` encapsulate the SSE transport and the
ed25519 signing.

---

## 1. Tool inventory you will need

| Tool | Purpose |
|---|---|
| `smartcontract_compile` | Java source → bytecode + `tokenStandard` |
| `smartcontract_pack` | Build a signable transaction blob (deploy or execute) |
| `smartcontract_deploy` | Submit a packed deploy + signature |
| `smartcontract_execute` | Submit a packed method-call + signature |
| `monitor_wait_for_block` | Block until the next block is finalized |
| `monitor_wait_for_smart_transaction` | Block until a specific contract has had a tx in a new block |
| `transaction_get_info` | On-chain status / from / to / fee / time of a tx |
| `transaction_result` | Smart-contract method return value (gateway cache) |
| `smartcontract_get` | Deployer, source, bytecode, transaction count |
| `smartcontract_state` | Public methods + exposed state variables |
| `monitor_get_balance` / `monitor_get_wallet_info` | CS balance |
| `tokens_balances_get` / `tokens_holders_get` / `tokens_info` | Token-indexer queries (only on nodes with the indexer enabled) |
| `diag_get_supply` / `diag_get_active_nodes` | Health / sanity calls |

---

## 2. Compile the source

**Tool:** `smartcontract_compile`

**Input:**
```json
{
  "sourceCode": "<full EBB.java text>"
}
```

**Output (excerpt for EBB):**
```json
{
  "success": true,
  "message": "Success: ",
  "tokenStandard": 1,
  "byteCodeObjects": [
    { "name": "EBB", "byteCode": "<base64 — 5076 chars>" }
  ]
}
```

`tokenStandard != 0` is the signal that the node recognises the
contract as a token (i.e. it implements `BasicStandard` /
`ExtensionStandard` correctly). A plain non-token contract gets
`tokenStandard: 0`.

`byteCodeObjects` is a **list** because Java sources can produce
multiple classes; pass it through verbatim to the next steps.

---

## 3. Pack the deploy transaction

**Tool:** `smartcontract_pack` with `operation: "deploy"`.

**Input (this is what the deploy actually sent):**
```json
{
  "PublicKey": "<owner base58 pub>",
  "operation": "deploy",
  "sourceCode": "<EBB.java>",
  "byteCodeObjects": [
    { "name": "EBB", "byteCode": "<base64>" }
  ],
  "feeAsString": "1",
  "UserData": ""
}
```

**Output (excerpt):**
```json
{
  "success": true,
  "transactionInnerId": 235,
  "contractAddress": "EK7Fm4JPmfACz1GYt1yhh917bELfckMDkmDr1eUras6W",
  "dataResponse": {
    "transactionPackagedStr": "<base58 of the raw bytes to be signed>",
    "recommendedFee": 0.034960937500000004,
    "contractAddress": "EK7Fm4JPmfACz1GYt1yhh917bELfckMDkmDr1eUras6W",
    "actualSum": 0,
    "smartContractResult": null,
    "publicKey": null
  },
  "actualFee": 0,
  "transactionId": 0,
  "messageError": null
}
```

Three fields you must extract:

| Where | Field | What for |
|---|---|---|
| top-level | `transactionInnerId` | mandatory in `smartcontract_deploy` to bind it to this pack |
| `dataResponse` | `transactionPackagedStr` | base58 blob you sign |
| top-level **or** `dataResponse` | `contractAddress` | deterministic address the contract will live at |

> **Pitfall #1 — `transactionInnerId` is at the top level**, *not* inside `dataResponse`. We initially read it from the wrong place and got `KeyError: 'transactionInnerId'`.

> **Pitfall #2 — `feeAsString: "0"` is rejected.** `feeAsString` is a *max* the wallet will pay, not a hint. `"0"` makes the node refuse with `Transaction's max fee is not enough to issue transaction. Counted fee will be 0.183594.` Use a comfortable upper bound (we used `"1"` for deploy, `"0.5"` for execute). The `recommendedFee` in `dataResponse` is **advisory** and was an order of magnitude lower than the actual fee in our case (0.035 advised vs 0.18 actual estimate vs 0.0087 actually charged).

---

## 4. Sign client-side (ed25519)

The Credits convention:

- **Private key** (base58) is the concatenation `seed (32) || derived_pub (32)`.
  PyNaCl wants only the 32-byte seed.
- The signature is over the **raw bytes** of `transactionPackagedStr`
  decoded from base58 — *not* over the base58 string itself.
- The signature you submit is base58 of the 64-byte ed25519 signature.

```python
import base58, nacl.signing

def sign_packaged(packaged_b58: str, private_key_b58: str) -> str:
    raw = base58.b58decode(packaged_b58)
    sk = nacl.signing.SigningKey(base58.b58decode(private_key_b58)[:32])
    sig = sk.sign(raw).signature
    return base58.b58encode(sig).decode()
```

> **Pitfall #3 — many client libs default to signing the *string*.**
> If the node returns a vague rejection on a packed tx that looks
> well-formed, double-check you decoded the base58 first.

---

## 5. Deploy

**Tool:** `smartcontract_deploy`

**Input:**
```json
{
  "PublicKey":            "<owner base58 pub>",
  "sourceCode":           "<EBB.java>",
  "byteCodeObjects":      [ { "name": "EBB", "byteCode": "<base64>" } ],
  "TransactionSignature": "<base58 of ed25519 signature>",
  "transactionInnerId":   235,
  "feeAsString":          "1",
  "UserData":             ""
}
```

**Output (excerpt):**
```json
{
  "success": true,
  "transactionId": "174756873.1",
  "actualFee":     "0.008740234375000001",
  "messageError":  null
}
```

The same `feeAsString` and `transactionInnerId` you passed to `pack`
**must** be passed here, otherwise the signature is invalid for the
re-built blob.

---

## 6. Wait and confirm

```json
// monitor_wait_for_block
{ "timeoutMs": 30000 }

// transaction_get_info
{ "transactionId": "174756873.1" }
```

`transaction_get_info` returns the canonical on-chain view:

```json
{
  "id": "174756873.1",
  "blockNum": "174756873",
  "status": "Success",
  "success": true,
  "found": true,
  "fromAccount": "<owner>",
  "toAccount":   "EK7Fm4JPmfACz1GYt1yhh917bELfckMDkmDr1eUras6W",
  "transactionType": 1,
  "transactionTypeDefinition": "TT_SmartDeploy",
  "fee":  "(sum of 2) 0.008740234375000001",
  "time": "2026-04-30T12:55:33.421Z"
}
```

`status: "Success"` is the source of truth. The contract is now live
at `toAccount`.

---

## 7. Execute a contract method

The flow is the same shape as deploy: `pack` → `sign` → `execute`,
just with `operation: "execute"` and the contract address as
`ReceiverPublicKey`.

### 7a. Pack

**Input (real example: `transfer("MooNRor8…", "1000")`):**
```json
{
  "PublicKey":         "<owner base58 pub>",
  "operation":         "execute",
  "ReceiverPublicKey": "EK7Fm4JPmfACz1GYt1yhh917bELfckMDkmDr1eUras6W",
  "method":            "transfer",
  "params": [
    { "v_string": "MooNRor8TcLT3xpAw3UvA2Q6xT8huRoQojn6ZSveDpP" },
    { "v_string": "1000" }
  ],
  "feeAsString": "0.5",
  "UserData":    ""
}
```

`params` is a list of variant objects: `{"v_string": ...}` for a Java
`String`, `{"v_int": ...}` for `int`, `{"v_bool": ...}` for `boolean`,
etc. Order matches the Java method signature.

### 7b. Sign

Same ed25519 routine as §4, over `dataResponse.transactionPackagedStr`.

### 7c. Execute

```json
{
  "PublicKey":            "<owner base58 pub>",
  "ReceiverPublicKey":    "EK7Fm4JPmfACz1GYt1yhh917bELfckMDkmDr1eUras6W",
  "method":               "transfer",
  "params":               [ { "v_string": "MooNRor8…" }, { "v_string": "1000" } ],
  "TransactionSignature": "<base58>",
  "transactionInnerId":   260,
  "feeAsString":          "0.5",
  "UserData":             ""
}
```

Output (real, `txId 174958376.1`):
```json
{
  "success":      true,
  "transactionId":"174958376.1",
  "actualFee":    "0.008740234375000001",
  "messageError": null
}
```

> **Pitfall #4 — short transaction TTL.** Once `pack` runs, the blob
> embeds a timestamp / round number that the node validates on
> arrival. If `execute` round-trips slowly (proxy lag, slow tunnel,
> queueing), the node rejects with `Failure: transaction is expired`
> and `txId: 0.1`. We saw this when the SSE round-trip took >30 s.
> Mitigations: warm-up call before `pack` (e.g. a `diag_get_supply`
> on the same session so the connection / TLS / cache is hot); retry
> with backoff; if your tunnel is consistently slow, fix that first.

---

## 8. Verify what happened on a transaction

This is the part where the node's exposed surface matters.

### 8a. Canonical: `transaction_get_info`

Always works. Returns `status`, `fromAccount`, `toAccount`, `fee`,
`time`, `transactionTypeDefinition` (`TT_SmartDeploy` or `TT_SmartExecute`).

A `status: "Success"` for `TT_SmartExecute` means the JVM ran the
method **without throwing**. Combined with reading the contract
source, this gives you the post-conditions for free: any pre-check
(`if (frozen) throw …`, `if (amt.signum() <= 0) throw …`,
`if (fromBal.compareTo(amt) < 0) throw …`) that would have aborted
clearly didn't, so the writes (`balances.put(initiator, fromBal − amt)`,
`balances.put(to, toBal + amt)`) committed.

For our `transfer(MooNRor…, "1000")` tx `174958376.1`:

```json
{
  "id": "174958376.1",
  "blockNum": "174958376",
  "status": "Success",
  "success": true,
  "found": true,
  "fromAccount": "3EDCyBgXoD4i35wYAf71vh3nqCDtVASmD2qDB7TgGpVw",
  "toAccount":   "EK7Fm4JPmfACz1GYt1yhh917bELfckMDkmDr1eUras6W",
  "transactionType": 2,
  "transactionTypeDefinition": "TT_SmartExecute",
  "fee":  "(sum of 2) 0.008740234375000001",
  "time": "2026-05-04T13:15:17.113Z",
  "innerId": 260
}
```

### 8b. Method return value: `transaction_result`

```json
{ "transactionId": "174958376.1" }
```

When the gateway has the SC API answer cached:
```json
{ "found": true, "success": true, "result": { ... }, "value": "1000" }
```

When it doesn't (the case for *both* of our txs):
```json
{
  "found":   false,
  "success": false,
  "result":  null,
  "executor":null,
  "message": "Transaction not found in API answers"
}
```

This is **not a failure of the transaction** — it's a gateway-side
cache miss. The on-chain status from `transaction_get_info` is still
authoritative. Different gateway/node combinations differ widely on
how aggressively they retain SC API answers; on a cold gateway you
will get `"Transaction not found in API answers"` even seconds after
the tx is finalised.

### 8c. Contract metadata + tx counter: `smartcontract_get`

```json
{ "address": "EK7Fm4JPmfACz1GYt1yhh917bELfckMDkmDr1eUras6W" }
```

```json
{
  "success": true,
  "contract": {
    "address":            "EK7Fm4JPmfACz1GYt1yhh917bELfckMDkmDr1eUras6W",
    "deployer":           "3EDCyBgXoD4i35wYAf71vh3nqCDtVASmD2qDB7TgGpVw",
    "createTime":         1777553940853,
    "transactionId":      "174756873.1",
    "transactionsCount":  52,
    "sourceCode":         "<original EBB.java>",
    "byteCodeObjects":    [ { "name": "EBB", "byteCode": "<base64>" } ]
  }
}
```

A monotonically increasing `transactionsCount` is a useful sanity
check after each call.

### 8d. Public methods + state vars: `smartcontract_state`

```json
{ "address": "EK7Fm4JPmfACz1GYt1yhh917bELfckMDkmDr1eUras6W" }
```

```json
{
  "success": true,
  "methods": [
    { "name": "transfer",
      "arguments": [
        { "name": "to",     "type": "java.lang.String" },
        { "name": "amount", "type": "java.lang.String" }
      ],
      "returnType": "boolean" },
    { "name": "balanceOf",
      "arguments": [
        { "name": "address", "type": "java.lang.String" }
      ],
      "returnType": "java.lang.String" },
    /* ... approve, burn, getName, getSymbol, getDecimal, totalSupply,
       allowance, transferFrom, setFrozen ... */
  ],
  "variables": []
}
```

`variables: []` is normal for our contract: the node only exposes
*public* fields with a getter; private fields like
`Map<String, BigDecimal> balances` are not visible here. To read the
balance of a specific holder you must `execute` the `balanceOf`
method (and pay the fee).

### 8e. Token indexer (optional — not all nodes)

| Tool | Input |
|---|---|
| `tokens_info` | `{ "token": "<contract addr>" }` |
| `tokens_holders_get` | `{ "token": "<addr>", "offset": 0, "limit": 20, "order": 0, "desc": true }` |
| `tokens_balances_get` | `{ "PublicKey": "<wallet>", "offset": 0, "limit": 50 }` |

On a node *with* the token indexer enabled these return the live
holders list, supply, holder count, and per-wallet token balances
(including the EBB balance of `MooNRor8…`).

On a node **without** the indexer (the case for the gateway we used):

```json
{
  "success": false,
  "message": "This node doesn't provide such info",
  "balances": []
}
```

If you need authoritative on-chain balance reads and the indexer is
off, your only options are:
1. point the MCP server's gateway at a node with `tokens` API enabled, or
2. perform a paid `execute` of `balanceOf(addr)` and have a gateway
   that retains the SC API answer (see §8b).

---

## 9. Concrete example: the EBB lifecycle

| Step | Tool | TxId | Block | Status | Fee (CS) |
|---|---|---|---|---|---|
| Deploy contract `EBB` | `smartcontract_compile`, `smartcontract_pack`, `smartcontract_deploy` | **174756873.1** | 174756873 | Success | 0.008740234375 |
| `transfer("MooNRor8…", "1000")` from owner | `smartcontract_pack`, `smartcontract_execute` | **174958376.1** | 174958376 | Success | 0.008740234375 |
| `balanceOf("MooNRor8…")` from owner | `smartcontract_pack`, `smartcontract_execute` | **175020676.1** | 175020676 | Success | 0.008740234375 |

Contract address: `EK7Fm4JPmfACz1GYt1yhh917bELfckMDkmDr1eUras6W`
Owner: `3EDCyBgXoD4i35wYAf71vh3nqCDtVASmD2qDB7TgGpVw`
Recipient: `MooNRor8TcLT3xpAw3UvA2Q6xT8huRoQojn6ZSveDpP`

After the transfer, since the node hosting our gateway has neither
the token indexer nor a populated SC-result cache, the **inferred**
post-state is:

| Wallet | EBB balance |
|---|---|
| Owner | 1 000 000 − 1 000 = **999 000** |
| `MooNRor8…` | 0 + 1 000 = **1 000** |

Inference is sound because `EBB.transfer` is the only code path that
moved EBB and `transaction_get_info.status == "Success"` proves it ran
to completion without throwing. To *observe* the numbers you would
need an indexer-enabled node (§8e).

---

## 10. Pitfalls cheatsheet

| Symptom | Root cause | Fix |
|---|---|---|
| `KeyError: 'transactionInnerId'` after `pack` | Read it from `dataResponse` instead of top level | Read `pack["transactionInnerId"]` |
| `"Counted fee will be 0.X"` rejection | `feeAsString: "0"` (zero max-fee) | Pass a real upper bound, e.g. `"1"` for deploy, `"0.5"` for execute |
| `Failure: transaction is expired` instantly | TTL elapsed between `pack` and `execute` due to slow tunnel | Warm up the SSE session, retry with backoff, fix the proxy |
| `"Transaction not found in API answers"` | Gateway didn't cache the SC method return | OK: `transaction_get_info.status == Success` is enough; or use an indexer-enabled gateway |
| `"This node doesn't provide such info"` on `tokens_*` | Token indexer disabled on the node | Point at an indexer-enabled node, or do a paid `execute balanceOf(...)` |
| Empty `variables: []` from `smartcontract_state` | Field is not a public getter | Use a public method (`balanceOf`, `totalSupply`, …) instead of inspecting state |
| 401 on POST `/messages/?session_id=…` | Bearer missing or rotated | Re-load `Authorization` header on every request, refresh from server admin |
| 403 with `x-deny-reason: resolve_no_records` | The MCP server's upstream proxy can't resolve its own backend | Server-side: fix DNS / Envoy cluster; not a client issue |

---

## 11. Files in this repo

| Path | Role |
|---|---|
| `contracts/EBB.java` | Token source |
| `scripts/_ebb_mcp_client.py` | MCP-SSE client (URL + bearer from `.env.local`) |
| `scripts/_ebb_signing.py` | ed25519 sign + keypair helpers |
| `scripts/deploy_ebb.py` | One-shot deploy pipeline (compile → pack → sign → deploy → wait → verify) |
| `scripts/test_ebb_transfers.py` | Multi-wallet end-to-end scenarios (transfer / burn / pause / holders) |
| `payloads/smartcontract/EBB_Compile.json` | Reference compile payload |
| `.env.local` *(gitignored)* | `EBB_MCP_URL`, `EBB_MCP_BEARER`, `BABA_PUBLIC_KEY`, `BABA_PRIVATE_KEY`, `EBB_CONTRACT_ADDRESS`, `EBB_DEPLOY_TX_ID` |
| `.env.test.local` *(gitignored)* | Generated test wallet seeds A/B/C |

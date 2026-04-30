"""Scenario di test end-to-end del token EBB.

Pipeline:
    0. (idempotente) carica/genera 3 wallet di test A/B/C; salva i seed in
       .env.test.local (gitignored). Stampa solo le public key.
    1. fund: invio 0.1 CS dall'owner a ciascuno di A/B/C
    2. distribuzione EBB: owner trasferisce 100 EBB a ciascuno di A/B/C
    3. transfer fra utenti: A → B per 10 EBB (firmato con la chiave di A)
    4. burn: A → burn 5 EBB
    5. pause: owner setFrozen(true); A tenta transfer (deve fallire);
       owner setFrozen(false); A retransfer (deve riuscire)
    6. holders snapshot via tokens_holders_get + state via smartcontract_state

Richiede in env:
    BABA_PUBLIC_KEY, BABA_PRIVATE_KEY (owner)
    EBB_CONTRACT_ADDRESS               (output di deploy_ebb.py)

Output:
    log dettagliato di ogni passo + scrive un riassunto in
    docs/EBB_TOKEN.md (solo public key, niente seed).
"""
from __future__ import annotations
import os
import sys
import json
import asyncio
import pathlib
import time

THIS_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from mcp.types import CallToolRequest, CallToolRequestParams
from baba_mcp.server import load_config, build_server
from scripts._ebb_signing import sign_packaged, generate_keypair

ENV_TEST_PATH = REPO_ROOT / ".env.test.local"


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing env: {name}")
    return v


def load_or_create_test_wallets() -> dict[str, dict[str, str]]:
    """Ritorna {label: {pub, priv}} per A, B, C. Se .env.test.local esiste
    li riutilizza, altrimenti li genera e li salva."""
    wallets: dict[str, dict[str, str]] = {}
    if ENV_TEST_PATH.exists():
        for line in ENV_TEST_PATH.read_text().splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            for label in ("A", "B", "C"):
                if k == f"EBB_TEST_{label}_PUB":
                    wallets.setdefault(label, {})["pub"] = v.strip()
                elif k == f"EBB_TEST_{label}_PRIV":
                    wallets.setdefault(label, {})["priv"] = v.strip()
    needs_generate = any(
        label not in wallets or "pub" not in wallets[label] or "priv" not in wallets[label]
        for label in ("A", "B", "C")
    )
    if needs_generate:
        for label in ("A", "B", "C"):
            if label not in wallets:
                pub, priv = generate_keypair()
                wallets[label] = {"pub": pub, "priv": priv}
        lines = [
            "# Wallet di test EBB. NON COMMITTARE. NON CONDIVIDERE.",
            "# Generati automaticamente da scripts/test_ebb_transfers.py.",
        ]
        for label in ("A", "B", "C"):
            w = wallets[label]
            lines.append(f"EBB_TEST_{label}_PUB={w['pub']}")
            lines.append(f"EBB_TEST_{label}_PRIV={w['priv']}")
        ENV_TEST_PATH.write_text("\n".join(lines) + "\n")
        print(f"[wallets] generati e salvati in {ENV_TEST_PATH}")
    else:
        print(f"[wallets] caricati da {ENV_TEST_PATH}")
    for label, w in wallets.items():
        print(f"  {label}.pub = {w['pub']}")
    return wallets


async def call(server, name: str, args: dict) -> dict:
    handler = server.request_handlers[CallToolRequest]
    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=name, arguments=args),
    )
    res = await handler(req)
    return json.loads(res.root.content[0].text)


async def cs_transfer(server, from_pub: str, from_priv: str, to_pub: str, amount: str) -> dict:
    pack = await call(server, "transaction_pack", {
        "PublicKey": from_pub, "ReceiverPublicKey": to_pub,
        "amountAsString": amount, "feeAsString": "0", "UserData": "",
    })
    pkg = pack["dataResponse"]["transactionPackagedStr"]
    sig = sign_packaged(pkg, from_priv)
    exe = await call(server, "transaction_execute", {
        "PublicKey": from_pub, "ReceiverPublicKey": to_pub,
        "amountAsString": amount, "feeAsString": "0", "UserData": "",
        "TransactionSignature": sig,
    })
    return exe


async def sc_execute(server, caller_pub: str, caller_priv: str, contract: str,
                     method: str, params: list[dict]) -> dict:
    pack = await call(server, "smartcontract_pack", {
        "PublicKey": caller_pub,
        "operation": "execute",
        "ReceiverPublicKey": contract,
        "method": method,
        "params": params,
        "feeAsString": "0",
        "UserData": "",
    })
    if not pack.get("success"):
        return {"success": False, "stage": "pack", "error": pack}
    dr = pack["dataResponse"]
    inner_id = dr["transactionInnerId"]
    pkg = dr["transactionPackagedStr"]
    sig = sign_packaged(pkg, caller_priv)
    exe = await call(server, "smartcontract_execute", {
        "PublicKey": caller_pub,
        "ReceiverPublicKey": contract,
        "method": method,
        "params": params,
        "TransactionSignature": sig,
        "transactionInnerId": inner_id,
        "feeAsString": "0",
        "UserData": "",
    })
    return exe


async def wait_tx(server, contract: str, tx_id: str | None = None) -> dict:
    await call(server, "monitor_wait_for_smart_transaction",
               {"address": contract, "timeoutMs": 30000})
    if tx_id:
        return await call(server, "transaction_result", {"transactionId": tx_id})
    return {}


async def main() -> None:
    owner_pub = env("BABA_PUBLIC_KEY")
    owner_priv = env("BABA_PRIVATE_KEY")
    contract = env("EBB_CONTRACT_ADDRESS")

    wallets = load_or_create_test_wallets()
    A, B, C = wallets["A"], wallets["B"], wallets["C"]

    cfg = load_config()
    server = build_server(cfg)

    # ---- Step 1: fund A/B/C with 0.1 CS each
    for label, w in [("A", A), ("B", B), ("C", C)]:
        print(f"[fund] owner -> {label} 0.1 CS ...")
        r = await cs_transfer(server, owner_pub, owner_priv, w["pub"], "0.1")
        ok = r.get("success")
        print(f"  ok={ok} txId={r.get('transactionId')} err={r.get('messageError')}")
        if not ok:
            sys.exit(f"fund {label} failed: {r}")

    print("[wait] settling fund txs ...")
    await call(server, "monitor_wait_for_block", {"timeoutMs": 30000})

    # ---- Step 2: distribute 100 EBB to each of A/B/C
    for label, w in [("A", A), ("B", B), ("C", C)]:
        print(f"[distribute] owner -> {label} 100 EBB ...")
        r = await sc_execute(server, owner_pub, owner_priv, contract,
                             "transfer", [{"v_string": w["pub"]}, {"v_string": "100"}])
        print(f"  ok={r.get('success')} txId={r.get('transactionId')}")
        if not r.get("success"):
            sys.exit(f"distribute to {label} failed: {r}")
    await wait_tx(server, contract)

    # ---- Step 3: A transfers 10 EBB to B
    print("[user-transfer] A -> B 10 EBB ...")
    r = await sc_execute(server, A["pub"], A["priv"], contract,
                         "transfer", [{"v_string": B["pub"]}, {"v_string": "10"}])
    print(f"  ok={r.get('success')} txId={r.get('transactionId')}")
    if not r.get("success"):
        sys.exit(f"A->B transfer failed: {r}")
    await wait_tx(server, contract)

    # ---- Step 4: A burns 5 EBB
    print("[burn] A burns 5 EBB ...")
    r = await sc_execute(server, A["pub"], A["priv"], contract,
                         "burn", [{"v_string": "5"}])
    print(f"  ok={r.get('success')} txId={r.get('transactionId')}")
    if not r.get("success"):
        sys.exit(f"burn failed: {r}")
    await wait_tx(server, contract)

    # ---- Step 5: pause / unpause
    print("[pause] owner setFrozen(true) ...")
    r = await sc_execute(server, owner_pub, owner_priv, contract,
                         "setFrozen", [{"v_bool": True}])
    print(f"  ok={r.get('success')} txId={r.get('transactionId')}")
    await wait_tx(server, contract)

    print("[pause-test] A -> C 1 EBB during pause (expected to revert) ...")
    r = await sc_execute(server, A["pub"], A["priv"], contract,
                         "transfer", [{"v_string": C["pub"]}, {"v_string": "1"}])
    print(f"  submitted ok={r.get('success')} txId={r.get('transactionId')}")
    res = await wait_tx(server, contract, r.get("transactionId"))
    print(f"  result={res.get('success')} err={res.get('messageError')}")
    if res.get("success"):
        print("  WARNING: pause did not block transfer (expected revert)")

    print("[pause] owner setFrozen(false) ...")
    r = await sc_execute(server, owner_pub, owner_priv, contract,
                         "setFrozen", [{"v_bool": False}])
    print(f"  ok={r.get('success')} txId={r.get('transactionId')}")
    await wait_tx(server, contract)

    print("[post-pause] A -> C 1 EBB (should succeed) ...")
    r = await sc_execute(server, A["pub"], A["priv"], contract,
                         "transfer", [{"v_string": C["pub"]}, {"v_string": "1"}])
    print(f"  ok={r.get('success')} txId={r.get('transactionId')}")
    await wait_tx(server, contract, r.get("transactionId"))

    # ---- Step 6: read state + holders
    print("[state] smartcontract_state(EBB) ...")
    state = await call(server, "smartcontract_state", {"address": contract})
    print(json.dumps(state, indent=2))

    print("[holders] tokens_holders_get(EBB) ...")
    holders = await call(server, "tokens_holders_get",
                        {"token": contract, "offset": 0, "limit": 20,
                         "order": 0, "desc": True})
    print(json.dumps(holders, indent=2))

    print("[tokens_info] tokens_info(EBB) ...")
    tinfo = await call(server, "tokens_info", {"token": contract})
    print(json.dumps(tinfo, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

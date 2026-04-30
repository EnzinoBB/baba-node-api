"""Deploy del token EBB su Credits via gateway baba-credits.

Pipeline (rispetta `recipes/deploy-contract.md`):
    1. smartcontract_compile(sourceCode=EBB.java)
    2. smartcontract_pack(operation="deploy", sourceCode, byteCodeObjects)
    3. firma client-side ed25519 (PyNaCl) di transactionPackagedStr
    4. smartcontract_deploy(stessi campi del Pack + signature + transactionInnerId)
    5. monitor_wait_for_block + transaction_get_info per conferma
    6. tokens_info(token=deployedAddress) per validare la registrazione del token

Richiede in env:
    BABA_PUBLIC_KEY  - base58 della wallet owner (pubblico)
    BABA_PRIVATE_KEY - base58 64 byte (seed||pub) della wallet owner

Stampa in stdout l'indirizzo del contratto deployato e la transactionId.
NON scrive segreti su disco.
"""
from __future__ import annotations
import os
import sys
import json
import asyncio
import pathlib

THIS_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from mcp.types import CallToolRequest, CallToolRequestParams
from baba_mcp.server import load_config, build_server
from scripts._ebb_signing import sign_packaged

EBB_SRC_PATH = REPO_ROOT / "contracts" / "EBB.java"


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing env: {name}")
    return v


async def call(server, name: str, args: dict) -> dict:
    handler = server.request_handlers[CallToolRequest]
    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=name, arguments=args),
    )
    res = await handler(req)
    return json.loads(res.root.content[0].text)


async def main() -> None:
    pk = env("BABA_PUBLIC_KEY")
    sk = env("BABA_PRIVATE_KEY")
    src = EBB_SRC_PATH.read_text()

    cfg = load_config()
    server = build_server(cfg)

    bal = await call(server, "monitor_get_balance", {"PublicKey": pk})
    print(f"[balance] owner CS = {bal.get('balance')}")
    if float(bal.get("balance", 0) or 0) < 0.15:
        sys.exit("owner has < 0.15 CS — need at least the deploy fee + a margin")

    print("[compile] compiling EBB.java ...")
    comp = await call(server, "smartcontract_compile", {"sourceCode": src})
    if not comp.get("success"):
        sys.exit(f"compile failed: {comp}")
    bco = comp["byteCodeObjects"]
    token_std = comp.get("tokenStandard")
    print(f"[compile] ok — classes={[b['name'] for b in bco]} tokenStandard={token_std}")

    print("[pack] packing deploy tx ...")
    pack = await call(server, "smartcontract_pack", {
        "PublicKey": pk,
        "operation": "deploy",
        "sourceCode": src,
        "byteCodeObjects": bco,
        "feeAsString": "0",
        "UserData": "",
    })
    if not pack.get("success"):
        sys.exit(f"pack failed: {pack}")
    dr = pack["dataResponse"]
    inner_id = dr["transactionInnerId"]
    pkg = dr["transactionPackagedStr"]
    deployed_addr = dr.get("deployedAddress")
    rec_fee = dr.get("recommendedFee")
    print(f"[pack] inner_id={inner_id} deployedAddress={deployed_addr} recommendedFee={rec_fee}")

    sig = sign_packaged(pkg, sk)
    print("[sign] ok")

    print("[deploy] submitting ...")
    dep = await call(server, "smartcontract_deploy", {
        "PublicKey": pk,
        "sourceCode": src,
        "byteCodeObjects": bco,
        "TransactionSignature": sig,
        "transactionInnerId": inner_id,
        "feeAsString": "0",
        "UserData": "",
    })
    if not dep.get("success"):
        sys.exit(f"deploy failed: {dep}")
    tx_id = dep.get("transactionId")
    fee = dep.get("actualFee")
    print(f"[deploy] OK — txId={tx_id} actualFee={fee} addr={dep.get('deployedAddress')}")

    print("[wait] waiting next block ...")
    await call(server, "monitor_wait_for_block", {"timeoutMs": 30000})

    info = await call(server, "transaction_get_info", {"transactionId": tx_id})
    print(f"[confirm] status={info.get('status')} found={info.get('found')}")

    addr = dep.get("deployedAddress") or deployed_addr
    tinfo = await call(server, "tokens_info", {"token": addr})
    print(f"[tokens_info] {json.dumps(tinfo, indent=2)}")

    print()
    print(f"EBB_CONTRACT_ADDRESS={addr}")
    print(f"EBB_DEPLOY_TX_ID={tx_id}")


if __name__ == "__main__":
    asyncio.run(main())

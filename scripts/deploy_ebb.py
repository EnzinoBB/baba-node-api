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

from scripts._ebb_mcp_client import open_session, call
from scripts._ebb_signing import sign_packaged

EBB_SRC_PATH = REPO_ROOT / "contracts" / "EBB.java"


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"missing env: {name}")
    return v


async def main() -> None:
    pk = env("BABA_PUBLIC_KEY")
    sk = env("BABA_PRIVATE_KEY")
    src = EBB_SRC_PATH.read_text()

    async with open_session() as s:
        bal = await call(s, "monitor_get_balance", {"PublicKey": pk})
        print(f"[balance] owner CS = {bal.get('balance')}")
        if float(bal.get("balance", 0) or 0) < 0.15:
            sys.exit("owner has < 0.15 CS — need at least the deploy fee + a margin")

        print("[compile] compiling EBB.java ...")
        comp = await call(s, "smartcontract_compile", {"sourceCode": src})
        if not comp.get("success"):
            sys.exit(f"compile failed: {comp}")
        bco = comp["byteCodeObjects"]
        token_std = comp.get("tokenStandard")
        print(f"[compile] ok — classes={[b['name'] for b in bco]} tokenStandard={token_std}")

        max_fee = "1"  # ~5x the expected 0.18 CS deploy fee, owner has 98 CS

        print("[pack] packing deploy tx ...")
        pack = await call(s, "smartcontract_pack", {
            "PublicKey": pk,
            "operation": "deploy",
            "sourceCode": src,
            "byteCodeObjects": bco,
            "feeAsString": max_fee,
            "UserData": "",
        })
        if not pack.get("success"):
            sys.exit(f"pack failed: {pack}")
        dr = pack["dataResponse"]
        inner_id = pack["transactionInnerId"]
        pkg = dr["transactionPackagedStr"]
        deployed_addr = pack.get("contractAddress") or dr.get("contractAddress")
        rec_fee = dr.get("recommendedFee")
        print(f"[pack] inner_id={inner_id} contractAddress={deployed_addr} recommendedFee={rec_fee}")

        sig = sign_packaged(pkg, sk)
        print("[sign] ok")

        print("[deploy] submitting ...")
        dep = await call(s, "smartcontract_deploy", {
            "PublicKey": pk,
            "sourceCode": src,
            "byteCodeObjects": bco,
            "TransactionSignature": sig,
            "transactionInnerId": inner_id,
            "feeAsString": max_fee,
            "UserData": "",
        })
        if not dep.get("success"):
            sys.exit(f"deploy failed: {dep}")
        tx_id = dep.get("transactionId")
        fee = dep.get("actualFee")
        addr_from_dep = dep.get("contractAddress") or (dep.get("dataResponse") or {}).get("contractAddress")
        print(f"[deploy] OK — txId={tx_id} actualFee={fee} addr={addr_from_dep}")

        print("[wait] waiting next block ...")
        await call(s, "monitor_wait_for_block", {"timeoutMs": 30000})

        info = await call(s, "transaction_get_info", {"transactionId": tx_id})
        print(f"[confirm] status={info.get('status')} found={info.get('found')}")

        addr = addr_from_dep or deployed_addr
        tinfo = await call(s, "tokens_info", {"token": addr})
        print(f"[tokens_info] {json.dumps(tinfo, indent=2)}")

        print()
        print(f"EBB_CONTRACT_ADDRESS={addr}")
        print(f"EBB_DEPLOY_TX_ID={tx_id}")


if __name__ == "__main__":
    asyncio.run(main())

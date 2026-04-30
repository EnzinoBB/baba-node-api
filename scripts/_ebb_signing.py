"""Helper riusabile per la firma ed25519 client-side dei pacchetti
``transactionPackagedStr`` Credits — richiesto da deploy/execute degli
smart contract e da transaction_execute.

Convenzione Credits:
- la private key è 64 byte base58 (32 seed || 32 derived pubkey)
- PyNaCl SigningKey vuole solo i 32 byte di seed
- la firma è sui RAW BYTES del pacchetto, non sulla stringa base58
"""
from __future__ import annotations
import base58
import nacl.signing


def sign_packaged(packaged_b58: str, private_key_b58: str) -> str:
    raw = base58.b58decode(packaged_b58)
    sk = nacl.signing.SigningKey(base58.b58decode(private_key_b58)[:32])
    sig = sk.sign(raw).signature
    return base58.b58encode(sig).decode()


def derive_keypair_from_private(private_key_b58: str) -> tuple[str, str]:
    """Restituisce (public_key_b58, private_key_b58_normalizzata).
    Utile per derivare la public key di un wallet di test partendo solo
    dal seed."""
    raw = base58.b58decode(private_key_b58)
    sk = nacl.signing.SigningKey(raw[:32])
    pk_bytes = bytes(sk.verify_key)
    pk_b58 = base58.b58encode(pk_bytes).decode()
    full_priv = base58.b58encode(raw[:32] + pk_bytes).decode()
    return pk_b58, full_priv


def generate_keypair() -> tuple[str, str]:
    """Crea un nuovo keypair ed25519 e ritorna (pub_b58, priv_b58_64bytes).
    La priv ha già il formato Credits (seed || pub)."""
    sk = nacl.signing.SigningKey.generate()
    seed = bytes(sk)
    pk_bytes = bytes(sk.verify_key)
    pk_b58 = base58.b58encode(pk_bytes).decode()
    priv_b58 = base58.b58encode(seed + pk_bytes).decode()
    return pk_b58, priv_b58

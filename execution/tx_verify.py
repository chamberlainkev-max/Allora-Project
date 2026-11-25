"""
execution/tx_verify.py
======================
F01 — decode and verify unsigned PumpPortal transactions before local signing.

Rejects transactions that:
  - cannot be decoded
  - use address-lookup tables that cannot be resolved
  - invoke a program outside the allowlist
  - do not invoke the Pump.fun program
  - are not signed by the expected wallet
  - do not reference the expected mint (buy / sell / create)

v0 transactions with ALTs are allowed when the caller supplies resolved
lookup-table addresses (fetched from RPC) so account keys can be expanded safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

# Pump.fun bonding-curve program (mainnet)
PUMPFUN_PROGRAM_ID = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
# Pump.fun trade router used by current PumpPortal local txs
PUMPFUN_TRADE_PROGRAM_ID = Pubkey.from_string("FAdo9NCw1ssek6Z6yeWzWjhLVsr8uiCwcWNUnKgzTnHe")

_PUMP_PROGRAMS = frozenset({
    str(PUMPFUN_PROGRAM_ID),
    str(PUMPFUN_TRADE_PROGRAM_ID),
})

_ALLOWED_PROGRAMS = frozenset({
    str(PUMPFUN_PROGRAM_ID),
    str(PUMPFUN_TRADE_PROGRAM_ID),
    "11111111111111111111111111111111",  # System
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # Associated Token
    "ComputeBudget111111111111111111111111111111",
})


class UnsafeTransactionError(ValueError):
    """Raised when an unsigned transaction fails F01 verification."""


@dataclass(frozen=True)
class TxExpectation:
    """What the caller intends the unsigned transaction to do."""

    wallet_pubkey: str
    action: str  # "buy" | "sell" | "create"
    mint: str


def build_account_keys(
    message,
    lookup_addresses: Mapping[str, Sequence[Pubkey]] | None = None,
) -> list[Pubkey]:
    """
    Expand a v0 message's account keys using resolved address-lookup tables.
    Order: static keys, then all writable ALT accounts, then all readonly ALT accounts.
    """
    keys = list(message.account_keys)
    lookups = getattr(message, "address_table_lookups", None) or []
    if not lookups:
        return keys

    if not lookup_addresses:
        raise UnsafeTransactionError("address lookup tables require resolved account keys")

    writable: list[Pubkey] = []
    readonly: list[Pubkey] = []
    for lookup in lookups:
        table_id = str(lookup.account_key)
        addrs = lookup_addresses.get(table_id)
        if addrs is None:
            raise UnsafeTransactionError(f"missing lookup table: {table_id}")
        for idx in lookup.writable_indexes:
            if idx < 0 or idx >= len(addrs):
                raise UnsafeTransactionError("lookup writable index out of range")
            writable.append(addrs[idx])
        for idx in lookup.readonly_indexes:
            if idx < 0 or idx >= len(addrs):
                raise UnsafeTransactionError("lookup readonly index out of range")
            readonly.append(addrs[idx])
    return keys + writable + readonly


def verify_unsigned_transaction(
    unsigned_tx_bytes: bytes,
    expected: TxExpectation,
    *,
    lookup_addresses: Mapping[str, Sequence[Pubkey]] | None = None,
    account_keys: Sequence[Pubkey] | None = None,
) -> None:
    """
    Decode unsigned tx bytes and verify they match the caller's intent.
    Raises UnsafeTransactionError on any mismatch.

    For v0 txs with ALTs, pass either:
      - lookup_addresses: {lookup_table_pubkey: [addresses...]} from RPC, or
      - account_keys: fully expanded key list (static + writable ALTs + readonly ALTs)
    """
    action = expected.action.lower().strip()
    if action not in {"buy", "sell", "create"}:
        raise UnsafeTransactionError(f"unsupported action: {expected.action}")

    try:
        wallet = Pubkey.from_string(expected.wallet_pubkey)
        mint = Pubkey.from_string(expected.mint)
    except Exception as exc:
        raise UnsafeTransactionError(f"invalid wallet/mint pubkey: {exc}") from exc

    try:
        tx = VersionedTransaction.from_bytes(unsigned_tx_bytes)
    except Exception as exc:
        raise UnsafeTransactionError(f"cannot decode transaction: {exc}") from exc

    message = tx.message
    lookups = getattr(message, "address_table_lookups", None) or []

    if account_keys is not None:
        keys = list(account_keys)
    else:
        try:
            keys = build_account_keys(message, lookup_addresses)
        except UnsafeTransactionError:
            raise
        except Exception as exc:
            raise UnsafeTransactionError(f"cannot expand account keys: {exc}") from exc

    if lookups and len(keys) <= len(message.account_keys):
        raise UnsafeTransactionError("address lookup tables require resolved account keys")

    if not keys:
        raise UnsafeTransactionError("transaction has no account keys")

    header = message.header
    num_signers = header.num_required_signatures
    signer_keys = keys[:num_signers]
    if wallet not in signer_keys:
        raise UnsafeTransactionError("expected wallet is not a required signer")

    if mint not in keys:
        raise UnsafeTransactionError("expected mint is not present in transaction accounts")

    instructions = list(message.instructions)
    if not instructions:
        raise UnsafeTransactionError("transaction has no instructions")

    program_ids: list[Pubkey] = []
    for ix in instructions:
        idx = ix.program_id_index
        if idx < 0 or idx >= len(keys):
            raise UnsafeTransactionError("instruction program_id_index out of range")
        program_ids.append(keys[idx])

    for program_id in program_ids:
        if str(program_id) not in _ALLOWED_PROGRAMS:
            raise UnsafeTransactionError(f"disallowed program: {program_id}")

    if not any(str(pid) in _PUMP_PROGRAMS for pid in program_ids):
        raise UnsafeTransactionError("Pump.fun program is not invoked")

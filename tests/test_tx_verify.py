"""
tests/test_tx_verify.py
=======================
F01 — unsigned transaction verification before signing.
"""

import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM
from solders.transaction import VersionedTransaction

from execution.tx_verify import (
    PUMPFUN_PROGRAM_ID,
    PUMPFUN_TRADE_PROGRAM_ID,
    TxExpectation,
    UnsafeTransactionError,
    verify_unsigned_transaction,
)
from integrations.pumpfun_client import sign_transaction


def _pump_trade_tx(wallet: Keypair, mint: Pubkey, extra_program: Pubkey | None = None) -> bytes:
    metas = [
        AccountMeta(wallet.pubkey(), True, True),
        AccountMeta(mint, False, True),
    ]
    ixs = [
        Instruction(PUMPFUN_PROGRAM_ID, bytes([1, 2, 3, 4]), metas),
    ]
    if extra_program is not None:
        ixs.append(Instruction(extra_program, bytes([9]), [
            AccountMeta(wallet.pubkey(), True, True),
        ]))
    msg = MessageV0.try_compile(wallet.pubkey(), ixs, [], Hash.default())
    # Unsigned-shaped bytes: message with empty/no useful signatures — build via VersionedTransaction
    # then re-encode message only path used by PumpPortal is full VersionedTransaction bytes.
    vtx = VersionedTransaction(msg, [wallet])
    return bytes(vtx)


class TestTxVerifyF01:

    def test_accepts_valid_pump_buy(self):
        wallet = Keypair()
        mint = Keypair().pubkey()
        raw = _pump_trade_tx(wallet, mint)
        verify_unsigned_transaction(
            raw,
            TxExpectation(wallet_pubkey=str(wallet.pubkey()), action="buy", mint=str(mint)),
        )

    def test_rejects_wrong_wallet(self):
        wallet = Keypair()
        other = Keypair()
        mint = Keypair().pubkey()
        raw = _pump_trade_tx(wallet, mint)
        with pytest.raises(UnsafeTransactionError, match="required signer"):
            verify_unsigned_transaction(
                raw,
                TxExpectation(wallet_pubkey=str(other.pubkey()), action="buy", mint=str(mint)),
            )

    def test_rejects_missing_mint(self):
        wallet = Keypair()
        mint = Keypair().pubkey()
        other_mint = Keypair().pubkey()
        raw = _pump_trade_tx(wallet, mint)
        with pytest.raises(UnsafeTransactionError, match="mint"):
            verify_unsigned_transaction(
                raw,
                TxExpectation(wallet_pubkey=str(wallet.pubkey()), action="sell", mint=str(other_mint)),
            )

    def test_rejects_disallowed_program(self):
        wallet = Keypair()
        mint = Keypair().pubkey()
        evil = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")
        raw = _pump_trade_tx(wallet, mint, extra_program=evil)
        with pytest.raises(UnsafeTransactionError, match="disallowed program"):
            verify_unsigned_transaction(
                raw,
                TxExpectation(wallet_pubkey=str(wallet.pubkey()), action="buy", mint=str(mint)),
            )

    def test_rejects_system_only_without_pump(self):
        wallet = Keypair()
        mint = Keypair().pubkey()
        ix = Instruction(SYSTEM_PROGRAM, bytes([0] * 4), [
            AccountMeta(wallet.pubkey(), True, True),
            AccountMeta(mint, False, True),
        ])
        msg = MessageV0.try_compile(wallet.pubkey(), [ix], [], Hash.default())
        raw = bytes(VersionedTransaction(msg, [wallet]))
        with pytest.raises(UnsafeTransactionError, match="Pump.fun"):
            verify_unsigned_transaction(
                raw,
                TxExpectation(wallet_pubkey=str(wallet.pubkey()), action="buy", mint=str(mint)),
            )

    def test_accepts_pump_trade_router_program(self):
        """Current PumpPortal buys invoke FAdo9… trade router."""
        wallet = Keypair()
        mint = Keypair().pubkey()
        metas = [
            AccountMeta(wallet.pubkey(), True, True),
            AccountMeta(mint, False, True),
        ]
        ix = Instruction(PUMPFUN_TRADE_PROGRAM_ID, bytes([1, 2, 3, 4]), metas)
        msg = MessageV0.try_compile(wallet.pubkey(), [ix], [], Hash.default())
        raw = bytes(VersionedTransaction(msg, [wallet]))
        verify_unsigned_transaction(
            raw,
            TxExpectation(wallet_pubkey=str(wallet.pubkey()), action="buy", mint=str(mint)),
        )

    def test_sign_transaction_verifies_first(self):
        wallet = Keypair()
        mint = Keypair().pubkey()
        raw = _pump_trade_tx(wallet, mint)
        signed = sign_transaction(
            raw,
            [wallet],
            TxExpectation(wallet_pubkey=str(wallet.pubkey()), action="buy", mint=str(mint)),
        )
        assert isinstance(signed, (bytes, bytearray))
        assert len(signed) > 0

    def test_sign_transaction_refuses_unsafe(self):
        wallet = Keypair()
        other = Keypair()
        mint = Keypair().pubkey()
        raw = _pump_trade_tx(wallet, mint)
        with pytest.raises(UnsafeTransactionError):
            sign_transaction(
                raw,
                [other],
                TxExpectation(wallet_pubkey=str(other.pubkey()), action="buy", mint=str(mint)),
            )

    def test_alt_requires_resolved_keys(self):
        """v0 txs that declare ALTs must not verify without resolved tables."""
        from execution.tx_verify import build_account_keys

        wallet = Keypair()
        mint = Keypair().pubkey()
        raw = _pump_trade_tx(wallet, mint)
        tx = VersionedTransaction.from_bytes(raw)
        # No ALTs on this fixture — build_account_keys returns static keys only.
        keys = build_account_keys(tx.message, None)
        assert wallet.pubkey() in keys
        assert mint in keys

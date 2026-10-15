"""
execution/rpc_client.py
========================
Solana RPC connectivity layer.

Provides:
  - Async connection management (primary + backup endpoints)
  - Timeout handling
  - Automatic retry with failover between endpoints
  - Transaction submission + confirmation

Supported providers: Helius, QuickNode (URL from env).
"""

import asyncio
from typing import Any

import httpx
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction_status import TransactionConfirmationStatus

from config import settings
from logging_config.logger import get_logger

log = get_logger(__name__)

_CONFIRMED_STATUSES = (
    TransactionConfirmationStatus.Confirmed,
    TransactionConfirmationStatus.Finalized,
)


class SolanaRPCClient:
    """
    Managed Solana async RPC client with failover.

    Usage:
        async with SolanaRPCClient() as rpc:
            balance = await rpc.get_balance(pubkey)
    """

    def __init__(self) -> None:
        self._urls: list[str] = list(
            dict.fromkeys([settings.SOLANA_RPC_URL, settings.SOLANA_RPC_URL_BACKUP])
        )
        self._clients: list[AsyncClient] = []
        self._active_idx = 0
        self._http: httpx.AsyncClient | None = None

    # ---- Lifecycle ----

    async def connect(self) -> None:
        """Open connections to all configured RPC endpoints."""
        self._clients = [
            AsyncClient(url, commitment=Confirmed, timeout=settings.RPC_TIMEOUT)
            for url in self._urls
        ]
        self._http = httpx.AsyncClient(timeout=settings.RPC_TIMEOUT)
        self._active_idx = 0
        log.info("rpc_connected", endpoints=len(self._urls), network=settings.SOLANA_NETWORK)

    async def close(self) -> None:
        for client in self._clients:
            await client.close()
        if self._http:
            await self._http.aclose()
        log.info("rpc_closed")

    async def __aenter__(self) -> "SolanaRPCClient":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ---- Retry + failover ----

    def _failover(self) -> None:
        if len(self._clients) > 1:
            self._active_idx = (self._active_idx + 1) % len(self._clients)
            log.warning("rpc_failover", endpoint_index=self._active_idx)

    async def _with_retry(self, method_name: str, fn) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, settings.RPC_MAX_RETRIES + 1):
            try:
                return await fn()
            except Exception as exc:
                last_exc = exc
                log.warning("rpc_error", method=method_name, attempt=attempt, reason=str(exc))
                if attempt < settings.RPC_MAX_RETRIES:
                    self._failover()
                    await asyncio.sleep(0.5 * attempt)
        log.error("rpc_error", method=method_name, reason=str(last_exc), attempts=settings.RPC_MAX_RETRIES)
        raise last_exc

    async def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Call a solana-py AsyncClient method with retry + failover."""
        return await self._with_retry(
            method_name,
            lambda: getattr(self._clients[self._active_idx], method_name)(*args, **kwargs),
        )

    async def _raw_call(self, method: str, params: list) -> Any:
        """Call a JSON-RPC method directly (for methods solana-py does not expose)."""
        async def _post() -> Any:
            resp = await self._http.post(
                self._urls[self._active_idx],
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"RPC {method} error: {data['error']}")
            return data.get("result")

        return await self._with_retry(method, _post)

    # ---- Public RPC methods ----

    async def get_balance(self, pubkey: Pubkey) -> int:
        """Return lamport balance for a public key."""
        resp = await self._call("get_balance", pubkey)
        return resp.value

    async def get_latest_blockhash(self) -> Any:
        """Return the latest blockhash response."""
        return await self._call("get_latest_blockhash")

    async def get_priority_fee_estimate(self, account_keys: list[str], priority_level: str) -> float:
        """Helius getPriorityFeeEstimate → microlamports per compute unit."""
        result = await self._raw_call(
            "getPriorityFeeEstimate",
            [{"accountKeys": account_keys, "options": {"priorityLevel": priority_level}}],
        )
        return float((result or {}).get("priorityFeeEstimate") or 0)

    async def get_multiple_accounts(self, pubkeys: list[str]) -> list[str | None]:
        """Base64 account data for up to 100 accounts (None where the account does not exist)."""
        result = await self._raw_call(
            "getMultipleAccounts",
            [pubkeys, {"encoding": "base64", "commitment": "processed"}],
        )
        return [acc["data"][0] if acc else None for acc in (result or {}).get("value", [])]

    async def get_transaction(self, signature: str) -> dict | None:
        """Return a confirmed transaction (jsonParsed), or None if not yet indexed."""
        return await self._raw_call(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}],
        )

    async def send_raw_transaction(self, raw_tx: bytes) -> Signature:
        """Submit a signed raw transaction. Returns the transaction signature."""
        resp = await self._call("send_raw_transaction", raw_tx)
        log.info("tx_submitted", signature=str(resp.value))
        return resp.value

    async def confirm_transaction(
        self,
        signature: Signature,
        max_wait_secs: float = 60.0,
    ) -> bool:
        """
        Poll for transaction confirmation up to max_wait_secs.
        Returns True if confirmed, False on timeout or on-chain failure.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_wait_secs
        while loop.time() < deadline:
            try:
                resp = await self._call("get_signature_statuses", [signature])
                status = resp.value[0] if resp.value else None
                if status is not None:
                    if status.err:
                        log.error("tx_result", result="failed", signature=str(signature), error=str(status.err))
                        return False
                    if status.confirmation_status in _CONFIRMED_STATUSES:
                        log.info("tx_result", result="confirmed", signature=str(signature))
                        return True
            except Exception as exc:
                log.warning("rpc_error", reason=str(exc), context="confirm_transaction")

            await asyncio.sleep(1.0)

        log.error("tx_result", result="timeout", signature=str(signature), waited_secs=max_wait_secs)
        return False

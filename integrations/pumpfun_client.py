"""
integrations/pumpfun_client.py
================================
PumpPortal Method 2 — Local Transaction API Integration.

Flow (contract §1.3 — atomic createAndBuy):
  1. Upload token image + metadata to Pinata (IPFS)          → metadata URI
  2. POST to PumpPortal /api/trade-local                      → unsigned tx bytes
  3. Sign locally (sign_transaction)                          → signed tx bytes
  4. Submit signed tx to Solana RPC (caller)

Why Method 2 (not Method 1):
  - Private keys never leave the VPS.
  - PumpPortal only sees the PUBLIC key and token parameters.

IPFS Upload:
  Primary:  Pinata Cloud (PINATA_JWT)
  Fallback: PumpPortal's own /api/ipfs endpoint

References:
  https://pumpportal.fun/local-trading-api/trading-api/
  https://docs.pinata.cloud/quickstart
"""

from typing import Optional

import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from config import settings
from logging_config.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------
_PUMPPORTAL_BASE = "https://pumpportal.fun/api"
_PUMPPORTAL_IPFS_URL = f"{_PUMPPORTAL_BASE}/ipfs"
_PUMPPORTAL_TRADE_LOCAL_URL = f"{_PUMPPORTAL_BASE}/trade-local"

_PINATA_UPLOAD_URL = "https://api.pinata.cloud/pinning/pinFileToIPFS"
_PINATA_JSON_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
_IPFS_GATEWAY = "https://ipfs.io/ipfs"


# ---------------------------------------------------------------
# Signing
# ---------------------------------------------------------------
def sign_transaction(unsigned_tx_bytes: bytes, signers: list[Keypair]) -> bytes:
    """Sign a PumpPortal VersionedTransaction locally with all required keypairs."""
    tx = VersionedTransaction.from_bytes(unsigned_tx_bytes)
    return bytes(VersionedTransaction(tx.message, signers))


# ---------------------------------------------------------------
# Pinata IPFS helpers
# ---------------------------------------------------------------
async def _upload_image_to_pinata(
    client: httpx.AsyncClient,
    image_bytes: bytes,
    filename: str,
) -> str:
    """Upload the token image to Pinata and return its IPFS gateway URL."""
    resp = await client.post(
        _PINATA_UPLOAD_URL,
        headers={"Authorization": f"Bearer {settings.PINATA_JWT}"},
        files={"file": (filename, image_bytes, "image/png")},
        timeout=30.0,
    )
    resp.raise_for_status()
    ipfs_hash = resp.json()["IpfsHash"]
    url = f"{_IPFS_GATEWAY}/{ipfs_hash}"
    log.info("ipfs_image_uploaded", provider="pinata", ipfs_hash=ipfs_hash, url=url)
    return url


async def _upload_metadata_to_pinata(
    client: httpx.AsyncClient,
    name: str,
    symbol: str,
    description: str,
    image_url: str,
) -> str:
    """Upload the token metadata JSON to Pinata and return the metadata URI."""
    metadata = {
        "name": name,
        "symbol": symbol,
        "description": description,
        "image": image_url,
        "showName": True,
        "createdOn": "https://pump.fun",
    }
    resp = await client.post(
        _PINATA_JSON_URL,
        headers={
            "Authorization": f"Bearer {settings.PINATA_JWT}",
            "Content-Type": "application/json",
        },
        json={
            "pinataContent": metadata,
            "pinataMetadata": {"name": f"{symbol}_metadata"},
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    ipfs_hash = resp.json()["IpfsHash"]
    uri = f"{_IPFS_GATEWAY}/{ipfs_hash}"
    log.info("ipfs_metadata_uploaded", provider="pinata", ipfs_hash=ipfs_hash, uri=uri)
    return uri


async def _upload_via_pumpportal_ipfs(
    client: httpx.AsyncClient,
    name: str,
    symbol: str,
    description: str,
    image_bytes: bytes,
) -> str:
    """Fallback: upload image + metadata via PumpPortal's /api/ipfs endpoint."""
    log.info("ipfs_upload_fallback", provider="pumpportal")
    resp = await client.post(
        _PUMPPORTAL_IPFS_URL,
        files={
            "file": ("token_image.png", image_bytes, "image/png"),
            "name": (None, name),
            "symbol": (None, symbol),
            "description": (None, description),
            "showName": (None, "true"),
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    uri = resp.json().get("metadataUri", "")
    log.info("ipfs_metadata_uploaded", provider="pumpportal", uri=uri)
    return uri


# ---------------------------------------------------------------
# PumpPortal Local Transaction API
# ---------------------------------------------------------------
async def _post_trade_local(payload: dict) -> bytes:
    """POST to /api/trade-local and return the raw unsigned transaction bytes."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(_PUMPPORTAL_TRADE_LOCAL_URL, json=payload, timeout=10.0)

    if resp.status_code != 200:
        log.error("pumpportal_api_error", action=payload.get("action"), status=resp.status_code, body=resp.text[:300])
        resp.raise_for_status()

    log.info("pumpportal_tx_received", action=payload.get("action"), tx_size_bytes=len(resp.content))
    return resp.content


# ---------------------------------------------------------------
# Public client class
# ---------------------------------------------------------------
class PumpFunClient:
    """
    PumpPortal Method 2 — Local Transaction API Client.
    Callers sign the returned bytes with sign_transaction() and submit to RPC.
    """

    async def upload_token_metadata(
        self,
        token_name: str,
        token_ticker: str,
        description: str,
        image_bytes: Optional[bytes] = None,
    ) -> str:
        """Upload token image + metadata to IPFS and return the metadata URI."""
        if image_bytes is None:
            image_bytes = _minimal_placeholder_image()

        async with httpx.AsyncClient() as client:
            if settings.PINATA_JWT:
                image_url = await _upload_image_to_pinata(
                    client, image_bytes, f"{token_ticker.lower()}.png"
                )
                return await _upload_metadata_to_pinata(
                    client, token_name, token_ticker, description, image_url
                )
            return await _upload_via_pumpportal_ipfs(
                client, token_name, token_ticker, description, image_bytes
            )

    async def get_unsigned_create_tx(
        self,
        creator_pubkey: str,
        token_name: str,
        token_ticker: str,
        metadata_uri: str,
        mint_public_key: str,
        initial_buy_sol: float,
        priority_fee_sol: float,
        slippage_pct: int,
    ) -> bytes:
        """Unsigned atomic createAndBuy transaction (token creation + dev buy in one tx)."""
        log.info(
            "pumpportal_tx_request",
            action="create",
            token_ticker=token_ticker,
            mint=mint_public_key,
            initial_buy_sol=initial_buy_sol,
            priority_fee_sol=priority_fee_sol,
        )
        return await _post_trade_local({
            "publicKey": creator_pubkey,
            "action": "create",
            "tokenMetadata": {
                "name": token_name,
                "symbol": token_ticker,
                "uri": metadata_uri,
            },
            "mint": mint_public_key,
            "denominatedInSol": "true",
            "amount": initial_buy_sol,
            "slippage": slippage_pct,
            "priorityFee": priority_fee_sol,
            "pool": "pump",
        })

    async def get_unsigned_trade_tx(
        self,
        wallet_pubkey: str,
        token_mint: str,
        action: str,
        amount: float | str,
        slippage_pct: int,
        priority_fee_sol: float,
        denominated_in_sol: bool = True,
        pool: str = "pump",
    ) -> bytes:
        """
        Unsigned buy or sell transaction.

        Args:
            action:             'buy' or 'sell'
            amount:             SOL amount (buy) or token amount / percentage like '100%' (sell)
            denominated_in_sol: False when amount is tokens or a percentage
            pool:               'pump' for bonding curve, 'auto' to follow the token after migration
        """
        log.info(
            "pumpportal_trade_request",
            action=action,
            mint=token_mint[:12] + "...",
            amount=amount,
            wallet=wallet_pubkey[:12] + "...",
            pool=pool,
        )
        return await _post_trade_local({
            "publicKey": wallet_pubkey,
            "action": action,
            "mint": token_mint,
            "denominatedInSol": "true" if denominated_in_sol else "false",
            "amount": amount,
            "slippage": slippage_pct,
            "priorityFee": priority_fee_sol,
            "pool": pool,
        })


# ---------------------------------------------------------------
# Utility
# ---------------------------------------------------------------
def _minimal_placeholder_image() -> bytes:
    """Minimal valid PNG (1x1 red pixel) used when no token image is provided."""
    return bytes([
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
        0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
        0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
        0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,
        0x54, 0x08, 0xD7, 0x63, 0xF8, 0xCF, 0xC0, 0x00,
        0x00, 0x00, 0x02, 0x00, 0x01, 0xE2, 0x21, 0xBC,
        0x33, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,
        0x44, 0xAE, 0x42, 0x60, 0x82,
    ])

"""
execution/priority_fee.py
==========================
Dynamic Solana priority fee calculator via Helius RPC.

Asks Helius' getPriorityFeeEstimate for the current fee level on the
Pump.fun program and converts it into the total priority fee (in SOL)
that PumpPortal's trade-local API expects.

KPI requirement:
  Fee must not exceed MAX_PRIORITY_FEE_MICROLAMPORTS per compute unit.
"""

from execution.rpc_client import SolanaRPCClient
from config import settings
from logging_config.logger import get_logger

log = get_logger(__name__)

_PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Compute units budgeted for a Pump.fun create / trade transaction
_COMPUTE_UNITS = 200_000


async def calculate_priority_fee_sol(rpc: SolanaRPCClient) -> float:
    """
    Return the recommended total priority fee in SOL.
    Returns 0.0 if the estimate is unavailable.
    """
    try:
        estimate = await rpc.get_priority_fee_estimate([_PUMPFUN_PROGRAM_ID], settings.PRIORITY_FEE_LEVEL)
        microlamports_per_cu = min(estimate, settings.MAX_PRIORITY_FEE_MICROLAMPORTS)
        fee_sol = microlamports_per_cu * _COMPUTE_UNITS / 1_000_000 / 1_000_000_000
        log.info(
            "priority_fee",
            level=settings.PRIORITY_FEE_LEVEL,
            estimate_microlamports_per_cu=estimate,
            fee_sol=fee_sol,
        )
        return fee_sol
    except Exception as exc:
        log.error("priority_fee_error", reason=str(exc))
        return 0.0

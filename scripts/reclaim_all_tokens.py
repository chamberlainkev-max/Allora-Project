"""
scripts/reclaim_all_tokens.py
==============================
Batch reclaim script: Sells test tokens back to the Pump.fun bonding curve
and reclaims account rent directly into Wallet 1 (Epapft2E7RwaCgm877e9rEXPudFohMBQ3tGGyCgqxVyg).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import base64
import httpx
from solders.keypair import Keypair
from execution.rpc_client import SolanaRPCClient
from execution.tx_verify import TxExpectation
from integrations.pumpfun_client import PumpFunClient, sign_transaction_async
from config import settings

_LYC_MINT = "HJjWmTp5bR4YYKpCtVW9nJ4qmoL9AgqL6JnNJEYW9nBm"

async def reclaim(sell_lyc: bool = False):
    wallet_kp = Keypair.from_base58_string(settings.PUMPPORTAL_WALLET_PRIVATE_KEY)
    wallet_pubkey = str(wallet_kp.pubkey())
    rpc_url = settings.SOLANA_RPC_URL
    client = PumpFunClient()

    print(f"[*] Starting Reclaim for Wallet: {wallet_pubkey}")
    
    # 1. Fetch all Token-2022 accounts owned by Wallet 1
    async with httpx.AsyncClient(timeout=20) as http_client:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                wallet_pubkey,
                {"programId": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"},
                {"encoding": "jsonParsed"}
            ]
        }
        res = await http_client.post(rpc_url, json=payload)
        accounts = res.json().get("result", {}).get("value", [])

    print(f"[*] Found {len(accounts)} token accounts in Wallet 1.")

    success_count = 0
    fail_count = 0

    for idx, acc in enumerate(accounts, 1):
        info = acc["account"]["data"]["parsed"]["info"]
        mint = info["mint"]
        amount = float(info["tokenAmount"]["uiAmount"] or 0)

        if amount <= 0:
            print(f"[{idx}/{len(accounts)}] Skipping empty account for mint: {mint[:8]}...")
            continue

        if mint == _LYC_MINT and not sell_lyc:
            print(f"[{idx}/{len(accounts)}] Preserving official $LYC token ({mint[:8]}...).")
            continue

        print(f"[{idx}/{len(accounts)}] Selling 100% of {mint[:8]}... (Holding: {amount:,.0f} tokens)")
        
        try:
            # Get unsigned sell tx from PumpPortal
            tx_bytes = await client.get_unsigned_trade_tx(
                wallet_pubkey=wallet_pubkey,
                token_mint=mint,
                action="sell",
                amount="100%",
                slippage_pct=30,
                priority_fee_sol=0.0001,
                denominated_in_sol=False,
                pool="pump"
            )
            async with SolanaRPCClient() as rpc:
                signed_tx = await sign_transaction_async(
                    tx_bytes,
                    [wallet_kp],
                    TxExpectation(wallet_pubkey=wallet_pubkey, action="sell", mint=mint),
                    rpc,
                )

            # Broadcast tx to Solana RPC
            async with httpx.AsyncClient(timeout=15) as http_client:
                tx_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        base64.b64encode(signed_tx).decode("ascii"),
                        {"encoding": "base64", "preflightCommitment": "confirmed"}
                    ]
                }
                send_res = await http_client.post(rpc_url, json=tx_payload)
                result = send_res.json()
                if "result" in result:
                    sig = result["result"]
                    print(f"    -> SUCCESS! Tx: {sig[:20]}...")
                    success_count += 1
                else:
                    print(f"    -> RPC Error: {result.get('error')}")
                    fail_count += 1

            # Small delay between sells to avoid rate limits
            await asyncio.sleep(1.5)

        except Exception as exc:
            print(f"    -> Failed: {exc}")
            fail_count += 1

    # Check final SOL balance
    async with httpx.AsyncClient(timeout=10) as http_client:
        bal_res = await http_client.post(rpc_url, json={"jsonrpc":"2.0","id":1,"method":"getBalance","params":[wallet_pubkey]})
        final_lamports = bal_res.json().get("result", {}).get("value", 0)
        final_sol = final_lamports / 1e9

    print("========================================")
    print(f"Reclaim Complete!")
    print(f"Successfully Sold: {success_count} tokens")
    print(f"Failed: {fail_count}")
    print(f"Current Wallet 1 SOL Balance: {final_sol:.5f} SOL")
    print("========================================")

if __name__ == "__main__":
    import sys
    sell_lyc_flag = "--include-lyc" in sys.argv
    asyncio.run(reclaim(sell_lyc=sell_lyc_flag))

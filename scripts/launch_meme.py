"""
scripts/launch_meme.py
======================
Launch a provided meme token on Pump.fun from the Elora wallet (Wallet 1,
PUMPPORTAL_WALLET_PRIVATE_KEY) via PumpPortal Method 2.

Usage:
  python scripts/launch_meme.py --name "Token Name" --symbol TICKER \
      --description "..." --image path/to/image.png [--buy 0.02]
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import asyncio
import io

from PIL import Image
from solders.keypair import Keypair

from config import settings
from execution.priority_fee import calculate_priority_fee_sol
from execution.rpc_client import SolanaRPCClient
from integrations.discord_client import DiscordClient
from integrations.pumpfun_client import PumpFunClient, sign_transaction
from integrations.telegram_client import TelegramClient

# Pump.fun creation costs ~0.02 SOL on top of the initial buy and network fees
_CREATE_COST_SOL = 0.025


async def launch(name: str, symbol: str, description: str, image_path: str, buy_sol: float) -> None:
    wallet_kp = Keypair.from_base58_string(settings.PUMPPORTAL_WALLET_PRIVATE_KEY)
    print(f"Creator wallet: {wallet_kp.pubkey()}")

    async with SolanaRPCClient() as rpc:
        bal_sol = await rpc.get_balance(wallet_kp.pubkey()) / 1e9
        need_sol = buy_sol + _CREATE_COST_SOL
        print(f"Balance: {bal_sol:.5f} SOL (need ~{need_sol:.3f})")
        if bal_sol < need_sol:
            print("ERROR: insufficient balance.")
            return

        with Image.open(image_path) as im:
            buf = io.BytesIO()
            im.save(buf, format="PNG")

        pf = PumpFunClient()
        metadata_uri = await pf.upload_token_metadata(
            token_name=name,
            token_ticker=symbol,
            description=description,
            image_bytes=buf.getvalue(),
        )
        print(f"Metadata URI: {metadata_uri}")

        mint_kp = Keypair()
        unsigned_tx = await pf.get_unsigned_create_tx(
            creator_pubkey=str(wallet_kp.pubkey()),
            token_name=name,
            token_ticker=symbol,
            metadata_uri=metadata_uri,
            mint_public_key=str(mint_kp.pubkey()),
            initial_buy_sol=buy_sol,
            priority_fee_sol=await calculate_priority_fee_sol(rpc),
            slippage_pct=settings.LAUNCH_SLIPPAGE_PCT,
        )
        signature = await rpc.send_raw_transaction(sign_transaction(unsigned_tx, [wallet_kp, mint_kp]))
        print(f"Submitted: {signature}")

        if not await rpc.confirm_transaction(signature):
            print("Transaction was not confirmed.")
            return

    mint = str(mint_kp.pubkey())
    print(f"LIVE: https://pump.fun/coin/{mint}")
    print(f"Mint: {mint}")
    print(f"Solscan: https://solscan.io/tx/{signature}")

    telegram = TelegramClient()
    await telegram.connect()
    await telegram.send_launch_alert(name, symbol, mint, str(signature))
    discord_client = DiscordClient()
    await discord_client.connect()
    await discord_client.send_launch_alert(name, symbol, mint, str(signature))
    await discord_client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch a provided meme token on Pump.fun")
    parser.add_argument("--name", required=True, help="Token name")
    parser.add_argument("--symbol", required=True, help="Token ticker")
    parser.add_argument("--description", required=True, help="Token description")
    parser.add_argument("--image", required=True, help="Path to the token image")
    parser.add_argument("--buy", type=float, default=settings.INITIAL_BUY_SOL, help="Initial buy in SOL")
    args = parser.parse_args()
    asyncio.run(launch(args.name, args.symbol, args.description, args.image, args.buy))


if __name__ == "__main__":
    main()

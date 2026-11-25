"""Trade pool wallet vault + tier on/off."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution import trade_pool as tp


@pytest.fixture()
def vault_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wallets = tmp_path / "trade_pool_wallets.json"
    state = tmp_path / "trade_pool_state.json"
    monkeypatch.setattr(tp.settings, "TRADE_POOL_WALLETS_FILE", str(wallets))
    monkeypatch.setattr(tp.settings, "TRADE_POOL_STATE_FILE", str(state))
    monkeypatch.setattr(tp.settings, "TRADE_POOL_SOL_PER_WALLET", 0.12)
    return tmp_path


def test_generate_and_tier_slice(vault_dir: Path):
    wallets = tp.generate_wallets(100)
    assert len(wallets) == 100
    path = tp.save_wallets(wallets)
    assert path.exists()
    loaded = tp.load_wallets()
    assert len(loaded) == 100
    assert loaded[0].pubkey == wallets[0].pubkey
    # round-trip keypair
    assert str(loaded[0].keypair().pubkey()) == loaded[0].pubkey

    for tier in tp.TIERS:
        slice_ = tp.wallets_for_tier(tier, loaded)
        assert len(slice_) == tier
        assert slice_[0].index == 0
        assert slice_[-1].index == tier - 1


def test_tier_armed_requires_state_and_env(vault_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADE_POOL_TIER_10_ENABLED", "false")
    tp.save_wallets(tp.generate_wallets(10))
    assert tp.is_tier_enabled(10) is False
    tp.set_tier_enabled(10, True)
    assert tp.is_tier_enabled(10) is False  # env still false
    monkeypatch.setenv("TRADE_POOL_TIER_10_ENABLED", "true")
    assert tp.is_tier_enabled(10) is True
    tp.set_tier_enabled(10, False)
    assert tp.is_tier_enabled(10) is False


def test_working_capital(vault_dir: Path):
    assert tp.working_capital_sol(10) == pytest.approx(1.2)
    assert tp.working_capital_sol(20) == pytest.approx(2.4)


def test_pubkey_inventory_has_no_secrets(vault_dir: Path):
    tp.save_wallets(tp.generate_wallets(5))
    inv = json.loads(tp.inventory_path().read_text(encoding="utf-8"))
    blob = json.dumps(inv)
    assert "secret" not in blob.lower()
    assert inv["count"] == 5

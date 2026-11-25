"""
tests/test_atomic_json.py
=========================
F03 — crash-safe JSON write/load (position file recovery).
"""

import json
import os

from execution.atomic_json import load_json_dict, write_json_atomic


class TestAtomicJsonF03:
    def test_write_and_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "positions.json")
        write_json_atomic(path, {"mint1": {"symbol": "AAA", "tokens": 1}})
        assert load_json_dict(path) == {"mint1": {"symbol": "AAA", "tokens": 1}}
        assert os.path.exists(path)

    def test_bak_created_on_overwrite(self, tmp_path):
        path = str(tmp_path / "positions.json")
        write_json_atomic(path, {"v": 1})
        write_json_atomic(path, {"v": 2})
        assert load_json_dict(path) == {"v": 2}
        with open(f"{path}.bak", encoding="utf-8") as fh:
            assert json.load(fh) == {"v": 1}

    def test_recovers_from_bak_when_primary_corrupt(self, tmp_path):
        path = str(tmp_path / "positions.json")
        write_json_atomic(path, {"mint": {"symbol": "SAFE"}})
        write_json_atomic(path, {"mint": {"symbol": "NEW"}})
        # Simulate crash window: primary truncated / corrupt, bak still good.
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not-json")
        recovered = load_json_dict(path)
        assert recovered == {"mint": {"symbol": "SAFE"}}

    def test_missing_files_return_empty_dict(self, tmp_path):
        path = str(tmp_path / "missing.json")
        assert load_json_dict(path) == {}

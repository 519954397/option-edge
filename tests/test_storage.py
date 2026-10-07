from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jin10_research.storage import ResearchStore


def bundle(now, price=80.0, trend=True):
    base = int(now.timestamp()) - 99 * 60
    bars = [
        {
            "time": base + index * 60,
            "open": 70 + index * 0.05,
            "high": 70.2 + index * 0.05,
            "low": 69.8 + index * 0.05,
            "close": 70.1 + index * 0.05,
            "volume": 100,
        }
        for index in range(100)
    ] if trend else []
    return {
        "generated_at": now.isoformat(),
        "cutoff": (now - timedelta(hours=12)).isoformat(),
        "hours": 12,
        "flashes": [],
        "oil_flashes": [],
        "news": [],
        "quotes": {
            "USOIL": {"code": "USOIL", "close": str(price), "time": now.isoformat()},
            "UKOIL": {"code": "UKOIL", "close": str(price + 5), "time": now.isoformat()},
        },
        "klines": {"USOIL": bars, "UKOIL": bars},
        "calendar": [],
    }


class StorageTests(unittest.TestCase):
    def test_run_persistence_and_statistics(self):
        now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            with ResearchStore(Path(directory) / "test.sqlite3") as store:
                run_id = store.record_run(bundle(now), Path(directory) / "report.md")
                self.assertEqual(run_id, 1)
                stats = store.statistics()
                self.assertEqual(stats["runs"], 1)
                self.assertEqual(stats["kline_bars"], 200)
                self.assertEqual(stats["strategies"].get("pending"), 1)

                store.record_run(bundle(now + timedelta(hours=25), price=82), Path(directory) / "later.md")
                stats = store.statistics()
                self.assertEqual(stats["completed"], 1)
                self.assertEqual(stats["wins"], 1)


if __name__ == "__main__":
    unittest.main()

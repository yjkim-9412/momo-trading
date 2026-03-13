import pytest

import realtime.stream_manager as stream_module
from realtime.stream_manager import StreamManager


class DummyWebSocket:
    def __init__(self):
        self.subscribed: set[tuple[str, str]] = set()
        self.is_connected = True

    @property
    def subscription_count(self) -> int:
        return len(self.subscribed)

    async def subscribe(self, symbol: str, market: str = "KRX") -> bool:
        self.subscribed.add((market, symbol.upper()))
        return True

    async def unsubscribe(self, symbol: str, market: str = "KRX") -> None:
        self.subscribed.discard((market, symbol.upper()))

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def listen(self) -> None:
        return None


@pytest.mark.asyncio
async def test_replace_market_subscriptions_keeps_other_scope(monkeypatch):
    dummy_ws = DummyWebSocket()
    monkeypatch.setattr(stream_module, "kis_websocket", dummy_ws)
    manager = StreamManager()

    await manager.replace_market_subscriptions("KRX", [("005930", "KRX"), ("000660", "KRX")])
    await manager.replace_market_subscriptions("US", [("AAPL", "NASDAQ")])

    assert dummy_ws.subscribed == {
        ("KRX", "005930"),
        ("KRX", "000660"),
        ("NASDAQ", "AAPL"),
    }

    await manager.replace_market_subscriptions("US", [("MSFT", "NASDAQ")])

    assert dummy_ws.subscribed == {
        ("KRX", "005930"),
        ("KRX", "000660"),
        ("NASDAQ", "MSFT"),
    }


@pytest.mark.asyncio
async def test_ensure_symbol_adds_to_scope_without_reset(monkeypatch):
    dummy_ws = DummyWebSocket()
    monkeypatch.setattr(stream_module, "kis_websocket", dummy_ws)
    manager = StreamManager()

    await manager.replace_market_subscriptions("KRX", [("005930", "KRX")])
    await manager.ensure_symbol("KRX", "000660", "KRX")

    assert dummy_ws.subscribed == {
        ("KRX", "005930"),
        ("KRX", "000660"),
    }

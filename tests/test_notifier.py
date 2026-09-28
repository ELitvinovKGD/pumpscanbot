import unittest

from app.db import Wallet
from app.detector import ActivityEvent
from app.market_data import TokenQuote, USDC_MINT
from app.notifier import format_activity_event


class NotifierTest(unittest.TestCase):
    def test_formats_swap_with_symbols_and_stablecoin_value(self):
        me_mint = "MEFNBXixkEbait3xn9bkm8WsJzXtVsaJEn4c8Sam21u"
        wallet = Wallet(1, "GJvewfRjqTUPtx6WsBSUnaFbdgXwgXnWfpDyLm65T4YA", "test", True, None, 0)
        event = ActivityEvent(
            wallet=wallet,
            signature="signature",
            event_type="SWAP",
            source="JUPITER",
            description="long technical description",
            sol_change=-0.000010565,
            token_changes=((USDC_MINT, 133.259082), (me_mint, -1778.9)),
            timestamp=None,
        )
        quotes = {
            USDC_MINT: TokenQuote("USDC", 1.0),
            me_mint: TokenQuote("ME", None),
        }

        text = format_activity_event(event, quotes)

        self.assertIn("1,778.9", text)
        self.assertIn("ME</a>", text)
        self.assertIn("133.259082", text)
        self.assertIn("USDC</a>", text)
        self.assertIn("$133.26", text)
        self.assertNotIn("-0.000010565", text)
        self.assertNotIn("long technical description", text)


if __name__ == "__main__":
    unittest.main()

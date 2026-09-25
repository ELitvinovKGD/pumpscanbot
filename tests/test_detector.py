import unittest

from app.db import Wallet
from app.detector import detect_activity_events, detect_buy_events, detect_sell_events


class DetectorTest(unittest.TestCase):
    def test_detects_generic_token_swap_once_for_wallet(self):
        wallet = Wallet(1, "Wallet1111111111111111111111111111111111", "Test", True, None, 0)
        tx = {
            "signature": "swap-sig",
            "type": "SWAP",
            "source": "JUPITER",
            "description": "Wallet swapped JTO for USDC",
            "accountData": [
                {
                    "account": "token-account-1",
                    "tokenBalanceChanges": [
                        {
                            "userAccount": wallet.address,
                            "mint": "JTO-mint",
                            "rawTokenAmount": {"tokenAmount": "-234000000", "decimals": 6},
                        }
                    ],
                },
                {
                    "account": "token-account-2",
                    "tokenBalanceChanges": [
                        {
                            "userAccount": wallet.address,
                            "mint": "USDC-mint",
                            "rawTokenAmount": {"tokenAmount": "133403213", "decimals": 6},
                        }
                    ],
                },
            ],
        }

        events = detect_activity_events(tx, [wallet])

        self.assertEqual(len(events), 1)
        self.assertEqual(dict(events[0].token_changes)["JTO-mint"], -234)
        self.assertAlmostEqual(dict(events[0].token_changes)["USDC-mint"], 133.403213)

    def test_detects_sol_transfer(self):
        wallet = Wallet(1, "Wallet1111111111111111111111111111111111", "Test", True, None, 0)
        tx = {
            "signature": "sol-sig",
            "type": "TRANSFER",
            "source": "SYSTEM_PROGRAM",
            "accountData": [
                {"account": wallet.address, "nativeBalanceChange": -500005000}
            ],
        }

        events = detect_activity_events(tx, [wallet])

        self.assertEqual(len(events), 1)
        self.assertAlmostEqual(events[0].sol_change, -0.500005)

    def test_ignores_unrelated_activity(self):
        wallet = Wallet(1, "Wallet1111111111111111111111111111111111", "Test", True, None, 0)
        tx = {"signature": "other-sig", "description": "AnotherWallet transferred SOL"}

        self.assertEqual(detect_activity_events(tx, [wallet]), [])

    def test_detects_pump_buy_above_threshold(self):
        wallet = Wallet(
            id=1,
            address="7xK11111111111111111111111111111111111111",
            label="Alpha",
            enabled=True,
            min_sol=None,
            created_at=0,
        )
        tx = {
            "signature": "sig1",
            "source": "PUMP_FUN",
            "timestamp": 1,
            "nativeTransfers": [
                {
                    "fromUserAccount": wallet.address,
                    "toUserAccount": "pool",
                    "amount": 250_000_000,
                }
            ],
            "tokenTransfers": [
                {
                    "fromUserAccount": "pool",
                    "toUserAccount": wallet.address,
                    "mint": "Mint1111111111111111111111111111111111111",
                    "tokenAmount": 12345,
                }
            ],
        }

        events = detect_buy_events(tx, [wallet], global_min_sol=0.1)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].sol_spent, 0.25)
        self.assertEqual(events[0].token_amount, 12345)

    def test_ignores_buy_below_threshold(self):
        wallet = Wallet(
            id=1,
            address="7xK11111111111111111111111111111111111111",
            label="Alpha",
            enabled=True,
            min_sol=1.0,
            created_at=0,
        )
        tx = {
            "signature": "sig2",
            "source": "PUMP_FUN",
            "nativeTransfers": [
                {
                    "fromUserAccount": wallet.address,
                    "toUserAccount": "pool",
                    "amount": 250_000_000,
                }
            ],
            "tokenTransfers": [
                {
                    "fromUserAccount": "pool",
                    "toUserAccount": wallet.address,
                    "mint": "Mint1111111111111111111111111111111111111",
                    "tokenAmount": 12345,
                }
            ],
        }

        self.assertEqual(detect_buy_events(tx, [wallet], global_min_sol=0.1), [])

    def test_detects_pump_sell_above_threshold(self):
        wallet = Wallet(
            id=1,
            address="7xK11111111111111111111111111111111111111",
            label="Red",
            enabled=True,
            min_sol=None,
            created_at=0,
        )
        tx = {
            "signature": "sig3",
            "source": "PUMP_SWAP",
            "timestamp": 1,
            "nativeTransfers": [
                {
                    "fromUserAccount": "pool",
                    "toUserAccount": wallet.address,
                    "amount": 350_000_000,
                }
            ],
            "tokenTransfers": [
                {
                    "fromUserAccount": wallet.address,
                    "toUserAccount": "pool",
                    "mint": "Mint1111111111111111111111111111111111111",
                    "tokenAmount": 5000,
                }
            ],
        }

        events = detect_sell_events(tx, [wallet], global_min_sol=0.1)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].sol_received, 0.35)
        self.assertEqual(events[0].token_amount, 5000)

    def test_ignores_sell_below_threshold(self):
        wallet = Wallet(
            id=1,
            address="7xK11111111111111111111111111111111111111",
            label="Red",
            enabled=True,
            min_sol=None,
            created_at=0,
        )
        tx = {
            "signature": "sig4",
            "source": "PUMP_SWAP",
            "nativeTransfers": [
                {
                    "fromUserAccount": "pool",
                    "toUserAccount": wallet.address,
                    "amount": 50_000_000,
                }
            ],
            "tokenTransfers": [
                {
                    "fromUserAccount": wallet.address,
                    "toUserAccount": "pool",
                    "mint": "Mint1111111111111111111111111111111111111",
                    "tokenAmount": 5000,
                }
            ],
        }

        self.assertEqual(detect_sell_events(tx, [wallet], global_min_sol=0.1), [])

    def test_detects_buy_from_account_data_fallback(self):
        wallet = Wallet(
            id=1,
            address="7GeX8WNjNGivUpSZeiiETHQrSCzuopqui97LB1qnrEaR",
            label="Red",
            enabled=True,
            min_sol=None,
            created_at=0,
        )
        tx = {
            "signature": "sig5",
            "source": "PUMP_SWAP",
            "accountData": [
                {
                    "account": wallet.address,
                    "nativeBalanceChange": -1_121_000_000,
                    "tokenBalanceChanges": [
                        {
                            "userAccount": wallet.address,
                            "mint": "Mint1111111111111111111111111111111111111",
                            "rawTokenAmount": {"tokenAmount": "5200000000000", "decimals": 6},
                        }
                    ],
                }
            ],
        }

        events = detect_buy_events(tx, [wallet], global_min_sol=0.05)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].sol_spent, 1.121)
        self.assertEqual(events[0].token_amount, 5_200_000)

    def test_detects_sell_from_account_data_fallback(self):
        wallet = Wallet(
            id=1,
            address="7GeX8WNjNGivUpSZeiiETHQrSCzuopqui97LB1qnrEaR",
            label="Red",
            enabled=True,
            min_sol=None,
            created_at=0,
        )
        tx = {
            "signature": "sig6",
            "source": "PUMP_SWAP",
            "accountData": [
                {
                    "account": wallet.address,
                    "nativeBalanceChange": 1_371_000_000,
                    "tokenBalanceChanges": [
                        {
                            "userAccount": wallet.address,
                            "mint": "Mint1111111111111111111111111111111111111",
                            "rawTokenAmount": {"tokenAmount": "-10700000000000", "decimals": 6},
                        }
                    ],
                }
            ],
        }

        events = detect_sell_events(tx, [wallet], global_min_sol=0.05)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].sol_received, 1.371)
        self.assertEqual(events[0].token_amount, 10_700_000)

    def test_detects_pump_mint_even_when_source_is_generic_swap(self):
        wallet = Wallet(
            id=1,
            address="7GeX8WNjNGivUpSZeiiETHQrSCzuopqui97LB1qnrEaR",
            label="Red",
            enabled=True,
            min_sol=None,
            created_at=0,
        )
        tx = {
            "signature": "sig7",
            "source": "UNKNOWN",
            "type": "SWAP",
            "nativeTransfers": [
                {
                    "fromUserAccount": wallet.address,
                    "toUserAccount": "pool",
                    "amount": 100_000_000,
                }
            ],
            "tokenTransfers": [
                {
                    "fromUserAccount": "pool",
                    "toUserAccount": wallet.address,
                    "mint": "ABC111111111111111111111111111111111pump",
                    "tokenAmount": 1_000_000,
                }
            ],
        }

        events = detect_buy_events(tx, [wallet], global_min_sol=0.05)

        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()

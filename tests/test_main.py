import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import main


def timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class FindPowerOffRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 21, 6, 5, tzinfo=main.TZ_BEIJING)

    def test_finds_record_in_cross_midnight_window(self) -> None:
        record = {
            "endtype": "39",
            "enddt": timestamp_ms(
                datetime(2026, 7, 21, 0, 5, tzinfo=main.TZ_BEIJING)
            ),
            "devaddress": "device",
            "devport": "11",
        }
        self.assertIs(main.find_power_off_record([record], now=self.now), record)

    def test_rejects_record_from_previous_day(self) -> None:
        record = {
            "endtype": 39,
            "enddt": timestamp_ms(
                datetime(2026, 7, 20, 0, 5, tzinfo=main.TZ_BEIJING)
            ),
        }
        self.assertIsNone(main.find_power_off_record([record], now=self.now))

    def test_selects_latest_matching_record(self) -> None:
        older = {
            "endtype": 39,
            "enddt": timestamp_ms(
                datetime(2026, 7, 20, 23, 55, tzinfo=main.TZ_BEIJING)
            ),
        }
        newer = {
            "endtype": 39,
            "enddt": timestamp_ms(
                datetime(2026, 7, 21, 0, 10, tzinfo=main.TZ_BEIJING)
            ),
        }
        self.assertIs(
            main.find_power_off_record([older, newer], now=self.now),
            newer,
        )


class PortStatusTests(unittest.TestCase):
    def test_port_index_is_zero_based(self) -> None:
        self.assertTrue(main.is_port_free("111011", "3"))
        self.assertFalse(main.is_port_free("111011", "2"))
        self.assertFalse(main.is_port_free("000", "3"))


class ChargeParameterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = {
            "measure": 1,
            "ycTime": 480,
            "devtypeid": 40,
            "safeCharge": 9,
            "efee": 110,
            "eCharge": 55,
            "serviceCharge": 55,
        }

    def test_builds_current_meterage_protocol(self) -> None:
        params, duration = main.build_charge_params(
            "device",
            "11",
            699,
            self.device,
        )
        self.assertEqual(params["money"], 7)
        self.assertEqual(params["yuan7"], 0)
        self.assertEqual(params["beforemoney"], 699)
        self.assertEqual(params["fullStop"], 0)
        self.assertEqual(params["safeOpen"], 0)
        self.assertEqual(duration, 480)

    def test_uses_configured_shorter_cap(self) -> None:
        with patch.object(main, "MAX_CHARGE_TIME", 240):
            params, duration = main.build_charge_params(
                "device",
                "1",
                500,
                self.device,
            )
        self.assertEqual(params["yuan7"], 240)
        self.assertEqual(duration, 240)

    def test_rejects_unverified_charge_mode(self) -> None:
        unsupported = {**self.device, "measure": 2}
        with self.assertRaises(main.UnsupportedChargeMode):
            main.build_charge_params("device", "1", 500, unsupported)


class PublicLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_does_not_log_balance_device_or_port(self) -> None:
        now = datetime.now(main.TZ_BEIJING)
        record = {
            "endtype": 39,
            "enddt": timestamp_ms(now.replace(hour=0, minute=0, second=0)),
            "devaddress": "sensitive-device-123",
            "devport": "1",
        }
        device = {
            "measure": 1,
            "ycTime": 480,
            "devtypeid": 40,
            "safeCharge": 0,
            "efee": 100,
            "eCharge": 50,
            "serviceCharge": 50,
            "portstatur": "00",
        }
        messages: list[str] = []

        with (
            patch.object(main, "DRY_RUN", True),
            patch.object(main, "log", side_effect=messages.append),
            patch.object(
                main,
                "get_user_info",
                AsyncMock(
                    return_value={"employeeid": 123456, "readyaccountmoney": 1234}
                ),
            ),
            patch.object(main, "get_charge_log", AsyncMock(return_value=[record])),
            patch.object(main, "get_device_info", AsyncMock(return_value=device)),
        ):
            result, message = await main.try_charge(object())

        combined = "\n".join([*messages, message])
        self.assertEqual(result, main.ChargeResult.DRY_RUN)
        self.assertNotIn("12.34", combined)
        self.assertNotIn("sensitive-device-123", combined)
        self.assertNotIn("设备=sensitive-device-123", combined)
        self.assertNotIn("端口=1", combined)
        self.assertNotIn("端口状态: 00", combined)


if __name__ == "__main__":
    unittest.main()

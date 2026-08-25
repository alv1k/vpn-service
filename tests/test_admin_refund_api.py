import unittest
from unittest.mock import patch, MagicMock

from awg_api.main import _sessions
_sessions["test_session_token"] = 9999999999.0

from api.webhook import app
from fastapi.testclient import TestClient

client = TestClient(app, cookies={"connect.sid": "test_session_token"})

class TestAdminRefundEndpoint(unittest.TestCase):

    @patch("api.db.get_payment_by_id")
    def test_refund_not_found(self, mock_get_payment):
        mock_get_payment.return_value = None
        response = client.post("/api/admin/payments/nonexistent/refund")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Payment not found", response.json()["error"])

    @patch("api.db.get_payment_by_id")
    def test_refund_already_refunded(self, mock_get_payment):
        mock_get_payment.return_value = {"payment_id": "p1", "status": "refunded", "amount": 199}
        response = client.post("/api/admin/payments/p1/refund")
        self.assertEqual(response.status_code, 400)
        self.assertIn("already been refunded", response.json()["error"])

    @patch("api.db.get_payment_by_id")
    def test_refund_not_paid(self, mock_get_payment):
        mock_get_payment.return_value = {"payment_id": "p1", "status": "pending", "amount": 199}
        response = client.post("/api/admin/payments/p1/refund")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Cannot refund payment", response.json()["error"])

    @patch("config.YOO_KASSA_SECRET_KEY", "test_sec")
    @patch("config.YOO_KASSA_SHOP_ID", "test_shop")
    @patch("api.db.get_payment_by_id")
    @patch("yookassa.Refund.create")
    @patch("api.db.update_payment_status")
    @patch("api.webhook.process_refund")
    @patch("api.webhook.send_telegram_notification")
    def test_refund_success(self, mock_notify, mock_process_refund, mock_update_status, mock_yookassa_refund, mock_get_payment):
        mock_get_payment.return_value = {"payment_id": "p_paid", "status": "paid", "amount": 499, "tg_id": 12345}
        
        mock_res = MagicMock()
        mock_res.status = "succeeded"
        mock_res.id = "ref_123"
        mock_yookassa_refund.return_value = mock_res
        
        mock_process_refund.return_value = True

        response = client.post("/api/admin/payments/p_paid/refund")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["vpn_deactivated"])
        
        mock_update_status.assert_called_once_with("p_paid", "refunded")
        mock_process_refund.assert_called_once_with("p_paid")

if __name__ == "__main__":
    unittest.main()

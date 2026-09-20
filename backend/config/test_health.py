from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase, override_settings


@override_settings(
    ALLOWED_HOSTS=["testserver", "healthcheck.railway.app"],
    SECURE_SSL_REDIRECT=True,
    SECURE_REDIRECT_EXEMPT=[r"^health/$"],
)
class HealthTests(TestCase):
    def test_railway_probe_works_over_http_without_login(self):
        response = self.client.get("/health/", HTTP_HOST="healthcheck.railway.app")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-store", response["Cache-Control"])

    def test_database_failure_returns_503_without_error_details(self):
        with patch("config.health.connection.cursor", side_effect=OperationalError("private-database-details")):
            response = self.client.get("/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "private-database-details", status_code=503)

    def test_application_still_redirects_to_https(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://testserver/login/")

    def test_health_endpoint_does_not_accept_post(self):
        self.assertEqual(self.client.post("/health/").status_code, 405)

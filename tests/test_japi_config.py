"""Tests that do not require a running jAPI service."""
import unittest
from app.http_services import validate_endpoint

class JapiConfigTests(unittest.TestCase):
    def test_relative_endpoint(self):
        self.assertEqual(validate_endpoint('/v1/mdserver/sessions'), '/v1/mdserver/sessions')

    def test_absolute_and_external_urls_rejected(self):
        for bad in ('http://127.0.0.1:8088/v1/sql/ping',
                    'https://example.com/v1/a', '/health', '/v1/../secret',
                    '/v1/%2e%2e/secret', '/v1/a?x=y'):
            with self.subTest(endpoint=bad), self.assertRaises(ValueError):
                validate_endpoint(bad)
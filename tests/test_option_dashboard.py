import base64
import unittest

from jin10_research.option_dashboard import _basic_auth_matches


class BasicAuthTest(unittest.TestCase):
    def test_disabled_auth_accepts_request(self):
        self.assertTrue(_basic_auth_matches(None, "", ""))

    def test_valid_credentials(self):
        token = base64.b64encode("trader:secret".encode("utf-8")).decode("ascii")
        self.assertTrue(_basic_auth_matches("Basic " + token, "trader", "secret"))

    def test_invalid_credentials_and_malformed_header(self):
        wrong = base64.b64encode(b"trader:wrong").decode("ascii")
        self.assertFalse(_basic_auth_matches("Basic " + wrong, "trader", "secret"))
        self.assertFalse(_basic_auth_matches("Bearer something", "trader", "secret"))
        self.assertFalse(_basic_auth_matches("Basic not-base64!", "trader", "secret"))
        self.assertFalse(_basic_auth_matches(None, "trader", "secret"))


if __name__ == "__main__":
    unittest.main()

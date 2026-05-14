from __future__ import annotations

import unittest

from src.python.ai_scorer.evals.redaction import (
    EMAIL_PLACEHOLDER,
    PHONE_PLACEHOLDER,
    URL_PLACEHOLDER,
    redact_case_fields,
    redact_text,
)


class TestRedactText(unittest.TestCase):
    def test_email_is_redacted(self):
        text = "Contact us at jobs@example.com for more info."
        result = redact_text(text)
        self.assertNotIn("jobs@example.com", result)
        self.assertIn(EMAIL_PLACEHOLDER, result)

    def test_multiple_emails_are_redacted(self):
        text = "Send CV to hr@corp.io or hiring@corp.io."
        result = redact_text(text)
        self.assertNotIn("hr@corp.io", result)
        self.assertNotIn("hiring@corp.io", result)
        self.assertEqual(result.count(EMAIL_PLACEHOLDER), 2)

    def test_url_is_redacted_by_default(self):
        text = "Apply at https://jobs.example.com/open/1234 today."
        result = redact_text(text)
        self.assertNotIn("https://jobs.example.com/open/1234", result)
        self.assertIn(URL_PLACEHOLDER, result)

    def test_url_not_redacted_when_disabled(self):
        url = "https://example.com/apply"
        text = f"Apply at {url}."
        result = redact_text(text, redact_urls=False)
        self.assertIn(url, result)
        self.assertNotIn(URL_PLACEHOLDER, result)

    def test_http_url_is_also_redacted(self):
        text = "Visit http://example.com for details."
        result = redact_text(text)
        self.assertNotIn("http://example.com", result)
        self.assertIn(URL_PLACEHOLDER, result)

    def test_phone_e164_format_is_redacted(self):
        text = "Call us at +1 (555) 123-4567 for inquiries."
        result = redact_text(text)
        self.assertNotIn("+1 (555) 123-4567", result)
        self.assertIn(PHONE_PLACEHOLDER, result)

    def test_plain_text_without_sensitive_content_is_unchanged(self):
        text = "This role requires Python, Go, and cloud experience."
        result = redact_text(text)
        self.assertEqual(result, text)

    def test_empty_string_returns_empty_string(self):
        self.assertEqual(redact_text(""), "")

    def test_email_and_url_both_redacted(self):
        text = "Apply at https://example.com or email hr@example.com"
        result = redact_text(text)
        self.assertIn(EMAIL_PLACEHOLDER, result)
        self.assertIn(URL_PLACEHOLDER, result)


class TestRedactCaseFields(unittest.TestCase):
    def test_description_urls_and_emails_are_redacted(self):
        title = "Platform Engineer"
        description = "Apply at https://jobs.io/123 or email hr@corp.io"
        location = "Remote"

        r_title, r_desc, r_loc = redact_case_fields(title, description, location)

        self.assertEqual(r_title, title)
        self.assertIn(URL_PLACEHOLDER, r_desc)
        self.assertIn(EMAIL_PLACEHOLDER, r_desc)
        self.assertEqual(r_loc, location)

    def test_title_urls_are_not_redacted(self):
        # Titles normally contain no URLs; we skip URL redaction to preserve
        # any unusual short codes
        title = "Platform Engineer"
        r_title, _, _ = redact_case_fields(title, "", "")
        self.assertEqual(r_title, title)

    def test_location_emails_are_redacted(self):
        _, _, r_loc = redact_case_fields("", "", "contact@jobs.example.com")
        self.assertIn(EMAIL_PLACEHOLDER, r_loc)


if __name__ == "__main__":
    unittest.main()

import re
import unittest

from parser import ParseError, Parser


class ParserTests(unittest.TestCase):
    def test_nested_document(self):
        tokens = [
            "{",
            '"name"',
            ":",
            '"review-craft"',
            ",",
            '"scores"',
            ":",
            "[",
            "95",
            ",",
            "98.5",
            ",",
            "null",
            "]",
            ",",
            '"ready"',
            ":",
            "true",
            "}",
        ]
        self.assertEqual(
            Parser(tokens).parse(),
            {"name": "review-craft", "scores": [95, 98.5, None], "ready": True},
        )

    def test_empty_containers(self):
        self.assertEqual(Parser(["{", "}"]).parse(), {})
        self.assertEqual(Parser(["[", "]"]).parse(), [])

    def test_string_escapes(self):
        self.assertEqual(Parser(['"line\\nnext"']).parse(), "line\nnext")

    def test_number_grammar(self):
        self.assertEqual(Parser(["-12.5e+2"]).parse(), -1250.0)
        for token in ("01", "1.", "-.5", "1_000", "1x"):
            with self.subTest(token=token), self.assertRaisesRegex(
                ParseError, rf"token 0: {re.escape(repr(token))}"
            ):
                Parser([token]).parse()

    def test_unterminated_string_reports_the_offending_token(self):
        with self.assertRaisesRegex(ParseError, "token 0"):
            Parser(['"unterminated']).parse()

    def test_duplicate_key_is_rejected(self):
        with self.assertRaisesRegex(ParseError, "duplicate object key.*token 5"):
            Parser(["{", '"a"', ":", "1", ",", '"a"', ":", "2", "}"]).parse()

    def test_unterminated_array_is_rejected(self):
        with self.assertRaisesRegex(ParseError, "expected ','"):
            Parser(["[", "1"]).parse()

    def test_trailing_token_is_rejected(self):
        with self.assertRaisesRegex(ParseError, "unexpected trailing token"):
            Parser(["true", "false"]).parse()


if __name__ == "__main__":
    unittest.main()

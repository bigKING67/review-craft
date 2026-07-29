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

    def test_duplicate_key_is_rejected(self):
        with self.assertRaisesRegex(ParseError, "duplicate object key"):
            Parser(["{", '"a"', ":", "1", ",", '"a"', ":", "2", "}"]).parse()

    def test_unterminated_array_is_rejected(self):
        with self.assertRaisesRegex(ParseError, "expected ','"):
            Parser(["[", "1"]).parse()

    def test_trailing_token_is_rejected(self):
        with self.assertRaisesRegex(ParseError, "unexpected trailing token"):
            Parser(["true", "false"]).parse()


if __name__ == "__main__":
    unittest.main()

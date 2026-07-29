"""Recursive-descent parser for a compact JSON-like token stream."""


class ParseError(ValueError):
    pass


class Parser:
    def __init__(self, tokens):
        self.tokens = list(tokens)
        self.index = 0

    def current(self):
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def previous(self):
        if self.index == 0:
            return None
        return self.tokens[self.index - 1]

    def at_end(self):
        return self.current() is None

    def advance(self):
        token = self.current()
        if token is not None:
            self.index += 1
        return token

    def check(self, expected):
        return self.current() == expected

    def match(self, *expected):
        if self.current() not in expected:
            return False
        self.advance()
        return True

    def expect(self, expected, message):
        if not self.match(expected):
            raise self.error(message)
        return self.previous()

    def error(self, message):
        token = self.current()
        rendered = "end of input" if token is None else repr(token)
        return ParseError(f"{message} at token {self.index}: {rendered}")

    def parse(self):
        if self.at_end():
            raise self.error("expected a value")
        value = self.parse_value()
        if not self.at_end():
            raise self.error("unexpected trailing token")
        return value

    def parse_value(self):
        token = self.current()
        if token == "{":
            return self.parse_object()
        if token == "[":
            return self.parse_array()
        if token is not None and token.startswith('"'):
            return self.parse_string()
        if token in {"true", "false", "null"}:
            return self.parse_literal()
        if token is not None and self.looks_like_number(token):
            return self.parse_number()
        raise self.error("expected a value")

    def parse_object(self):
        self.expect("{", "expected object")
        result = {}
        if self.match("}"):
            return result
        while True:
            if self.current() is None or not self.current().startswith('"'):
                raise self.error("expected a quoted object key")
            key = self.parse_string()
            if key in result:
                raise self.error(f"duplicate object key {key!r}")
            self.expect(":", "expected ':' after object key")
            result[key] = self.parse_value()
            if self.match("}"):
                return result
            self.expect(",", "expected ',' between object entries")

    def parse_array(self):
        self.expect("[", "expected array")
        result = []
        if self.match("]"):
            return result
        while True:
            result.append(self.parse_value())
            if self.match("]"):
                return result
            self.expect(",", "expected ',' between array items")

    def parse_string(self):
        token = self.advance()
        if token is None or len(token) < 2 or not token.endswith('"'):
            raise self.error("unterminated string")
        body = token[1:-1]
        result = []
        index = 0
        escapes = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        while index < len(body):
            character = body[index]
            if character != "\\":
                result.append(character)
                index += 1
                continue
            index += 1
            if index >= len(body) or body[index] not in escapes:
                raise self.error("unsupported string escape")
            result.append(escapes[body[index]])
            index += 1
        return "".join(result)

    def looks_like_number(self, token):
        return bool(token) and token[0] in "-0123456789"

    def parse_number(self):
        token = self.advance()
        try:
            if any(marker in token for marker in (".", "e", "E")):
                value = float(token)
            else:
                value = int(token)
        except (TypeError, ValueError) as error:
            raise self.error("invalid number") from error
        if isinstance(value, float) and (value != value or abs(value) == float("inf")):
            raise self.error("non-finite numbers are not supported")
        return value

    def parse_literal(self):
        token = self.advance()
        if token == "true":
            return True
        if token == "false":
            return False
        if token == "null":
            return None
        raise self.error("unknown literal")

# Parser Contract

The parser consumes string tokens produced by a lexer. The lexer owns token boundaries,
including rejecting raw control characters or unescaped quotes inside string tokens. This
parser owns container structure, duplicate-key rejection, the documented simple string
escapes, JSON number grammar, literals, and parser error locations. Unicode escape decoding
is intentionally outside this compact token format.

The parser remains one cohesive state machine. Its length alone is not a reason to split or
rewrite it; changes should be driven by a concrete behavioral defect or measured maintenance
cost.

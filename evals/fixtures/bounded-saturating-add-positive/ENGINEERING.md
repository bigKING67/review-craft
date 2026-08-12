# Engineering Context

## Invariant

`saturating_add(left, right)` accepts two integers in the closed range `0..255` and must
return `min(255, left + right)` for every input pair.

## Implementation constraint

The implementation is deliberately branchless. It may use arithmetic, comparisons, masks,
and bitwise operators, but it must not use `if`, a conditional expression, `min`, or `max`.

## Non-goals

Inputs outside `0..255`, alternate numeric representations, and vectorization are outside
this fixture's contract.

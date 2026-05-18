# Test Suite

## Philosophy

Ten good tests beat a thousand mediocre ones. A bad test is not a neutral
placeholder — it is actively harmful. It adds noise, slows the suite, creates
false confidence, makes real refactoring harder, and trains reviewers to ignore
test failures. Deleting a bad test is a contribution. Deleting a whole bad
class of tests is a significant contribution.

Every test must answer: **what user-visible behavior breaks if this test fails?**
If the answer is "nothing observable changes," delete the test.

**Test coverage percentage is a bad metric and an explicit non-goal.** It
rewards writing tests for the wrong reasons, punishes deleting bad tests, and
produces exactly the kind of theatre this document warns against. Nobody should
ever say "we need more coverage" as a reason to add a test. That is the wrong
question. The only question is: does this test guard against a real failure mode
that matters?

## What counts as observable behavior

This is an embedded device. Observable behavior is:

- **Pixel output** — what appears on the 64×32 LED matrix
- **LED color and state** — the NeoPixel is the only feedback when the device
  runs headlessly; its state machine behavior is real user-visible output
- **Parsed and stored data** — forecast values, historical baselines, settings
  written to `settings.toml`
- **Error handling** — what the caller receives when a network call fails

Not observable: which private method was called, in what order, how many times.

## Render tests are the primary regression guard

The PNG reference images in `tests/reference-images/` are the most valuable
tests in the suite. A single render test catches layout changes, color math
errors, expired-hour handling, font rendering — everything — in one assertion.

Do not add pixel-by-pixel sweep tests that duplicate what a render test already
covers. If you want to verify a specific formula or geometric relationship,
write a render test and update the reference.

To regenerate references after an intentional change:

    pytest --update-refs

## What not to test

**HTML copy text.** `assert "restarting" in html` breaks when you reword a
sentence. It catches nothing real. The integration test covers actual form
behavior.

**Constants equaling other constants.** If `STALE_COLOR` and `COLOR_UNCERTAIN`
must be equal, that is a code comment, not a test. A render test will catch any
visual divergence.

**Mock call internals.** Do not assert `assert_called_once_with()`,
`assert_not_called()`, or call ordering on mocks unless the *only* observable
effect of the function under test is a side effect on a collaborator with no
better proxy. When in doubt, check the return value or the rendered output.

**Tautological color checks.** `led.success(); assert color == GREEN` where
`GREEN` is imported from the same module being tested is `assert GREEN == GREEN`.
Test state machine *transitions* instead: `led.failure(); led.success();
assert color == ORANGE` — two different code paths, one assertion.

**Zero-sweep pixel tests.** Asserting that every non-lit pixel is zero after a
one-hour render is covered by the render test. Don't iterate 2,048 pixels in a
unit test.

## Do not adjust production code for the sake of tests

Tests exist to verify production code. Adjusting production code to make it
easier to test — adding a parameter that only exists so a test can redirect a
file path, inserting a defensive `getattr` to accommodate an incomplete mock,
keeping a dead alias because tests reference the old name — is the cart before
the horse. Fix the test or the fixture instead.

## Mocking policy

Mock at hardware boundaries only: network I/O, the real NeoPixel, the real
matrix hardware. Use recorded fixture files for network responses rather than
fabricated return values wherever possible.

If your mock setup is longer than the code under test, that is a signal to
stop and reconsider.

## When to add a test

Be conservative. A new test is justified when a real bug is fixed and that
specific failure mode could plausibly recur — the test is then a regression
guard for a known-bad case.

A test is not justified to prove that a feature is implemented, to demonstrate
that a problem is no longer a problem, or to satisfy a coverage target. Those
motivations produce tests that describe the current code rather than guarding
against real failures.

When you are about to add a test, ask: if someone deleted the code path this
test exercises, would the device fail in a way that matters — or would it just
fail this test? If the answer is "just this test," don't add it.

## When to delete a test

A test that mirrors code rather than outcome must go. The clearest signal: you
are changing production code and a test breaks — not because the observable
behavior changed, but because the test was essentially a copy of the
implementation. That is a test that validates the current code, not the intended
behavior. Delete it and move on.

Other mandatory deletion signals:

- The test assertion is trivially true regardless of what the production code
  does (e.g., asserting that an object you never passed to the function was
  never called by it).
- The test asserts a range (`0.0 <= x <= 1.0`) for a value that the code
  computes by construction as `min(a/b, 1.0)`. Python's `min()` works. This is
  not a test.
- The test asserts something about the format of a debug `print()` statement
  visible only on the serial console.
- The test is an exact duplicate of another test with a slightly different
  mock arrangement.
- The test asserts specific values from a committed fixture rather than
  asserting that the code processed the fixture correctly — and would break
  if the fixture were updated for the same location.

## CircuitPython context

Tests run under CPython, not on the device. `tests/simlib/` stubs out
CircuitPython-only modules so `src/` code can be imported. This means:

- Tests can import and call production code directly — do so
- Hardware that cannot be simulated (real NeoPixel writes, actual Wi-Fi) is
  replaced at the module boundary; everything above that boundary runs for real
- `tests/simlib/` and `bin/simulate` exist for interactive development; the
  simulator may be complex. Tests must be simple.

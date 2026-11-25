# Test protocol — feature

1. **Criteria first.** Each acceptance criterion is observable at a public interface
   (API, CLI, UI, function the callers use). Known edge cases and traps from the spec
   or contracts become criteria of their own.
2. **One slice at a time.** For each criterion: write the test, see it fail for the
   expected reason, write the least code that makes it pass, run the targeted test.
   Do not write all tests first.
3. **Honest tests.** A test must fail without the change and assert the behaviour the
   user sees. Reject tests that would pass if the code returned nothing, that recompute
   the expected value the way the code does, or that only assert on mocks. Expected
   values come from the spec, a worked example or a known-good literal.
4. **Failure paths.** Every new error path a user can hit has a test, or a recorded
   reason why it cannot be triggered.
5. **When an honest automated test is impractical** (UI layout, third-party behaviour),
   record the observation that verifies the criterion (command, screenshot path,
   real-data run) in the WP Evidence instead of writing a weak test.
6. **Seams** are chosen in the WP test ladder (`targeted:`); there is no separate
   approval step for them.

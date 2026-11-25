# Test protocol — refactor

1. **No behaviour change.** A refactor WP changes structure only. If behaviour must
   change, that is a separate feature or bug-fix WP.
2. **Pin behaviour first.** Existing tests that cover the code are the pins. Where
   coverage is missing, add characterization tests that record what the code does now
   (inputs and outputs at a public interface), and run them on the base before
   changing anything.
3. **Assertions stay unchanged.** Existing assertions in tests are not edited or
   removed. `intel.sh signals --type refactor` flags any edited assertion; each one
   needs an explicit owner decision recorded in the WP.
4. **Prove it mechanically.** The test ladder gets a pinned line:
   `- pinned: base <Repo HEAD at start>, tests <new characterization tests>, command <targeted command>`
   qa-runner runs `intel.sh fbpa --mode refactor` (sandboxed like fbpa): the pins must pass on the base and on
   the final tree, in copies. BLOCKED is an owner decision, as for fbpa; never run the pins
   outside the sandbox instead.
5. **Simplify, do not shuffle.** The change should reduce the number of concepts, not
   move the same complexity into new helpers. Check for an existing helper before
   adding one.
6. **Mutation probe (T3 or weak pins only).** The reviewer may mutate up to three
   decision points in a scratch copy; a surviving mutation is a `test-weak` finding.

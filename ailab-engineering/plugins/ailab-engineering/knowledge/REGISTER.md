# Knowledge register — ailab-engineering

Owner-reviewed engineering lessons from A.I. Lab work packages. Agents read it
only through `scripts/knowledge.sh find` and never edit it. Entries are added or
changed only in an owner-approved review and ship with a plugin version bump.

Intake: `/ailab-engineering:wp-close` files at most one evidence-backed candidate
per work package (default none) as a new append-only record in
`<agent-logs>/_knowledge/inbox/` with `knowledge.sh add`. No owner approval is
needed to file a candidate, and filing changes nothing else.
`knowledge.sh pending` lists the inbox candidates that have no entry here yet.

Entry format (the same as an inbox record; the id never changes):

    ## K-<yyyymmdd>-<project>-<WP> — <title>
    Status: candidate | validated | promoted | project | merged | rejected — <reason>
    Source: <project> / <WP> / HEAD <sha> / <date>
    Lesson: <the check or instruction>
    Evidence: <path or commit> — <what it shows>
    Scope: project | cross-project — <why>
    Target: <smallest destination file> (when promoted: the file and commit)
    Related: none | <ids>

Statuses:
- candidate: filed, not reviewed yet.
- validated: seen in a second project, confirmed by a read-only check in another
  repository, or a lesson about this plugin itself.
- promoted: approved by the owner; the change is in the named target.
- project: valid but project-specific; it stays in that project's memory.
- merged: duplicate of the entry named in Related.
- rejected: not reusable or not backed by evidence; the reason is on the Status line.

Nothing expires and nothing is rejected automatically; there is no entry limit.
Volume is bounded by one candidate per work package, and the default is none.

## Entries

(none yet)

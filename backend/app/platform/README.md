# Platform infrastructure contracts

This package owns cross-domain infrastructure. It must not own learner, position, practice, essay, planning, or commerce decisions.

## HTTP contract

- Every request receives an X-Request-ID response header.
- API v2 public errors use code, message, requestId, and optional details.
- API v1 keeps its existing error body for iOS compatibility.
- Internal exception text is never returned as a public API message.

## Outbox

Domain writes and OutboxRepository.append must use the same SQLAlchemy Session and commit together.

The deduplication key is a stable domain fact, for example:

    question-attempt:{attempt_id}:recorded:v{revision}

Reusing a key with different aggregate, event type, or payload is a contract error. Publishers claim with FOR UPDATE SKIP LOCKED. Only the holder of the claim token can publish or fail an event.

## Background jobs

The job key identifies immutable input, for example:

    essay-review:{attempt_id}:{answer_version}:{prompt_version}

Enqueuing the same key and input returns the original job. Reusing a key with different input is rejected. Workers claim queued jobs with FOR UPDATE SKIP LOCKED. A stale worker cannot complete a job after losing its claim token.

Retryable failures return to queued until max_attempts is reached. Terminal failures retain a public error code and an operator-facing message. Domain services decide whether a completed AI result needs user confirmation before it can change derived learner state.

## Transaction boundary

Repositories never commit. The command handler owns the transaction:

    with session.begin():
        attempt = practice.record_attempt(session, command)
        outbox.append(
            session,
            deduplication_key=f"question-attempt:{attempt.id}:recorded:v{attempt.revision}",
            aggregate_type="question_attempt",
            aggregate_id=str(attempt.id),
            event_type="AttemptRecorded",
            payload={"revision": attempt.revision},
        )

If the transaction rolls back, both the domain write and event disappear.

## Position rule releases

Qualification decisions only use an active `product.position_rule_releases` row. Build a release with:

    cd backend
    .venv/bin/python scripts/compile_position_rule_release.py \
      --exam-year 2026 \
      --rule-set-version guokao-2026-rules-v1 \
      --position-data-version <immutable-position-import-version>

Inspect compilation counts and warnings before rerunning with `--activate`. Activation is rejected unless every position has an audit and the stored requirement count matches the audit. Existing compiled versions are immutable; source changes require a new position-data version.

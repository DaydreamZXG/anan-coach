class MatchingError(RuntimeError):
    code = "matching_error"


class MatchNotFound(MatchingError):
    code = "match_not_found"


class MatchVersionConflict(MatchingError):
    code = "match_version_conflict"


class MatchIdempotencyConflict(MatchingError):
    code = "match_idempotency_conflict"


class MatchTransitionError(MatchingError):
    code = "match_transition_invalid"


class MatchingPolicyError(MatchingError):
    code = "matching_policy_invalid"


class MatchingAuthorizationError(MatchingError):
    code = "matching_authorization_denied"


class AIAnswerNotApproved(MatchingError):
    code = "ai_answer_not_approved"

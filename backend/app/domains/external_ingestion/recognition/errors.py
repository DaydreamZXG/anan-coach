class RecognitionError(RuntimeError):
    code = "recognition_error"


class ProcessorNotApproved(RecognitionError):
    code = "processor_not_approved"


class ProcessorConfigurationError(RecognitionError):
    code = "processor_configuration_invalid"


class ProcessorResponseError(RecognitionError):
    code = "processor_response_invalid"


class ProcessorDispatchUnknownError(ProcessorResponseError):
    code = "processor_dispatch_outcome_unknown"


class ProcessorNotDispatchedError(ProcessorResponseError):
    code = "processor_not_dispatched"


class ArtifactNotProcessable(RecognitionError):
    code = "artifact_not_processable"


class RecognitionNotFound(RecognitionError):
    code = "recognition_not_found"


class RecognitionVersionConflict(RecognitionError):
    code = "recognition_version_conflict"


class RecognitionIdempotencyConflict(RecognitionError):
    code = "recognition_idempotency_conflict"


class RecognitionTransitionError(RecognitionError):
    code = "recognition_transition_invalid"


class RecognitionOperationReconciliationRequired(RecognitionError):
    code = "recognition_operation_reconciliation_required"


class RecognitionOperationPersistenceError(RecognitionError):
    code = "recognition_operation_persistence_failed"


class RecognitionQuotaExceeded(RecognitionError):
    code = "recognition_quota_exhausted"

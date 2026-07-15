class RecognitionError(RuntimeError):
    code = "recognition_error"


class ProcessorNotApproved(RecognitionError):
    code = "processor_not_approved"


class ProcessorConfigurationError(RecognitionError):
    code = "processor_configuration_invalid"


class ProcessorResponseError(RecognitionError):
    code = "processor_response_invalid"


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

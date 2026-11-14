class PipelineError(Exception):
    """Processing a document failed; `code` becomes the document's `failure_code`.

    The message is the code only: never document text, a quote or a key.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RetryableError(PipelineError):
    """A passing problem (the AI service is down): the job is tried again."""


class PermanentError(PipelineError):
    """Trying again will not help (the key is refused, the output is unusable)."""

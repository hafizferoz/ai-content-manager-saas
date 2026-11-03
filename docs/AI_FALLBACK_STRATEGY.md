# AI Fallback Strategy

The workspace **AI Providers** screen orders enabled providers by priority.
The first configured provider is primary. `AI_MAX_PROVIDER_ATTEMPTS` caps
provider attempts (default `3`, maximum `5`).

Fallback is attempted only for retryable categories: rate limit/quota,
timeout, network unavailability, and provider service errors. Authentication
failures, invalid requests, unsupported configuration, malformed provider
output, and schema validation failures stop the chain. A provider is tried at
most once per workflow; no blind same-provider retries are performed.

Agent Activity records each attempted provider and sanitized outcome, plus
the final provider/model and whether a fallback was used. If all attempted
providers fail, the workflow remains failed and no content draft is created.
The workflow does not silently change publishing or approval behavior.

The current implementation does not add exponential backoff, jitter, provider
health scoring, or circuit breakers. Retry after reviewing the recorded error
and provider status.

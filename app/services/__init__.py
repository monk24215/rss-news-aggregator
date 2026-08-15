"""Domain services.

Pure logic and database work, deliberately separate from both the web layer and the
task layer: a worker task should be a thin wrapper that calls one of these and records
the result. That is what keeps the pipeline stages independently retryable (§14,
Non-Negotiable #8) and what makes them testable without a queue.
"""

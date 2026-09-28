# Telemetry (OpenTelemetry)

- Instrument at boundaries — application entrypoints, outbound HTTP calls,
  database calls — not inside every internal function. A span around every
  helper produces noise that drowns the traces actually worth looking at.
- Prefer an auto-instrumentation package (e.g.
  `opentelemetry-instrumentation-requests`) over hand-writing spans for any
  well-known library that has one. Hand-write spans only for custom
  business logic that's genuinely worth tracing on its own:
  `tracer = trace.get_tracer(__name__)` /
  `with tracer.start_as_current_span("operation_name"): ...`.
- Use the OTEL metrics API for counters/histograms on critical paths
  (request count, request latency, job duration) instead of ad hoc counters
  scattered through the code.
- The exporter endpoint (OTLP collector URL) comes from `Settings` (see
  `configuration.md`) — never hardcode a collector address.
- Correlate logs with traces: inject the active `trace_id`/`span_id` into
  the `structlog` context (see `logging.md`) so a log line and the trace it
  happened inside of can be cross-referenced.
- Local development and tests default to a no-op or console exporter.
  Telemetry must never require a running collector for `uv run pytest` (or
  the app itself) to work — it degrades to doing nothing observable, not to
  an error.

## Approved libraries

- `opentelemetry-sdk`
- `opentelemetry-api`
- `opentelemetry-exporter-otlp-proto-http` (or the `-grpc` variant if the
  collector requires gRPC — pick one per project, don't ship both)
- The matching `opentelemetry-instrumentation-*` package for any
  auto-instrumentable library actually in use (e.g. `-requests`, `-httpx`)

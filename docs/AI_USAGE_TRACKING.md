# AI Usage Tracking

AI workflow calls record provider, model, task type, status, duration,
input/output token counts when available, whether counts are estimated,
fallback/attempt metadata, and a sanitized error category. The Usage page
shows summary totals, daily request aggregates, provider aggregates, and
recent requests. Data remains scoped to the owning organization.

Provider costs are currently unknown unless supplied by the existing usage
source; the system does not invent model pricing or assume a free tier.
Failed provider attempts are visible in Agent Activity, but per-attempt token
and cost accounting is not yet implemented.

Charts, date/provider/model/task filters, a pricing registry, request/token/
spend limits, threshold warnings, and full usage analytics are outstanding.

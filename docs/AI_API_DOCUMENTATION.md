# AI Provider API

All endpoints require authentication and workspace membership. Provider
configuration, model discovery, validation, and deletion additionally
require an `owner` or `admin` membership.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/workspaces/{workspace_id}/ai/providers` | Provider catalog and redacted workspace configuration |
| `PUT` | `/api/v1/workspaces/{workspace_id}/ai/providers/{provider}` | Add/update key, model, enablement, and fallback priority |
| `POST` | `/api/v1/workspaces/{workspace_id}/ai/providers/{provider}/validate` | Validate stored credential and selected model availability |
| `GET` | `/api/v1/workspaces/{workspace_id}/ai/providers/{provider}/models` | Discover model IDs from the configured provider |
| `DELETE` | `/api/v1/workspaces/{workspace_id}/ai/providers/{provider}` | Remove the workspace credential |

`PUT` accepts `api_key` (optional only when a key is already saved), `model`,
`enabled`, and `priority`. An empty/omitted key on update preserves the
existing credential. Secret values are never included in success or error
responses. Model discovery contacts the provider server-side and returns model
identifiers only.

Existing content generation endpoints are unchanged and continue to create
editable drafts. Provider errors return safe messages and codes; raw provider
responses are not returned.

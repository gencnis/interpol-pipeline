# M4 API Contract

## Redis event (worker → Redis → WS → browser)
```json
{
  "notice_id": "2021/12345",
  "change_type": "updated",
  "forename": "John",
  "name": "SMITH",
  "diff": {"nationalities": {"old": ["TR"], "new": ["TR","US"]}},
  "recorded_at": "2024-01-02T10:00:00+00:00"
}
```
`change_type`: "created" | "updated" | "withdrawn". `diff` is null for created/withdrawn.

## GET /api/notices
Query: name (ilike), nationality (exact code), status, page (≥1), per_page (1–100, default 20)
Response: `{"items": [{notice fields}], "total": int, "page": int, "per_page": int}`

Notice fields: notice_id, forename, name, sex_id, date_of_birth, nationalities (list),
arrest_warrant_countries (list), charge_text, thumbnail_object_key, status,
first_seen_at (ISO), last_seen_at (ISO), last_changed_at (ISO)

## GET /api/notices/{notice_id}  (notice_id may contain /)
Response: notice fields + photo_url (presigned URL or null) + history (list, version ASC)
History dict: id, version, change_type, content_hash, diff (null|object), valid_from, valid_to, recorded_at

## GET /api/alerts
Query: page, per_page
Returns notice_history rows where change_type IN (updated, withdrawn), newest first.
Response: `{"items": [{history fields + notice_forename + notice_name}], "total": int, "page": int, "per_page": int}`

## WebSocket
GET /ws/alerts — subscribe to Redis REDIS_EVENT_CHANNEL, push each event as JSON text.

## UI routes (Jinja2)
GET /           → dashboard.html
  ctx: request, notices, total, page, per_page, filter_name, filter_nationality, filter_status

GET /notices/{notice_id:path} → detail.html
  ctx: request, notice (+ photo_url), history

from datetime import datetime, timezone

demo_store = {
    "organizations": [{"id": "org-demo", "name": "Atelier Events", "slug": "atelier-events", "timezone": "Asia/Singapore", "preferred_language": "en"}],
    "profiles": [{"id": "user-leila", "full_name": "Leila Morgan", "email": "leila@atelier.events"}, {"id": "user-maya", "full_name": "Maya Chen", "email": "maya@future.systems"}, {"id": "user-jon", "full_name": "Jon Bell", "email": "jon@patternlabs.com"}],
    "organization_members": [
        {"id": "member-leila", "organization_id": "org-demo", "user_id": "user-leila", "full_name": "Leila Morgan", "email": "leila@atelier.events", "role": "organization_admin", "status": "active", "created_at": "2026-09-01T08:00:00Z"},
        {"id": "member-maya", "organization_id": "org-demo", "user_id": "user-maya", "full_name": "Maya Chen", "email": "maya@future.systems", "role": "content_editor", "status": "active", "created_at": "2026-09-05T08:00:00Z"},
        {"id": "member-jon", "organization_id": "org-demo", "user_id": "user-jon", "full_name": "Jon Bell", "email": "jon@patternlabs.com", "role": "speaker", "status": "active", "created_at": "2026-09-08T08:00:00Z"},
    ],
    "speakers": [
        {"id": "speaker-001", "organization_id": "org-demo", "name": "Maya Chen", "title": "Founder", "company": "Future Systems", "biography": "Designs resilient systems for teams working through uncertainty.", "photo_url": "", "profile_url": "https://example.com/maya"},
        {"id": "speaker-002", "organization_id": "org-demo", "name": "Jon Bell", "title": "Product director", "company": "Pattern Labs", "biography": "Explores the human layer of AI adoption and product change.", "photo_url": "", "profile_url": "https://example.com/jon"},
        {"id": "speaker-003", "organization_id": "org-demo", "name": "Ari Singh", "title": "Innovation partner", "company": "Northstar", "biography": "Helps event teams turn attention into measurable next action.", "photo_url": "", "profile_url": "https://example.com/ari"},
    ],
    "session_speakers": [
        {"id": "session-speaker-001", "session_id": "ses-001", "speaker_id": "speaker-001", "sort_order": 0},
        {"id": "session-speaker-002", "session_id": "ses-002", "speaker_id": "speaker-002", "sort_order": 0},
        {"id": "session-speaker-003", "session_id": "ses-003", "speaker_id": "speaker-003", "sort_order": 0},
    ],
    "invitations": [],
    "brand_kits": [{"id": "brand-demo", "organization_id": "org-demo", "name": "Atelier Events", "logo_url": "", "primary_color": "#7568f3", "secondary_color": "#1b1c2d", "accent_color": "#e4ff63", "font_family": "Inter", "tone": "clear, generous, modern", "website": "https://atelier.events", "created_at": "2026-09-01T08:00:00Z"}],
    "events": [{"id": "evt-001", "name": "Global Futures Forum", "slug": "global-futures-forum", "venue": "Marina Bay Sands · Singapore", "starts_at": "2026-09-26T08:30:00Z", "ends_at": "2026-09-26T16:30:00Z", "status": "live", "brand_color": "#7568f3"}],
    "sessions": [
        {"id": "ses-001", "event_id": "evt-001", "title": "Designing for what’s next", "track": "Main stage", "room": "Auditorium 1", "speaker": "Maya Chen · Future Systems", "starts_at": "2026-09-26T09:00:00Z", "ends_at": "2026-09-26T09:45:00Z", "status": "live", "attendance": 1842, "sentiment": .91, "summary": "The most resilient organizations build the habit of responding together."},
        {"id": "ses-002", "event_id": "evt-001", "title": "The human layer of AI", "track": "Leadership", "room": "Studio B", "speaker": "Jon Bell · Pattern Labs", "starts_at": "2026-09-26T10:15:00Z", "ends_at": "2026-09-26T11:00:00Z", "status": "upcoming", "attendance": 612, "sentiment": .86, "summary": "AI adoption is a people design problem before it is a technology problem."},
        {"id": "ses-003", "event_id": "evt-001", "title": "From attention to action", "track": "Growth", "room": "Studio A", "speaker": "Ari Singh · Northstar", "starts_at": "2026-09-26T11:15:00Z", "ends_at": "2026-09-26T12:00:00Z", "status": "upcoming", "attendance": 428, "sentiment": .79, "summary": "The best event experiences make the next step obvious."},
    ],
    "attendees": [
        {"id": "att-001", "event_id": "evt-001", "full_name": "Amara Okafor", "company": "Northstar Labs", "role": "Strategy lead", "interests": ["resilience", "AI", "leadership"], "intent_score": 92, "checked_in": True},
        {"id": "att-002", "event_id": "evt-001", "full_name": "Daniel Kim", "company": "Pattern Labs", "role": "Product director", "interests": ["AI", "product", "leadership"], "intent_score": 87, "checked_in": True},
        {"id": "att-003", "event_id": "evt-001", "full_name": "Sofia Martins", "company": "Brightline", "role": "Community director", "interests": ["resilience", "community", "growth"], "intent_score": 81, "checked_in": False},
        {"id": "att-004", "event_id": "evt-001", "full_name": "Mateo Silva", "company": "Fieldwork", "role": "Innovation partner", "interests": ["AI", "growth", "resilience"], "intent_score": 78, "checked_in": True},
    ],
    "insights": [{"id": "ins-001", "event_id": "evt-001", "session_id": "ses-001", "kind": "theme", "title": "Collective response is the new advantage", "body": "Across 68 live signals, attendees connected resilience with shared rituals, not isolated prediction.", "confidence": .94, "created_at": "2026-09-26T09:35:00Z"}],
    "share_links": [{"id": "lnk-001", "event_id": "evt-001", "label": "Attendee portal", "token": "gff-live", "destination": "/attendee/global-futures-forum", "clicks": 428}],
    "transcripts": [],
    "transcript_segments": [],
    "transcript_segment_revisions": [],
    "translations": [],
    "questions": [],
    "question_votes": [],
    "polls": [],
    "poll_options": [],
    "poll_responses": [],
    "feedback": [],
    "generated_assets": [],
    "generated_asset_versions": [],
    "reports": [],
    "takeaways": [],
    "summaries": [],
    "ai_conversations": [],
    "ai_messages": [],
    "files": [],
    "processing_jobs": [],
    "integrations": [],
    "audit_logs": [],
    "attendee_preferences": [],
    "topics": [],
    "topic_relations": [],
    "auth_users": [{"id": "user-demo-owner", "email": "daniduwijesinghe11@gmail.com", "password_hash": "pbkdf2$310000$e8a6b85a40b4a1e46b0ba90432bb9edd$1ef17a2838fd481244a110af722100373790adf86ca2952c1aaec7a607842aec", "role": "organization_admin"}],
    "auth_sessions": {},
    "auth_refresh_tokens": {},
    "auth_reset_tokens": {},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

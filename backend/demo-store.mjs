export const demoStore = {
  events: [{ id: 'evt-001', name: 'Global Futures Forum', slug: 'global-futures-forum', venue: 'Marina Bay Sands · Singapore', starts_at: '2026-09-26T08:30:00Z', ends_at: '2026-09-26T16:30:00Z', status: 'live', brand_color: '#7568f3' }],
  sessions: [
    { id: 'ses-001', event_id: 'evt-001', title: 'Designing for what’s next', track: 'Main stage', room: 'Auditorium 1', speaker: 'Maya Chen · Future Systems', starts_at: '2026-09-26T09:00:00Z', ends_at: '2026-09-26T09:45:00Z', status: 'live', attendance: 1842, sentiment: .91, summary: 'The most resilient organizations build the habit of responding together.' },
    { id: 'ses-002', event_id: 'evt-001', title: 'The human layer of AI', track: 'Leadership', room: 'Studio B', speaker: 'Jon Bell · Pattern Labs', starts_at: '2026-09-26T10:15:00Z', ends_at: '2026-09-26T11:00:00Z', status: 'upcoming', attendance: 612, sentiment: .86, summary: 'AI adoption is a people design problem before it is a technology problem.' },
    { id: 'ses-003', event_id: 'evt-001', title: 'From attention to action', track: 'Growth', room: 'Studio A', speaker: 'Ari Singh · Northstar', starts_at: '2026-09-26T11:15:00Z', ends_at: '2026-09-26T12:00:00Z', status: 'upcoming', attendance: 428, sentiment: .79, summary: 'The best event experiences make the next step obvious.' }
  ],
  attendees: [
    { id: 'att-001', event_id: 'evt-001', full_name: 'Leila Morgan', company: 'Atelier Works', role: 'VP Experience', intent_score: 96, checked_in: true, interests: ['AI', 'Experience'] },
    { id: 'att-002', event_id: 'evt-001', full_name: 'Noah Williams', company: 'Northstar', role: 'Strategy Lead', intent_score: 88, checked_in: true, interests: ['Growth', 'Data'] },
    { id: 'att-003', event_id: 'evt-001', full_name: 'Priya Das', company: 'Pattern Labs', role: 'Founder', intent_score: 82, checked_in: false, interests: ['AI', 'Leadership'] },
    { id: 'att-004', event_id: 'evt-001', full_name: 'Owen Hart', company: 'Vista Group', role: 'Marketing Director', intent_score: 76, checked_in: true, interests: ['Content', 'Brand'] }
  ],
  insights: [
    { id: 'ins-001', event_id: 'evt-001', session_id: 'ses-001', kind: 'theme', title: 'Collective response is the new advantage', body: 'Across 68 live signals, attendees connected resilience with shared rituals, not isolated prediction.', confidence: .94, created_at: '2026-09-26T09:35:00Z' },
    { id: 'ins-002', event_id: 'evt-001', session_id: 'ses-001', kind: 'quote', title: 'A line worth carrying forward', body: '“The teams that learn in public move faster than the teams that wait for certainty.”', confidence: .89, created_at: '2026-09-26T09:38:00Z' },
    { id: 'ins-003', event_id: 'evt-001', session_id: 'ses-001', kind: 'question', title: 'Audience is asking', body: 'How do we make response rituals visible across distributed teams?', confidence: .84, created_at: '2026-09-26T09:41:00Z' }
  ],
  share_links: [{ id: 'lnk-001', event_id: 'evt-001', label: 'Attendee portal', token: 'gff-live', destination: '/attendee/global-futures-forum', clicks: 428 }],
  transcripts: []
};

'use strict';

// WellPeps Pulse — shared vocabulary for every tab. Loaded before app.js and
// pulse.js. Raw values (enum codes) never appear as visible text; they go in
// a title attribute for debugging. Same CSP rules as app.js: no inline
// style or handlers, and every value passes through escHtml.

function escHtml(s) {
  if (s === null || s === undefined || s === '') return '';
  return String(s).replace(/[&<>"']/g, ch => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]
  ));
}

const LABELS = {
  category: {
    complaint: 'Complaint', question: 'Question', purchase_intent: 'Purchase intent', praise: 'Praise',
    misinformation: 'Misinformation', adverse_event: 'Possible adverse event',
    legal_regulatory: 'Legal / regulatory', privacy: 'Privacy', billing_fraud: 'Billing fraud', other: 'Other',
  },
  kind: {
    adverse_event: 'Possible adverse event', legal: 'Legal / regulatory threat', privacy: 'Privacy complaint',
    billing_fraud: 'Billing fraud accusation', viral_negative: 'Viral negative about WellPeps',
  },
  urgency: {urgent: 'Urgent', high: 'High', normal: 'Normal', low: 'Low'},
  platform: {
    reddit: 'Reddit', instagram: 'Instagram', facebook: 'Facebook', tiktok: 'TikTok', x: 'X',
    youtube: 'YouTube', trustpilot: 'Trustpilot', bbb: 'BBB', google_reviews: 'Google reviews',
    web: 'Web', other: 'Other',
  },
  status: {
    new: 'New', triaged: 'Triaged', drafted: 'Drafted', in_review: 'Waiting on you',
    approved: 'Approved — post by hand', rejected: 'Rejected', posted: 'Posted', dropped: 'Dropped',
    escalated: 'Escalated',
  },
  subject: {wellpeps: 'WellPeps', competitor: 'Competitor', product: 'Product', category: 'Category', drug: 'Drug', none: 'None'},
  role: {viewer: 'Viewer', reviewer: 'Reviewer', clinical: 'Clinical', admin: 'Admin'},
  verdict: {pass: 'Reviewer passed', reject: 'Reviewer rejected', needs_human: 'Needs a human'},
  tier: {green: 'Filter: clear', yellow: 'Filter: caution', red: 'Filter: blocked'},
  event: {
    collected: 'Collected', triaged: 'Triaged', drafted: 'Drafted', filtered: 'Filtered', reviewed: 'Reviewed',
    edited: 'Edited', approved: 'Approved', rejected: 'Rejected', posted: 'Marked posted', copied: 'Copied',
    escalated: 'Escalated', acked: 'Acknowledged',
  },
  owner: {
    marketing: 'Marketing', product: 'Product', support: 'Support', clinical: 'Clinical',
    compliance: 'Compliance', leadership: 'Leadership',
  },
  horizon: {this_week: 'This week', this_month: 'This month', watch: 'Watch'},
  period: {daily: 'Daily', weekly: 'Weekly'},
  sentiment: {positive: 'Positive', neutral: 'Neutral', negative: 'Negative', mixed: 'Mixed'},
  why: {
    keyword: 'Keyword rule', safety_screen: 'Safety check', severe_category: 'Triage',
    model: 'Triage', triage_failed: 'Triage failed', manual: 'Manual',
  },
};

function titleCase(raw) {
  return String(raw === null || raw === undefined ? '' : raw).replace(/[_-]+/g, ' ').trim()
    .replace(/\b\w/g, c => c.toUpperCase());
}

// Human label for a raw code; falls back to Title Case.
function label(group, raw) {
  if (raw === null || raw === undefined || raw === '') return '';
  const map = LABELS[group] || {};
  const key = String(raw);
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : titleCase(key);
}

// Escaped label with the raw code in the tooltip.
function labelHtml(group, raw) {
  if (raw === null || raw === undefined || raw === '') return '';
  return '<span title="' + escHtml(raw) + '">' + escHtml(label(group, raw)) + '</span>';
}

// ── Time ──

// The DB stores naive UTC; without a zone suffix the browser would read it as local.
const NAIVE_TS = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

function parseTs(d) {
  if (!d) return null;
  const s = String(d);
  const dt = new Date(NAIVE_TS.test(s) ? s.replace(' ', 'T') + 'Z' : s);
  return isNaN(dt) ? null : dt;
}

function clockTime(dt) {
  return dt.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
}

function absTime(d) {
  const dt = parseTs(d);
  return dt ? dt.toLocaleString('en-US', {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short'}) : '';
}

// "just now", "3h ago", "in 12m", "yesterday 4:05 PM", "Sep 22, 4:05 PM".
function relTime(d, now) {
  const dt = parseTs(d);
  if (!dt) return '';
  now = now || new Date();
  const secs = Math.round((dt.getTime() - now.getTime()) / 1000);
  const abs = Math.abs(secs);
  const fmt = n => (secs < 0 ? n + ' ago' : 'in ' + n);
  if (abs < 45) return secs < 0 ? 'just now' : 'in a moment';
  if (abs < 3600) return fmt(Math.round(abs / 60) + 'm');
  if (abs < 6 * 3600) return fmt(Math.floor(abs / 3600) + 'h' + (abs % 3600 >= 600 && abs < 3 * 3600 ? ' ' + Math.round((abs % 3600) / 60) + 'm' : ''));
  const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(dt) - day(now)) / 86400000);
  if (days === 0) return 'today ' + clockTime(dt);
  if (days === -1) return 'yesterday ' + clockTime(dt);
  if (days === 1) return 'tomorrow ' + clockTime(dt);
  const sameYear = dt.getFullYear() === now.getFullYear();
  return dt.toLocaleString('en-US', {month: 'short', day: 'numeric', year: sameYear ? undefined : 'numeric'}) +
    ', ' + clockTime(dt);
}

// <time> with the relative text and the absolute time in the tooltip.
function timeHtml(d) {
  const dt = parseTs(d);
  if (!dt) return escHtml(d || '');
  return '<time datetime="' + escHtml(dt.toISOString()) + '" title="' + escHtml(absTime(d)) + '">' +
    escHtml(relTime(d)) + '</time>';
}

// ── Platforms ──

const PLATFORM_ICONS = ['reddit', 'instagram', 'facebook', 'tiktok', 'x', 'youtube', 'trustpilot', 'bbb',
  'google_reviews', 'web', 'other'];

function platformIcon(p) {
  const id = PLATFORM_ICONS.includes(p) ? p : 'other';
  return '<svg class="pf-icon" aria-hidden="true" focusable="false"><use href="/static/icons.svg#pf-' + id + '"></use></svg>';
}

// Icon + human name, raw code in the tooltip.
function platformHtml(p) {
  return '<span class="pf" title="' + escHtml(p || '') + '">' + platformIcon(p) +
    '<span>' + escHtml(label('platform', p) || 'Unknown') + '</span></span>';
}

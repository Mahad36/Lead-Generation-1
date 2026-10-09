export const LEAD_STATUS = {
  new: "New",
  processing: "Analyzing",
  ready: "Ready to go",
  no_email: "No email (not emailed)",
  failed: "Needs review",
} as const;

export const LEAD_SOURCE = {
  google_maps: "Google Maps",
  csv: "CSV import",
  manual: "Manual",
} as const;

export const SAMPLE_LEADS_CSV = `business_name,category,city,country,phone,email,website
Test Plumbing Co,Plumbers,Miami,United States,3055550100,info@testplumbing.example,https://example.com
Sunrise Dental,Dentists,Miami,United States,3055550101,hello@sunrisedental.example,
`;

export const CAMPAIGN_STATUS: Record<string, string> = {
  draft: "Draft",
  scheduled: "Scheduled",
  running: "Running",
  paused: "Paused",
  completed: "Completed",
  stopped: "Stopped",
  failed: "Failed",
};

export const CAMPAIGN_LEAD_STATUS: Record<string, string> = {
  pending: "Writing email",
  generating: "Writing email",
  ready: "Ready to review",
  approved: "Queued",
  queued: "Queued",
  sending: "Sending",
  auditing: "Auditing website",
  sent: "Sent",
  failed: "Failed",
  skipped: "Skipped",
  unsubscribed: "Unsubscribed",
};

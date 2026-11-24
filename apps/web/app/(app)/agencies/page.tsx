"use client";

import { AgencyPitchStatus } from "@/components/agency-pitch";
import { ScrapeWorkspace } from "@/components/scrape-workspace";

export default function AgenciesPage() {
  return (
    <ScrapeWorkspace
      purpose="agency"
      title="Agency outreach"
      intro={
        <>
          Find agencies on Google Maps (e.g. Shopify or marketing agencies in US cities) and offer them your
          white-label services. Each agency with a checked email address gets its own email, written by Claude from its
          website and your pitch, with your company profile attached. Sending uses the same inboxes, daily limits, health
          protection, sending hours and follow-ups as your audit emails.
        </>
      }
      header={<AgencyPitchStatus />}
      defaultAreas={["New York, New York"]}
      defaultTypes={["Shopify agency", "Marketing agency"]}
      namePlaceholder="US Shopify agencies"
    />
  );
}

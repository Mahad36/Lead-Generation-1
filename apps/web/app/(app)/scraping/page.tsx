"use client";

import { ScrapeWorkspace } from "@/components/scrape-workspace";

export default function ScrapingPage() {
  return (
    <ScrapeWorkspace
      purpose="audit"
      title="Scraping / Leads"
      intro={
        <>
          Each job stays in its own file, with its own automatic outreach: every lead with a website and an email is
          audited and gets its own personalised email with the PDF attached, within that file&apos;s daily limit. Start,
          pause or stop each file separately.
        </>
      }
      defaultAreas={["Miami, Florida"]}
      defaultTypes={["Plumbers"]}
      namePlaceholder="New York HVAC Leads"
    />
  );
}

import { Badge } from "@/components/ui";
import { formatDate } from "@/lib/utils";

export type Tracking = {
  open_count?: number;
  opened_at?: string | null;
  last_opened_at?: string | null;
  click_count?: number;
  clicked_at?: string | null;
  last_clicked_at?: string | null;
  replied_at?: string | null;
  bounced_at?: string | null;
};

/** Opened / Replied / Bounced badges for a sent email. Opens are approximate (image loading); replies are exact. */
export function TrackingBadges({ t, sent }: { t: Tracking; sent: boolean }) {
  if (!sent) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {t.bounced_at ? (
        <Badge tone="bad">Bounced</Badge>
      ) : (
        <>
          {t.replied_at ? <Badge tone="ok">Replied {formatDate(t.replied_at)}</Badge> : null}
          {t.open_count ? (
            <span title={`First opened ${formatDate(t.opened_at)}, last ${formatDate(t.last_opened_at)}`}>
              <Badge tone="ok">Opened{t.open_count > 1 ? ` ${t.open_count}×` : ""}</Badge>
            </span>
          ) : !t.replied_at ? (
            <Badge>Not opened yet</Badge>
          ) : null}
          {t.click_count ? (
            <span title={`First click ${formatDate(t.clicked_at)}, last ${formatDate(t.last_clicked_at)}`}>
              <Badge tone="ok">Clicked{t.click_count > 1 ? ` ${t.click_count}×` : ""}</Badge>
            </span>
          ) : null}
        </>
      )}
    </span>
  );
}

"use client";

import { Badge, Button, Input, Label } from "@/components/ui";
import { api } from "@/lib/api";
import { CalendarPlus, Clock, Lightbulb, MapPin, X } from "lucide-react";
import { useEffect, useState } from "react";

/** When a file's / campaign's automatic emails go out (apps/api/app/schemas/scraping.py::ScheduleIn). */
export type Schedule = {
  // local: set hours in each lead's time; global: my sending hours; now: right away; slots: chosen dates and times
  mode: "local" | "global" | "now" | "slots";
  days: number[]; // Monday = 0 ... Sunday = 6
  start: number;
  end: number;
  start_at: string; // "" or a datetime-local value: nothing goes out before then
  slots: Slot[];
  slots_zone: "mine" | "lead"; // the chosen times are in my time / in each lead's own time
};
export type Slot = { date: string; start: string; end: string }; // "2026-10-16", "13:00", "15:00"

export const DEFAULT_SCHEDULE: Schedule = {
  mode: "local",
  days: [1, 2, 3],
  start: 9,
  end: 11,
  start_at: "",
  slots: [],
  slots_zone: "mine",
};

type Plan = {
  suggestion: { days: number[]; start: number; end: number; label: string; note: string };
  zones: { timezone: string; places: string[]; their_time_now: string; in_your_time: string }[];
};
type City = { area: string; tags: string[]; why: string };

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const SCHEDULE_COUNTRIES = [
  "United States", "Canada", "United Kingdom", "Australia", "New Zealand", "Ireland", "Germany", "Netherlands",
  "France", "Spain", "Italy", "United Arab Emirates", "Saudi Arabia", "Qatar", "Singapore", "South Africa",
  "India", "Pakistan",
];
const SELECT = "rounded-md border border-border bg-card px-3 py-2 text-sm";
const hour = (h: number) => `${String(h % 24).padStart(2, "0")}:00`;
const pad = (n: number) => String(n).padStart(2, "0");
const isoDay = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (day: string, n: number) => {
  const d = new Date(`${day}T12:00:00`);
  d.setDate(d.getDate() + n);
  return isoDay(d);
};
const dayLabel = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

/** For the API: the start time as an ISO moment in your own time zone (or null), and your time zone for the slots. */
export function scheduleForApi(s: Schedule) {
  return {
    ...s,
    start_at: s.start_at ? new Date(s.start_at).toISOString() : null,
    slots: s.mode === "slots" ? s.slots.filter((x) => x.date) : [],
    my_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
  };
}

/** What's wrong with the chosen dates and times ("" when they're fine). */
export function slotsProblem(s: Schedule): string {
  if (s.mode !== "slots") return "";
  const slots = s.slots.filter((x) => x.date);
  if (!slots.length) return "Add at least one date and time to send on";
  const bad = slots.find((x) => x.end <= x.start);
  if (bad) return `${dayLabel(bad.date)}: the "until" time must be after the "from" time`;
  if (slots.every((x) => x.date < isoDay(new Date()))) return "All the dates you chose have passed";
  return "";
}

/** The chosen dates with their time windows: add as many as you like. */
function SlotList({
  value,
  onChange,
  suggestion,
}: {
  value: Schedule;
  onChange: (patch: Partial<Schedule>) => void;
  suggestion?: Plan["suggestion"];
}) {
  const today = isoDay(new Date());
  const slots = value.slots;
  const setSlot = (i: number, patch: Partial<Slot>) =>
    onChange({ slots: slots.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const addSlot = () => {
    const last = slots[slots.length - 1];
    const date = last?.date ? addDays(last.date, 1) : addDays(today, 1);
    onChange({ slots: [...slots, { date, start: last?.start || "09:00", end: last?.end || "11:00" }] });
  };
  const addBestDays = () => {
    if (!suggestion) return;
    const added: Slot[] = [];
    for (let n = 0; n < 21 && added.length < 3; n++) {
      const date = addDays(today, n);
      const weekday = (new Date(`${date}T12:00:00`).getDay() + 6) % 7; // Monday = 0
      if (suggestion.days.includes(weekday) && !slots.some((x) => x.date === date))
        added.push({ date, start: hour(suggestion.start), end: hour(suggestion.end) });
    }
    onChange({ slots: [...slots, ...added].sort((a, b) => a.date.localeCompare(b.date)), slots_zone: "lead" });
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-4 text-sm">
        <span className="text-muted">These times are:</span>
        <label className="flex items-center gap-2">
          <input type="radio" checked={value.slots_zone === "mine"} onChange={() => onChange({ slots_zone: "mine" })} />
          My time
        </label>
        <label className="flex items-center gap-2">
          <input type="radio" checked={value.slots_zone === "lead"} onChange={() => onChange({ slots_zone: "lead" })} />
          Each lead&apos;s own time
        </label>
      </div>
      {slots.map((slot, i) => {
        const past = !!slot.date && slot.date < today;
        const wrong = slot.end <= slot.start;
        return (
          <div key={i} className="flex flex-wrap items-end gap-2">
            <div>
              {i === 0 ? <Label>Date</Label> : null}
              <Input type="date" min={today} value={slot.date} onChange={(e) => setSlot(i, { date: e.target.value })} />
            </div>
            <div>
              {i === 0 ? <Label>From</Label> : null}
              <Input type="time" value={slot.start} onChange={(e) => setSlot(i, { start: e.target.value })} />
            </div>
            <div>
              {i === 0 ? <Label>Until</Label> : null}
              <Input type="time" value={slot.end} onChange={(e) => setSlot(i, { end: e.target.value })} />
            </div>
            <Button
              type="button"
              variant="secondary"
              aria-label="Remove this date"
              onClick={() => onChange({ slots: slots.filter((_, j) => j !== i) })}
            >
              <X className="h-4 w-4" />
            </Button>
            {slot.date ? <span className="pb-2 text-xs text-muted">{dayLabel(slot.date)}</span> : null}
            {past ? <Badge tone="warn">Date has passed</Badge> : null}
            {wrong ? <Badge tone="warn">Until must be after From</Badge> : null}
          </div>
        );
      })}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="secondary" onClick={addSlot}>
          <CalendarPlus className="mr-1 h-4 w-4" />
          {slots.length ? "Add another date" : "Add a date"}
        </Button>
        {suggestion ? (
          <Button type="button" variant="secondary" onClick={addBestDays}>
            <Lightbulb className="mr-1 h-4 w-4" />
            Add the next 3 best days ({hour(suggestion.start)}-{hour(suggestion.end)} their time)
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** Suggested cities for a country: biggest business markets and high-income areas. Click one to add it. */
export function CitySuggestions({
  country,
  selected,
  onAdd,
}: {
  country: string;
  selected: string[];
  onAdd: (area: string) => void;
}) {
  const [cities, setCities] = useState<City[]>([]);
  const [tag, setTag] = useState<"all" | "Most businesses" | "High-paying clients">("all");
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (!country) return;
    api<City[]>(`/api/scheduling/cities?country=${encodeURIComponent(country)}`)
      .then(setCities)
      .catch(() => setCities([]));
  }, [country]);

  if (!cities.length) return null;
  const shown = cities.filter((c) => tag === "all" || c.tags.includes(tag));

  return (
    <div className="rounded-xl border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" className="flex items-center gap-2 text-sm font-medium" onClick={() => setOpen(!open)}>
          <Lightbulb className="h-4 w-4 text-yellow-500" />
          Suggested places in {country}
          <span className="text-xs font-normal text-muted">{open ? "hide" : "show"}</span>
        </button>
        {open ? (
          <div className="flex flex-wrap gap-1">
            {(["all", "Most businesses", "High-paying clients"] as const)
              .filter((t) => t === "all" || cities.some((c) => c.tags.includes(t)))
              .map((t) => (
              <Button key={t} type="button" variant={tag === t ? "primary" : "secondary"} onClick={() => setTag(t)}>
                {t === "all" ? "All" : t}
              </Button>
            ))}
          </div>
        ) : null}
      </div>
      {open ? (
        <>
          <div className="mt-3 flex flex-wrap gap-2">
            {shown.map((c) => {
              const added = selected.includes(c.area);
              return (
                <button
                  key={c.area}
                  type="button"
                  title={c.why}
                  disabled={added}
                  onClick={() => onAdd(c.area)}
                  className={`rounded-lg border px-3 py-1.5 text-left text-sm ${
                    added ? "border-primary bg-primary/10 text-muted" : "border-border hover:border-primary"
                  }`}
                >
                  <span className="font-medium">{added ? "✓ " : "+ "}{c.area}</span>
                  <span className="block text-xs text-muted">{c.why}</span>
                </button>
              );
            })}
          </div>
          <p className="mt-2 text-xs text-muted">
            Largest cities by population for every country (GeoNames, geonames.org), plus hand-picked notes on
            business density and income where available. Bigger markets give more leads; high-income areas have owners
            more able to pay for a new website.
          </p>
        </>
      ) : null}
    </div>
  );
}

/** Sending schedule with the best time for the chosen places, shown in their time and yours. */
export function SchedulePicker({
  country,
  areas,
  value,
  onChange,
  showStartAt = true,
  windowLabel,
  onCountryChange,
}: {
  country: string;
  areas: string[];
  value: Schedule;
  onChange: (next: Schedule) => void;
  showStartAt?: boolean;
  windowLabel?: string; // your sending hours from settings, e.g. "17:00-02:00, Mon-Fri"
  onCountryChange?: (country: string) => void; // set: a country choice for the suggestions (campaigns)
}) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const areasKey = areas.join("|");

  useEffect(() => {
    if (!country) return;
    const query = new URLSearchParams({ country });
    areas.forEach((a) => query.append("areas", a));
    api<Plan>(`/api/scheduling/plan?${query}`)
      .then(setPlan)
      .catch(() => setPlan(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [country, areasKey]);

  const set = (patch: Partial<Schedule>) => onChange({ ...value, ...patch });
  const s = plan?.suggestion;
  const matchesSuggestion =
    !!s && value.mode === "local" && value.start === s.start && value.end === s.end && value.days.join() === s.days.join();

  return (
    <div className="space-y-3 rounded-xl border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Clock className="h-4 w-4" />
          Sending schedule
        </div>
        {onCountryChange ? (
          <label className="flex items-center gap-2 text-xs text-muted">
            Leads are in
            <select className={SELECT} value={country} onChange={(e) => onCountryChange(e.target.value)}>
              {SCHEDULE_COUNTRIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      {s ? (
        <div className="space-y-2 rounded-lg bg-background p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <span className="font-medium">Best time for {country}: </span>
              {s.label}
              <span className="block text-xs text-muted">{s.note}</span>
            </div>
            {matchesSuggestion ? (
              <Badge tone="ok">Using this</Badge>
            ) : (
              <Button type="button" variant="secondary" onClick={() => set({ mode: "local", days: s.days, start: s.start, end: s.end })}>
                Use this time
              </Button>
            )}
          </div>
          {plan.zones.length ? (
            <div className="space-y-1 text-xs">
              {plan.zones.map((z) => (
                <div key={z.timezone} className="flex flex-wrap items-center gap-x-2 text-muted">
                  <MapPin className="h-3 w-3" />
                  <span className="text-foreground">{z.places.slice(0, 3).join(", ")}{z.places.length > 3 ? ` +${z.places.length - 3}` : ""}</span>
                  <span>· their time now {z.their_time_now}</span>
                  {z.in_your_time ? (
                    <span>
                      · {hour(s.start)}-{hour(s.end)} there is <span className="font-medium text-foreground">{z.in_your_time}</span> your time
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-4 text-sm">
        <label className="flex items-center gap-2">
          <input type="radio" checked={value.mode === "now"} onChange={() => set({ mode: "now" })} />
          Send now (each email as soon as it&apos;s ready)
        </label>
        <label className="flex items-center gap-2">
          <input type="radio" checked={value.mode === "local"} onChange={() => set({ mode: "local" })} />
          At set hours in each lead&apos;s own time
        </label>
        <label className="flex items-center gap-2">
          <input type="radio" checked={value.mode === "global"} onChange={() => set({ mode: "global" })} />
          My sending hours{windowLabel ? ` (${windowLabel})` : " (AI & settings)"}
        </label>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            checked={value.mode === "slots"}
            onChange={() =>
              set({
                mode: "slots",
                slots: value.slots.length
                  ? value.slots
                  : [{ date: addDays(isoDay(new Date()), 1), start: "09:00", end: "11:00" }],
              })
            }
          />
          On dates and times I choose
        </label>
      </div>

      {value.mode === "slots" ? <SlotList value={value} onChange={set} suggestion={s} /> : null}

      {value.mode === "local" ? (
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label>Days</Label>
            <div className="mt-1 flex flex-wrap gap-1">
              {DAYS.map((d, i) => {
                const on = value.days.includes(i);
                return (
                  <button
                    key={d}
                    type="button"
                    onClick={() => set({ days: on ? value.days.filter((x) => x !== i) : [...value.days, i].sort() })}
                    className={`rounded-md border px-2 py-1 text-xs ${on ? "border-primary bg-primary text-white" : "border-border"}`}
                  >
                    {d}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <Label>From (their time)</Label>
            <select className={SELECT} value={value.start} onChange={(e) => set({ start: Number(e.target.value) })}>
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>
                  {hour(h)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label>Until</Label>
            <select className={SELECT} value={value.end} onChange={(e) => set({ end: Number(e.target.value) })}>
              {Array.from({ length: 24 }, (_, i) => i + 1).map((h) => (
                <option key={h} value={h}>
                  {hour(h)}
                </option>
              ))}
            </select>
          </div>
        </div>
      ) : null}

      {showStartAt ? (
        <div className="max-w-xs">
          <Label>Don&apos;t send before (your time, optional)</Label>
          <Input type="datetime-local" value={value.start_at} onChange={(e) => set({ start_at: e.target.value })} />
        </div>
      ) : null}
      <p className="text-xs text-muted">
        {value.mode === "now"
          ? "Each email goes out as soon as its audit is ready, at any hour. Daily limits and 2 minutes between emails still apply."
          : value.mode === "slots"
          ? "Emails only go out inside the dates and times above; whatever isn't sent in one window waits for the next. If every date has passed, emails wait until you add a new one."
          : "Leads are still found and audited straight away; their emails wait until the schedule opens."}{" "}
        Follow-ups use the same schedule.
      </p>
    </div>
  );
}

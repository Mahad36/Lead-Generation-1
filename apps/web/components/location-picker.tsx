"use client";

import { Input, Label } from "@/components/ui";
import { useEffect, useRef, useState } from "react";

type Place = { label: string; kind: "country" | "state" | "city"; code?: string };
export type Location = { country: string; area: string };

const SELECT = "mt-1 w-full rounded-md border border-border bg-card px-3 py-2 text-sm";

// Bump when the /geo response format changes, so browsers don't reuse an old cached list.
const GEO_VERSION = "2";

async function geo(query = ""): Promise<Place[]> {
  const res = await fetch(`/geo${query ? `${query}&` : "?"}v=${GEO_VERSION}`);
  if (!res.ok) return [];
  const rows: (Place & { name?: string })[] = await res.json();
  return rows.map((row) => ({ ...row, label: row.label ?? row.name ?? "" })).filter((row) => row.label);
}

/** The place part of the Maps search: "Florida", "Miami, Florida", "Lahore, Punjab" or any town typed in. */
export function searchArea(location: Location): string {
  return location.area.trim();
}

/** Country, then one field for a state / province / city of that country (states listed first; type to find cities). */
export function LocationPicker({
  value,
  onChange,
  onPick,
}: {
  value: Location;
  onChange: (next: Location) => void;
  onPick?: (area: string) => void; // set: a chosen place is added to a list and the field clears
}) {
  const [countries, setCountries] = useState<Place[]>([]);
  const [options, setOptions] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState<string | null>(null); // null = show the country's states
  const box = useRef<HTMLDivElement>(null);
  const countryCode = countries.find((c) => c.label === value.country)?.code || "";

  useEffect(() => {
    geo().then(setCountries).catch(() => setCountries([]));
  }, []);

  useEffect(() => {
    if (!countryCode) return;
    let live = true;
    const timer = setTimeout(() => {
      geo(`?country=${countryCode}${query ? `&q=${encodeURIComponent(query)}` : ""}`).then((rows) => live && setOptions(rows));
    }, query ? 150 : 0);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [countryCode, query]);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const hasStates = options.some((o) => o.kind === "state");

  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div>
        <Label>Country</Label>
        <select
          className={SELECT}
          value={value.country}
          onChange={(e) => {
            setQuery(null);
            onChange({ country: e.target.value, area: "" });
          }}
        >
          {!countries.length ? <option value={value.country}>{value.country}</option> : null}
          {countries.map((c) => (
            <option key={c.code} value={c.label}>
              {c.label}
            </option>
          ))}
        </select>
      </div>
      <div ref={box} className="relative">
        <Label>State / province / city</Label>
        <Input
          className="mt-1"
          value={value.area}
          placeholder={hasStates || query === null ? "Choose a state or province, or type a city" : "Type a city"}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            onChange({ ...value, area: e.target.value });
            setQuery(e.target.value.trim() || null);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setOpen(false);
            if (e.key === "Enter") {
              e.preventDefault();
              const picked = open && options[0] ? options[0].label : value.area.trim();
              if (onPick && picked) {
                onPick(picked);
                onChange({ ...value, area: "" });
                setQuery(null);
              } else if (picked) {
                onChange({ ...value, area: picked });
              }
              setOpen(false);
            }
          }}
        />
        {open && options.length ? (
          <ul className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-md border border-border bg-card py-1 text-sm shadow-lg">
            {options.map((o) => (
              <li key={`${o.kind}:${o.label}`}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left hover:bg-background"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    if (onPick) {
                      onPick(o.label);
                      onChange({ ...value, area: "" });
                      setQuery(null);
                    } else {
                      onChange({ ...value, area: o.label });
                    }
                    setOpen(false);
                  }}
                >
                  <span>{o.label}</span>
                  <span className="text-xs text-muted">{o.kind === "state" ? "State / province" : "City"}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <p className="mt-1 text-xs text-muted">
          {onPick
            ? "Pick as many states, provinces or cities as you like (or type a town and press Enter)."
            : "A state or province searches all of it; a city searches just that city. You can also type any town."}
        </p>
      </div>
    </div>
  );
}

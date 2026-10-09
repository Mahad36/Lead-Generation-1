import { City, Country, State } from "country-state-city";
import { NextResponse } from "next/server";

// Location data for the scraping form, from the bundled country-state-city package (no network calls).
//   /geo                     -> countries
//   /geo?country=US          -> its states / provinces (or its first cities when it has none)
//   /geo?country=US&q=mia    -> states / provinces and cities matching "mia" ("Miami, Florida")
type Place = { label: string; kind: "country" | "state" | "city" };

const LIMIT = 40;

export function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const country = params.get("country") || "";
  const q = (params.get("q") || "").trim().toLowerCase();
  if (!country) {
    const countries: Place[] = Country.getAllCountries().map((c) => ({ label: c.name, kind: "country", code: c.isoCode }) as Place);
    return json(countries);
  }
  const states = State.getStatesOfCountry(country);
  const stateName = new Map(states.map((s) => [s.isoCode, s.name]));
  const stateItems: Place[] = states.map((s) => ({ label: s.name, kind: "state" }));
  if (!q) {
    if (stateItems.length) return json(sorted(stateItems));
    return json(sorted(cityItems(country, stateName)).slice(0, LIMIT));
  }
  const matches = [...stateItems, ...cityItems(country, stateName)].filter((p) => p.label.toLowerCase().includes(q));
  // Exact names, then names starting with the text; states before cities; shorter names ("Miami" before
  // "Miami Beach"); then alphabetical.
  const name = (p: Place) => p.label.split(",")[0].toLowerCase();
  const rank = (p: Place) => (name(p) === q ? 0 : p.label.toLowerCase().startsWith(q) ? 1 : 2);
  matches.sort(
    (a, b) =>
      rank(a) - rank(b) ||
      Number(a.kind !== "state") - Number(b.kind !== "state") ||
      name(a).length - name(b).length ||
      a.label.localeCompare(b.label)
  );
  return json(dedupe(matches).slice(0, LIMIT));
}

function cityItems(country: string, stateName: Map<string, string>): Place[] {
  return (City.getCitiesOfCountry(country) || []).map((c) => ({
    label: stateName.get(c.stateCode) ? `${c.name}, ${stateName.get(c.stateCode)}` : c.name,
    kind: "city",
  }));
}

function sorted(items: Place[]) {
  return dedupe(items).sort((a, b) => a.label.localeCompare(b.label));
}

function dedupe(items: Place[]) {
  const seen = new Set<string>();
  return items.filter((item) => !seen.has(item.label) && seen.add(item.label));
}

function json(items: unknown) {
  return NextResponse.json(items, { headers: { "Cache-Control": "public, max-age=3600" } });
}

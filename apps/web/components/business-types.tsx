"use client";

/**
 * Business types worth targeting for website work: they depend on their website to win customers and can pay
 * for a good one. "$$$" = high-value clients (big tickets or high margins); the rest are solid, established small
 * businesses. You can still type any other business type.
 */
export const BUSINESS_TYPE_GROUPS: { group: string; types: string[] }[] = [
  {
    group: "$$$ High-paying professional services",
    types: [
      "Dentists", "Cosmetic dentists", "Orthodontists", "Plastic surgeons", "Med spas", "Dermatologists",
      "Personal injury lawyers", "Family lawyers", "Immigration lawyers", "Accountants", "Financial advisors",
      "Mortgage brokers", "Real estate agents", "Insurance agencies", "Architects", "Interior designers",
    ],
  },
  {
    group: "$$$ High-ticket home services",
    types: [
      "Roofers", "Solar installers", "HVAC contractors", "Custom home builders", "Kitchen remodelers",
      "Bathroom remodelers", "Pool builders", "Landscape designers", "Window and door installers", "Foundation repair",
    ],
  },
  {
    group: "Home services",
    types: [
      "Plumbers", "Electricians", "Pest control", "Garage door repair", "Flooring contractors", "Painters",
      "Cleaning services", "Movers", "Tree services", "Fencing contractors", "Handyman services", "Locksmiths",
    ],
  },
  {
    group: "Health and wellness",
    types: [
      "Chiropractors", "Physiotherapists", "Veterinarians", "Optometrists", "Massage therapists", "Fertility clinics",
      "Hearing aid clinics", "Aesthetic clinics", "Gyms", "Personal trainers", "Yoga studios", "Pilates studios",
    ],
  },
  {
    group: "Local businesses",
    types: [
      "Restaurants", "Cafes", "Bakeries", "Hair salons", "Barber shops", "Nail salons", "Beauty salons",
      "Tattoo studios", "Wedding venues", "Wedding photographers", "Event planners", "Florists", "Jewelry stores",
      "Boutiques", "Furniture stores", "Pet groomers",
    ],
  },
  {
    group: "Automotive and travel",
    types: [
      "Car dealerships", "Auto repair shops", "Car detailing", "Auto body shops", "Driving schools", "Boutique hotels",
      "Bed and breakfasts", "Tour operators", "Travel agencies",
    ],
  },
  {
    group: "Education and B2B",
    types: [
      "Private schools", "Tutoring centers", "Daycares", "Music schools", "Language schools", "Marketing agencies",
      "IT services", "Staffing agencies", "Manufacturers", "Commercial cleaning", "Logistics companies",
    ],
  },
];

/** A dropdown of suggested business types; picking one adds it (typing your own still works next to it). */
export function BusinessTypeSelect({ selected, onAdd }: { selected: string[]; onAdd: (type: string) => void }) {
  return (
    <select
      className="h-10 rounded-md border border-border bg-card px-3 text-sm"
      value=""
      aria-label="Choose a business type"
      onChange={(e) => {
        if (e.target.value) onAdd(e.target.value);
      }}
    >
      <option value="">Choose from the list...</option>
      {BUSINESS_TYPE_GROUPS.map((g) => (
        <optgroup key={g.group} label={g.group}>
          {g.types.map((t) => (
            <option key={t} value={t} disabled={selected.includes(t)}>
              {t}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

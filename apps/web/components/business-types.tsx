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

/**
 * Agency outreach: agencies that sell web, Shopify and marketing work to their clients and could hand the delivery
 * to a white-label partner.
 */
export const AGENCY_TYPE_GROUPS: { group: string; types: string[] }[] = [
  {
    group: "Shopify and ecommerce",
    types: [
      "Shopify agency", "Shopify Plus agency", "Shopify developers", "Ecommerce agency", "Ecommerce marketing agency",
      "DTC marketing agency", "BigCommerce agency", "WooCommerce developers",
    ],
  },
  {
    group: "Web design and development",
    types: [
      "Web design agency", "Web development company", "WordPress agency", "Webflow agency", "UX design agency",
      "CRO agency", "Branding agency", "Creative agency",
    ],
  },
  {
    group: "Marketing agencies",
    types: [
      "Marketing agency", "Digital marketing agency", "SEO agency", "PPC agency", "Social media marketing agency",
      "Advertising agency", "Email marketing agency", "Growth marketing agency", "Content marketing agency",
    ],
  },
];

/** A dropdown of suggested business types; picking one adds it (typing your own still works next to it). */
export function BusinessTypeSelect({
  selected,
  onAdd,
  groups = BUSINESS_TYPE_GROUPS,
}: {
  selected: string[];
  onAdd: (type: string) => void;
  groups?: { group: string; types: string[] }[];
}) {
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
      {groups.map((g) => (
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

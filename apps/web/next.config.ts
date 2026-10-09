import type { NextConfig } from "next";
import path from "path";

const apiOrigin = (process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(
  /\/$/,
  ""
);

const nextConfig: NextConfig = {
  turbopack: {
    root: path.join(__dirname),
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
  webpack: (config) => {
    // Avoid webpack pack-cache OOMs when the native SWC binary is missing.
    config.cache = false;
    return config;
  },
};

export default nextConfig;

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  // the dev badge sits bottom-left, on top of the graph's zoom controls
  devIndicators: false,
};

export default nextConfig;

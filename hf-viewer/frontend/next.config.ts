import type { NextConfig } from "next";

const BACKEND = process.env.HF_VIEWER_BACKEND ?? "http://localhost:8100";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

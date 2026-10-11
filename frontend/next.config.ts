import type { NextConfig } from "next";
const config: NextConfig = {
  async rewrites() {
    const base = process.env.FULL_BACKEND_URL || "http://127.0.0.1:8100";
    const url = new URL(base);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
      throw new Error("FULL_BACKEND_URL must be a plain HTTP(S) origin without credentials.");
    }
    return [{ source: "/api/:path*", destination: url.origin + "/api/:path*" }];
  },
};
export default config;

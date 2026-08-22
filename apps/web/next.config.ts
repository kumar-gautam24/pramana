import type { NextConfig } from "next";

const config: NextConfig = {
  // `standalone` emits a self-contained server with only the modules actually imported,
  // which is what lets the Dockerfile ship a runtime stage with no node_modules copy.
  output: "standalone",
  reactStrictMode: true,
};

export default config;

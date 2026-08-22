import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  // `@/` is how every module in this app imports its siblings (see tsconfig's paths), so a
  // test that could not resolve it would be testing a different import graph than the one
  // `next build` compiles.
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  esbuild: { jsx: "automatic" },
  test: {
    // Node, not a browser: the two things under test are a stream parser and a component
    // rendered to a string. Neither touches a document, and a jsdom environment would be a
    // dependency bought for nothing.
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    // `gateway.ts` refuses to build a URL without this and says so loudly, which is the
    // right behaviour for a console that would otherwise ship pointing at nothing. Tests
    // stub `fetch`, so the value only has to exist.
    env: { NEXT_PUBLIC_GATEWAY_URL: "http://gateway.test" },
  },
});

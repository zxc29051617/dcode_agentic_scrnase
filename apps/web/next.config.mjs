/** @type {import('next').NextConfig} */
const nextConfig = {
  // No `env:` block that would inline GATEWAY_URL or a model key into the
  // client bundle. Every server-only value is read from `process.env` inside
  // Server Components and Route Handlers only — see lib/gateway.ts and
  // app/api/copilotkit/route.ts. A key that must reach the bundle would need
  // the NEXT_PUBLIC_ prefix, and none of the values here carry it.
  serverExternalPackages: ["@copilotkit/runtime", "@ai-sdk/google-vertex"],
};

export default nextConfig;

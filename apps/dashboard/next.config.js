/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a self-contained server bundle so the runtime image only needs the
  // traced dependencies instead of the full node_modules tree.
  output: 'standalone',
};

module.exports = nextConfig;

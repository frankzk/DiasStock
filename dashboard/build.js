const fs = require("fs");
const path = require("path");

const root = __dirname;
const dist = path.join(root, "dist");

fs.rmSync(dist, { recursive: true, force: true });
fs.mkdirSync(dist, { recursive: true });

for (const file of ["index.html", "README.md", "supabase-schema.sql", "product-funnel-schema.sql"]) {
  fs.copyFileSync(path.join(root, file), path.join(dist, file));
}

const config = {
  SUPABASE_URL: process.env.SUPABASE_URL || "",
  SUPABASE_ANON_KEY:
    process.env.SUPABASE_ANON_KEY ||
    process.env.SUPABASE_PUBLISHABLE_KEY ||
    "",
};

fs.writeFileSync(
  path.join(dist, "config.js"),
  `window.DIASSTOCK_CONFIG = ${JSON.stringify(config, null, 2)};\n`,
  "utf8"
);

console.log("Dashboard built to dashboard/dist");

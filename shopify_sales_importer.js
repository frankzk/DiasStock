const fs = require("fs");
const path = require("path");

const SALES_TABLE = "shopify_sales_daily";
const RUNS_TABLE = "shopify_sales_import_runs";
const DEFAULT_API_VERSION = "2026-04";
const DEFAULT_TIMEZONE = "America/Lima";
const DEFAULT_DAYS = 7;

async function importShopifySales(options = {}) {
  const days = normalizePositiveInt(options.days, DEFAULT_DAYS);
  const timezone = options.timezone || process.env.SHOPIFY_SALES_TIMEZONE || DEFAULT_TIMEZONE;
  const runDate = options.runDate || todayInTimeZone(timezone);
  const endDate = options.endDate || addDaysIso(runDate, -1);
  const startDate = addDaysIso(endDate, -(days - 1));
  const storeKeyFilter = normalizeKey(options.storeKey || "");
  const stores = discoverStores()
    .filter((store) => !storeKeyFilter || normalizeKey(store.key) === storeKeyFilter)
    .filter((store) => store.shopifyUrl && store.shopifyToken);

  if (!stores.length) {
    return {
      ok: true,
      dryRun: Boolean(options.dryRun),
      table: SALES_TABLE,
      days,
      startDate,
      endDate,
      stores: 0,
      rows: 0,
      skippedStores: discoverStores().length,
      message: "No hay tiendas con STORE_XX_SHOPIFY_URL y STORE_XX_SHOPIFY_TOKEN.",
      results: [],
      errors: [],
    };
  }

  const allRows = [];
  const runRows = [];
  const results = [];
  const errors = [];

  for (const store of stores) {
    try {
      const result = await fetchStoreSales(store, { days, startDate, endDate, timezone });
      allRows.push(...result.rows);
      runRows.push({
        run_date: runDate,
        store_key: store.key,
        store_name: store.name,
        shop_domain: result.shopDomain,
        window_start: startDate,
        window_end: endDate,
        rows_imported: result.rows.length,
        units_sold: result.unitsSold,
        orders_count: result.orders,
        updated_at: new Date().toISOString(),
      });
      results.push({
        storeKey: store.key,
        storeName: store.name,
        rows: result.rows.length,
        unitsSold: result.unitsSold,
        orders: result.orders,
        skippedLineItems: result.skippedLineItems,
      });
    } catch (error) {
      errors.push({
        storeKey: store.key,
        storeName: store.name,
        message: error && error.message ? error.message : String(error),
      });
    }
  }

  if (!options.dryRun) {
    const { url, key } = getSupabaseConfig();
    for (const runRow of runRows) {
      await deleteSalesWindow(url, key, runRow.store_key, startDate, endDate);
      if (runDate > endDate) {
        await deleteSalesWindow(url, key, runRow.store_key, runDate, runDate);
      }
    }
    if (allRows.length) await upsertSalesRows(url, key, allRows);
    if (runRows.length) await upsertRunRows(url, key, runRows);
  }

  return {
    ok: errors.length === 0,
    dryRun: Boolean(options.dryRun),
    table: SALES_TABLE,
    runsTable: RUNS_TABLE,
    days,
    runDate,
    startDate,
    endDate,
    stores: stores.length,
    rows: allRows.length,
    unitsSold: allRows.reduce((sum, row) => sum + Number(row.units_sold || 0), 0),
    results,
    errors,
  };
}

async function fetchStoreSales(store, options) {
  const apiVersion = process.env.SHOPIFY_API_VERSION || DEFAULT_API_VERSION;
  const domain = normalizeShopifyDomain(store.shopifyUrl);
  const startUtc = zonedDateTimeToUtcIso(options.startDate, options.timezone);
  const endUtc = zonedDateTimeToUtcIso(addDaysIso(options.endDate, 1), options.timezone);
  let url = `https://${domain}/admin/api/${apiVersion}/orders.json`;
  let params = {
    status: "any",
    created_at_min: startUtc,
    created_at_max: endUtc,
    limit: "250",
    fields: "id,name,created_at,processed_at,cancelled_at,line_items",
  };

  const byDateSku = new Map();
  let orders = 0;
  let skippedLineItems = 0;

  while (url) {
    const response = await fetch(`${url}${params ? `?${new URLSearchParams(params)}` : ""}`, {
      headers: {
        "X-Shopify-Access-Token": store.shopifyToken,
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      throw new Error(`Shopify ${store.key} HTTP ${response.status}: ${await response.text()}`);
    }

    const data = await response.json();
    for (const order of data.orders || []) {
      if (order.cancelled_at) continue;
      const saleDate = formatDateInTimeZone(order.processed_at || order.created_at, options.timezone);
      if (!dateInRange(saleDate, options.startDate, options.endDate)) continue;
      orders += 1;

      for (const item of order.line_items || []) {
        const sku = String(item.sku || "").trim();
        const productName = String(item.name || "").trim();
        const quantity = Number(item.quantity ?? item.current_quantity ?? 0);
        if (!sku || !quantity) {
          skippedLineItems += 1;
          continue;
        }

        const key = `${saleDate}||${store.key}||${sku}`;
        if (!byDateSku.has(key)) {
          byDateSku.set(key, {
            sale_date: saleDate,
            store_key: store.key,
            store_name: store.name,
            shop_domain: domain,
            sku,
            product_name: productName,
            units_sold: 0,
            orders_count: 0,
            line_items_count: 0,
            updated_at: new Date().toISOString(),
            _orders: new Set(),
          });
        }

        const row = byDateSku.get(key);
        row.units_sold += quantity;
        row.line_items_count += 1;
        row._orders.add(order.id || order.name || `${saleDate}-${sku}`);
        if (!row.product_name && productName) row.product_name = productName;
      }
    }

    const nextUrl = parseNextLink(response.headers.get("link") || "");
    url = nextUrl;
    params = null;
  }

  const rows = Array.from(byDateSku.values()).map((row) => {
    row.orders_count = row._orders.size;
    delete row._orders;
    return row;
  });

  return {
    rows,
    shopDomain: domain,
    orders,
    skippedLineItems,
    unitsSold: rows.reduce((sum, row) => sum + Number(row.units_sold || 0), 0),
  };
}

function discoverStores() {
  const keys = Object.keys(process.env)
    .filter((key) => key.startsWith("STORE_") && key.endsWith("_NAME"))
    .map((key) => key.slice("STORE_".length, -"NAME".length - 1))
    .sort();

  return keys.map((key) => ({
    key,
    name: process.env[`STORE_${key}_NAME`] || key,
    shopifyUrl: process.env[`STORE_${key}_SHOPIFY_URL`] || "",
    shopifyToken: process.env[`STORE_${key}_SHOPIFY_TOKEN`] || "",
  }));
}

async function upsertSalesRows(url, key, rows) {
  for (const chunk of chunkRows(rows, 500)) {
    const response = await fetch(`${url}/rest/v1/${SALES_TABLE}?on_conflict=sale_date,store_key,sku`, {
      method: "POST",
      headers: supabaseHeaders(key, {
        Prefer: "resolution=merge-duplicates,return=minimal",
      }),
      body: JSON.stringify(chunk),
    });
    if (!response.ok) {
      throw new Error(`Supabase ${SALES_TABLE} HTTP ${response.status}: ${await response.text()}`);
    }
  }
}

async function upsertRunRows(url, key, rows) {
  for (const chunk of chunkRows(rows, 500)) {
    const response = await fetch(`${url}/rest/v1/${RUNS_TABLE}?on_conflict=run_date,store_key`, {
      method: "POST",
      headers: supabaseHeaders(key, {
        Prefer: "resolution=merge-duplicates,return=minimal",
      }),
      body: JSON.stringify(chunk),
    });
    if (!response.ok) {
      throw new Error(`Supabase ${RUNS_TABLE} HTTP ${response.status}: ${await response.text()}`);
    }
  }
}

async function deleteSalesWindow(url, key, storeKey, startDate, endDate) {
  const params = new URLSearchParams({
    store_key: `eq.${storeKey}`,
    sale_date: `gte.${startDate}`,
  });
  params.append("sale_date", `lte.${endDate}`);
  const response = await fetch(`${url}/rest/v1/${SALES_TABLE}?${params}`, {
    method: "DELETE",
    headers: supabaseHeaders(key, {
      Prefer: "return=minimal",
    }),
  });
  if (!response.ok) {
    throw new Error(`Supabase delete ${SALES_TABLE} HTTP ${response.status}: ${await response.text()}`);
  }
}

function getSupabaseConfig() {
  const url = String(process.env.SUPABASE_URL || "").replace(/\/$/, "");
  const key = process.env.SUPABASE_KEY || process.env.SUPABASE_SERVICE_ROLE_KEY || "";
  if (!url || !key) throw new Error("Faltan SUPABASE_URL y SUPABASE_KEY/SUPABASE_SERVICE_ROLE_KEY.");
  return { url, key };
}

function supabaseHeaders(key, extra = {}) {
  return {
    apikey: key,
    Authorization: `Bearer ${key}`,
    "Content-Type": "application/json",
    ...extra,
  };
}

function loadEnv(envPath = path.join(process.cwd(), ".env")) {
  if (!fs.existsSync(envPath)) return;
  const lines = fs.readFileSync(envPath, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match || process.env[match[1]]) continue;
    process.env[match[1]] = match[2].replace(/^['"]|['"]$/g, "");
  }
}

function parseCliArgs(argv) {
  const args = { dryRun: false, days: DEFAULT_DAYS, storeKey: "", endDate: "", runDate: "", help: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") args.dryRun = true;
    else if (arg === "--days") args.days = Number(argv[++index]);
    else if (arg.startsWith("--days=")) args.days = Number(arg.split("=", 2)[1]);
    else if (arg === "--store") args.storeKey = argv[++index] || "";
    else if (arg.startsWith("--store=")) args.storeKey = arg.split("=", 2)[1] || "";
    else if (arg === "--end-date") args.endDate = argv[++index] || "";
    else if (arg.startsWith("--end-date=")) args.endDate = arg.split("=", 2)[1] || "";
    else if (arg === "--run-date") args.runDate = argv[++index] || "";
    else if (arg.startsWith("--run-date=")) args.runDate = arg.split("=", 2)[1] || "";
    else if (arg === "--help" || arg === "-h") args.help = true;
  }
  return args;
}

function printHelp() {
  console.log(`Uso:
  node import_shopify_sales_to_supabase.js [--dry-run] [--days 7] [--store KA] [--end-date YYYY-MM-DD]

Importa ventas diarias Shopify por SKU a Supabase (${SALES_TABLE}).
Por defecto excluye hoy: si corre el 2026-05-03, importa 2026-04-26..2026-05-02.
El dashboard suma los ultimos 7 dias cerrados por store_key + sku.
`);
}

function normalizeShopifyDomain(value) {
  return String(value || "")
    .replace(/^https?:\/\//i, "")
    .replace(/\/.*$/, "")
    .trim();
}

function parseNextLink(linkHeader) {
  if (!linkHeader) return "";
  for (const part of linkHeader.split(",")) {
    const trimmed = part.trim();
    if (trimmed.includes('rel="next"')) {
      return trimmed.split(";")[0].trim().replace(/^<|>$/g, "");
    }
  }
  return "";
}

function todayInTimeZone(timezone) {
  return formatDateInTimeZone(new Date().toISOString(), timezone);
}

function formatDateInTimeZone(value, timezone) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${map.year}-${map.month}-${map.day}`;
}

function zonedDateTimeToUtcIso(dateValue, timezone) {
  const [year, month, day] = String(dateValue || "").split("-").map(Number);
  const utcGuess = Date.UTC(year, month - 1, day, 0, 0, 0);
  const zoned = getZonedParts(new Date(utcGuess), timezone);
  const zonedAsUtc = Date.UTC(zoned.year, zoned.month - 1, zoned.day, zoned.hour, zoned.minute, zoned.second);
  const offsetMs = zonedAsUtc - utcGuess;
  return new Date(utcGuess - offsetMs).toISOString();
}

function getZonedParts(date, timezone) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(date);
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return {
    year: Number(map.year),
    month: Number(map.month),
    day: Number(map.day),
    hour: Number(map.hour),
    minute: Number(map.minute),
    second: Number(map.second),
  };
}

function addDaysIso(dateValue, delta) {
  const [year, month, day] = String(dateValue || "").split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + delta));
  return date.toISOString().slice(0, 10);
}

function dateInRange(dateValue, startDate, endDate) {
  return dateValue >= startDate && dateValue <= endDate;
}

function chunkRows(rows, size) {
  const chunks = [];
  for (let index = 0; index < rows.length; index += size) {
    chunks.push(rows.slice(index, index + size));
  }
  return chunks;
}

function normalizePositiveInt(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

function normalizeKey(value) {
  return String(value || "").trim().toLowerCase();
}

module.exports = {
  SALES_TABLE,
  RUNS_TABLE,
  importShopifySales,
  loadEnv,
  parseCliArgs,
  printHelp,
};

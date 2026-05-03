const fs = require("fs");
const path = require("path");

const SOURCE_TABLE = "ad_sheet_sources";
const MAPPING_TABLE = "campaign_sku_mappings";
const DAILY_TABLE = "ad_campaign_daily";

const HEADER_ALIASES = {
  date: ["fecha", "date", "day"],
  campaign_name: ["campaign name", "campaign", "campana"],
  spend: ["spend", "amount spent", "importe gastado", "gasto"],
  account_name: ["account name", "cuenta", "ad account"],
  clicks: ["clicks", "clics"],
  currency: ["account currency", "currency", "moneda"],
  cpc: ["cpc"],
  cpm: ["cpm"],
  ctr: ["ctr"],
  impressions: ["impressions", "impresiones"],
};

main().catch((error) => {
  console.error(error.message || error);
  process.exitCode = 1;
});

async function main() {
  loadEnv();
  const args = parseArgs(process.argv.slice(2));
  const { url, key } = getSupabaseConfig();
  const sources = await loadSources(url, key, args.sourceId);
  if (!sources.length) {
    console.log("No hay fuentes Ads activas registradas.");
    return;
  }

  const mappings = await loadMappings(url, key);
  const payloadItems = args.jsonInput ? loadJsonInput(args.jsonInput) : null;
  let rows = [];

  for (const source of sources) {
    let values = valuesForSource(source, payloadItems);
    if (!values) values = await fetchSheetValues(source);
    const parsed = parseValues(source, values, mappings);
    rows = rows.concat(parsed);
    console.log(`  ${source.sheet_name}: ${parsed.length} fila(s) parseadas`);
  }

  rows = aggregateRows(rows);
  console.log(`Filas Ads listas: ${rows.length}`);

  if (args.dryRun) {
    const mapped = rows.filter((row) => row.sku).length;
    const spend = rows.reduce((sum, row) => sum + Number(row.spend || 0), 0);
    console.log(`Dry-run: ${mapped} fila(s) con SKU, gasto total ${spend.toFixed(2)}`);
    return;
  }

  await upsertDailyRows(url, key, rows);
  console.log("Listo: gasto Ads importado en Supabase.");
}

function parseArgs(argv) {
  const args = { dryRun: false, sourceId: null, jsonInput: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") args.dryRun = true;
    else if (arg === "--source-id") args.sourceId = Number(argv[++index]);
    else if (arg.startsWith("--source-id=")) args.sourceId = Number(arg.split("=", 2)[1]);
    else if (arg === "--json-input") args.jsonInput = argv[++index] || "";
    else if (arg.startsWith("--json-input=")) args.jsonInput = arg.split("=", 2)[1] || "";
  }
  return args;
}

function loadEnv() {
  const envPath = path.join(process.cwd(), ".env");
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

function getSupabaseConfig() {
  const url = (process.env.SUPABASE_URL || "").replace(/\/$/, "");
  const key = process.env.SUPABASE_KEY || process.env.SUPABASE_ANON_KEY || process.env.SUPABASE_PUBLISHABLE_KEY || "";
  if (!url || !key) throw new Error("Faltan SUPABASE_URL y SUPABASE_KEY/SUPABASE_ANON_KEY.");
  return { url, key };
}

function supabaseHeaders(key) {
  return {
    apikey: key,
    Authorization: `Bearer ${key}`,
    "Content-Type": "application/json",
  };
}

async function loadSources(url, key, sourceId) {
  const params = new URLSearchParams({
    select: "*",
    active: "eq.true",
    order: "sheet_name.asc",
  });
  if (sourceId) params.set("id", `eq.${sourceId}`);
  const response = await fetch(`${url}/rest/v1/${SOURCE_TABLE}?${params}`, {
    headers: supabaseHeaders(key),
  });
  if (!response.ok) throw new Error(`Supabase sources HTTP ${response.status}: ${await response.text()}`);
  return response.json();
}

async function loadMappings(url, key) {
  const response = await fetch(`${url}/rest/v1/${MAPPING_TABLE}?select=*`, {
    headers: supabaseHeaders(key),
  });
  if (!response.ok) throw new Error(`Supabase mappings HTTP ${response.status}: ${await response.text()}`);
  const rows = await response.json();
  const map = new Map();
  for (const row of rows) {
    map.set(`${Number(row.source_id)}::${normalizeText(row.campaign_name)}`, row);
  }
  return map;
}

function loadJsonInput(filePath) {
  const data = JSON.parse(fs.readFileSync(filePath, "utf8"));
  return Array.isArray(data) ? data : data.sources || [];
}

function valuesForSource(source, payloadItems) {
  if (!payloadItems) return null;
  for (const item of payloadItems) {
    const itemSource = item.source || {};
    if (Number(itemSource.id || 0) === Number(source.id)) return item.values || [];
    if (itemSource.sheet_name === source.sheet_name) return item.values || [];
  }
  return null;
}

async function fetchSheetValues(source) {
  const spreadsheetId = source.spreadsheet_id || extractSpreadsheetId(source.spreadsheet_url || "");
  const sheetName = source.sheet_name;
  const url = `https://docs.google.com/spreadsheets/d/${spreadsheetId}/gviz/tq?tqx=out:csv&sheet=${encodeURIComponent(sheetName)}`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Google Sheets HTTP ${response.status} en ${sheetName}`);
  const text = (await response.text()).replace(/^\uFEFF/, "");
  const values = parseCsv(text).filter((row) => row.some((cell) => String(cell).trim()));
  if (!values.length || !hasExpectedHeaders(values[0])) {
    throw new Error(
      `No pude leer headers Ads en '${sheetName}'. Comparte el Sheet por enlace o usa el conector Google Drive de Codex.`
    );
  }
  return values;
}

function parseValues(source, values, mappings) {
  if (!values.length) return [];
  const headers = buildHeaderMap(values[0]);
  const missing = ["date", "campaign_name", "spend"].filter((name) => !(name in headers));
  if (missing.length) throw new Error(`${source.sheet_name}: faltan columnas ${missing.join(", ")}`);

  const rows = [];
  for (const valueRow of values.slice(1)) {
    const campaign = cell(valueRow, headers.campaign_name);
    if (!campaign) continue;
    const spendDate = parseDate(cell(valueRow, headers.date));
    if (!spendDate) continue;

    const sourceId = Number(source.id);
    const mapping = mappings.get(`${sourceId}::${normalizeText(campaign)}`) || {};
    const spend = parseNumber(cell(valueRow, headers.spend), 0);
    const clicks = Math.trunc(parseNumber(cell(valueRow, headers.clicks), 0));
    const impressions = Math.trunc(parseNumber(cell(valueRow, headers.impressions), 0));

    rows.push({
      spend_date: spendDate,
      spreadsheet_id: source.spreadsheet_id || extractSpreadsheetId(source.spreadsheet_url || ""),
      sheet_name: source.sheet_name,
      source_id: sourceId,
      store_key: source.store_key || mapping.store_key || "",
      store_name: source.store_name || mapping.store_name || "",
      ad_account_name: cell(valueRow, headers.account_name) || source.ad_account_name || "",
      campaign_name: campaign,
      sku: mapping.sku || "",
      product_name: mapping.product_name || "",
      currency: cell(valueRow, headers.currency) || "USD",
      spend: round(spend, 2),
      clicks,
      cpc: parseNumber(cell(valueRow, headers.cpc), null),
      cpm: parseNumber(cell(valueRow, headers.cpm), null),
      ctr: parseNumber(cell(valueRow, headers.ctr), null),
      impressions,
    });
  }
  return rows;
}

function aggregateRows(rows) {
  const grouped = new Map();
  for (const row of rows) {
    const key = [row.spend_date, row.spreadsheet_id, row.sheet_name, normalizeText(row.campaign_name)].join("::");
    if (!grouped.has(key)) {
      grouped.set(key, { ...row });
      continue;
    }
    const current = grouped.get(key);
    current.spend = round(Number(current.spend || 0) + Number(row.spend || 0), 2);
    current.clicks = Number(current.clicks || 0) + Number(row.clicks || 0);
    current.impressions = Number(current.impressions || 0) + Number(row.impressions || 0);
    if (row.sku) {
      current.sku = row.sku;
      current.product_name = row.product_name || "";
    }
  }

  for (const row of grouped.values()) {
    const spend = Number(row.spend || 0);
    row.cpc = row.clicks ? round(spend / row.clicks, 4) : row.cpc;
    row.cpm = row.impressions ? round((spend / row.impressions) * 1000, 4) : row.cpm;
    row.ctr = row.impressions ? round(row.clicks / row.impressions, 6) : row.ctr;
  }
  return Array.from(grouped.values()).sort((a, b) => (
    `${a.spend_date} ${a.sheet_name} ${a.campaign_name}`.localeCompare(`${b.spend_date} ${b.sheet_name} ${b.campaign_name}`, "es")
  ));
}

async function upsertDailyRows(url, key, rows) {
  if (!rows.length) return;
  const params = new URLSearchParams({
    on_conflict: "spend_date,spreadsheet_id,sheet_name,campaign_name",
  });
  const response = await fetch(`${url}/rest/v1/${DAILY_TABLE}?${params}`, {
    method: "POST",
    headers: {
      ...supabaseHeaders(key),
      Prefer: "resolution=merge-duplicates,return=minimal",
    },
    body: JSON.stringify(rows),
  });
  if (!response.ok) throw new Error(`Supabase daily HTTP ${response.status}: ${await response.text()}`);
}

function buildHeaderMap(headers) {
  const normalized = new Map(headers.map((value, index) => [normalizeText(value), index]));
  const result = {};
  for (const [field, aliases] of Object.entries(HEADER_ALIASES)) {
    for (const alias of aliases) {
      const key = normalizeText(alias);
      if (normalized.has(key)) {
        result[field] = normalized.get(key);
        break;
      }
    }
  }
  return result;
}

function hasExpectedHeaders(headers) {
  const headerMap = buildHeaderMap(headers);
  return ["date", "campaign_name", "spend"].every((name) => name in headerMap);
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let value = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (quoted) {
      if (char === '"' && next === '"') {
        value += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        value += char;
      }
      continue;
    }
    if (char === '"') quoted = true;
    else if (char === ",") {
      row.push(value);
      value = "";
    } else if (char === "\n") {
      row.push(value);
      rows.push(row);
      row = [];
      value = "";
    } else if (char !== "\r") {
      value += char;
    }
  }
  row.push(value);
  rows.push(row);
  return rows;
}

function cell(row, index) {
  if (index === undefined || index === null || index >= row.length) return "";
  return row[index] === null || row[index] === undefined ? "" : String(row[index]).trim();
}

function parseDate(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (/^\d+(\.\d+)?$/.test(text)) {
    const serial = Math.trunc(Number(text));
    const date = new Date(Date.UTC(1899, 11, 30 + serial));
    return date.toISOString().slice(0, 10);
  }
  const parts = text.split(/[/-]/).map(Number);
  if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return "";
  if (String(parts[0]).length === 4) {
    return isoDate(parts[0], parts[1], parts[2]);
  }
  if (parts[1] > 12) {
    return isoDate(parts[2], parts[0], parts[1]);
  }
  return isoDate(parts[2], parts[1], parts[0]);
}

function isoDate(year, month, day) {
  if (!year || !month || !day) return "";
  const date = new Date(Date.UTC(year, month - 1, day));
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().slice(0, 10);
}

function parseNumber(value, fallback) {
  let text = String(value || "").trim();
  if (!text) return fallback;
  text = text.replace(/%|\$|USD/g, "").trim();
  if (text.includes(",") && !text.includes(".")) text = text.replace(",", ".");
  else text = text.replace(/,/g, "");
  const number = Number(text);
  return Number.isFinite(number) ? number : fallback;
}

function normalizeText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function extractSpreadsheetId(value) {
  const match = String(value || "").match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/);
  return match ? match[1] : String(value || "").trim();
}

function round(value, digits) {
  const factor = 10 ** digits;
  return Math.round(Number(value || 0) * factor) / factor;
}

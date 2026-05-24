const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { getSheetValues, hasGoogleServiceAccountConfig } = require("./google_sheets_api");

const SOURCE_TABLE = "ad_sheet_sources";
const MAPPING_TABLE = "campaign_sku_mappings";
const DAILY_TABLE = "ad_campaign_daily";

const DEFAULT_SOURCES_CACHE = path.join(process.cwd(), "outputs", "ads_sources_cache.json");
const DEFAULT_MAPPINGS_CACHE = path.join(process.cwd(), "outputs", "ads_mappings_cache.json");

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

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printHelp();
    return;
  }
  const result = await importAds(args);
  if (!result.ok) process.exitCode = 1;
}

async function importAds(options = {}) {
  if (options.loadEnv !== false) loadEnv();
  const { url, key } = getSupabaseConfig();
  const sources = await loadSources(url, key, options.sourceId, options.sourcesInput);
  if (!sources.length) {
    console.log("No hay fuentes Ads activas registradas.");
    return {
      ok: true,
      dryRun: Boolean(options.dryRun),
      table: DAILY_TABLE,
      sources: 0,
      rows: 0,
      mappedRows: 0,
      spend: 0,
      errors: [],
    };
  }

  const mappings = await loadMappings(url, key, options.mappingsInput);
  const payloadItems = options.jsonInput ? loadJsonInput(options.jsonInput) : null;
  let rows = [];
  const errors = [];

  for (const source of sources) {
    try {
      let values = valuesForSource(source, payloadItems);
      if (!values) values = await fetchSheetValues(source);
      const parsed = parseValues(source, values, mappings);
      rows = rows.concat(parsed);
      console.log(`  ${source.sheet_name}: ${parsed.length} fila(s) parseadas`);
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      errors.push({ source, message });
      console.error(`  ${source.sheet_name}: error al leer (${message})`);
    }
  }

  rows = aggregateRows(rows);
  console.log(`Filas Ads listas: ${rows.length}`);
  const summary = summarizeRows(rows);

  if (options.rowsOutput) {
    ensureOutputsDir();
    const target = path.isAbsolute(options.rowsOutput) ? options.rowsOutput : path.join(process.cwd(), options.rowsOutput);
    writeJsonFile(target, { rows });
    console.log(`Export: ${rows.length} fila(s) escritas en ${target}`);
    reportErrors(errors);
    return {
      ok: errors.length === 0,
      dryRun: true,
      table: DAILY_TABLE,
      sources: sources.length,
      rows: rows.length,
      ...summary,
      rowsOutput: target,
      errors: serializeErrors(errors),
    };
  }

  if (options.dryRun) {
    console.log(`Dry-run: ${summary.mappedRows} fila(s) con SKU, gasto total ${summary.spend.toFixed(2)}`);
    reportErrors(errors);
    return {
      ok: errors.length === 0,
      dryRun: true,
      table: DAILY_TABLE,
      sources: sources.length,
      rows: rows.length,
      ...summary,
      errors: serializeErrors(errors),
    };
  }

  await upsertDailyRows(url, key, rows);
  console.log(`Listo: ${rows.length} fila(s) upsert en Supabase.`);
  reportErrors(errors);
  return {
    ok: errors.length === 0,
    dryRun: false,
    table: DAILY_TABLE,
    sources: sources.length,
    rows: rows.length,
    ...summary,
    errors: serializeErrors(errors),
  };
}

function parseArgs(argv) {
  const args = { dryRun: false, sourceId: null, jsonInput: "", sourcesInput: "", mappingsInput: "", rowsOutput: "", help: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") args.dryRun = true;
    else if (arg === "--source-id") args.sourceId = Number(argv[++index]);
    else if (arg.startsWith("--source-id=")) args.sourceId = Number(arg.split("=", 2)[1]);
    else if (arg === "--json-input") args.jsonInput = argv[++index] || "";
    else if (arg.startsWith("--json-input=")) args.jsonInput = arg.split("=", 2)[1] || "";
    else if (arg === "--sources-input") args.sourcesInput = argv[++index] || "";
    else if (arg.startsWith("--sources-input=")) args.sourcesInput = arg.split("=", 2)[1] || "";
    else if (arg === "--mappings-input") args.mappingsInput = argv[++index] || "";
    else if (arg.startsWith("--mappings-input=")) args.mappingsInput = arg.split("=", 2)[1] || "";
    else if (arg === "--rows-output") args.rowsOutput = argv[++index] || "";
    else if (arg.startsWith("--rows-output=")) args.rowsOutput = arg.split("=", 2)[1] || "";
    else if (arg === "--help" || arg === "-h") args.help = true;
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
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY
    || process.env.SUPABASE_KEY
    || process.env.SUPABASE_ANON_KEY
    || process.env.SUPABASE_PUBLISHABLE_KEY
    || "";
  if (!url || !key) {
    throw new Error("Faltan SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY/SUPABASE_ANON_KEY.");
  }
  return { url, key };
}

function supabaseHeaders(key) {
  return {
    apikey: key,
    Authorization: `Bearer ${key}`,
    "Content-Type": "application/json",
  };
}

async function loadSources(url, key, sourceId, sourcesInput) {
  if (sourcesInput) {
    const rows = loadJsonArray(sourcesInput, "sources");
    const filtered = rows.filter((row) => row && row.active === true);
    const selected = sourceId ? filtered.filter((row) => Number(row.id) === Number(sourceId)) : filtered;
    const ready = selected.filter((row) => String(row.store_key || "").trim());
    const skipped = selected.length - ready.length;
    if (skipped) console.log(`  ${skipped} fuente(s) Ads omitidas sin tienda asignada.`);
    return ready.sort((a, b) => String(a.sheet_name || "").localeCompare(String(b.sheet_name || ""), "es"));
  }
  const params = new URLSearchParams({
    select: "*",
    active: "eq.true",
    order: "sheet_name.asc",
  });
  if (sourceId) params.set("id", `eq.${sourceId}`);
  let response;
  try {
    response = await fetch(`${url}/rest/v1/${SOURCE_TABLE}?${params}`, {
      headers: supabaseHeaders(key),
    });
  } catch (error) {
    if (fs.existsSync(DEFAULT_SOURCES_CACHE)) {
      console.log(`WARN: No se pudo leer ${SOURCE_TABLE} desde Supabase (${error && error.message ? error.message : error}). Usando cache local: ${DEFAULT_SOURCES_CACHE}`);
      const cached = loadJsonArray(DEFAULT_SOURCES_CACHE, "sources");
      const filtered = cached.filter((row) => row && row.active === true);
      const selected = sourceId ? filtered.filter((row) => Number(row.id) === Number(sourceId)) : filtered;
      const ready = selected.filter((row) => String(row.store_key || "").trim());
      const skipped = selected.length - ready.length;
      if (skipped) console.log(`  ${skipped} fuente(s) Ads omitidas sin tienda asignada.`);
      return ready.sort((a, b) => String(a.sheet_name || "").localeCompare(String(b.sheet_name || ""), "es"));
    }
    throw new Error(
      `No se pudo conectar a Supabase para leer ${SOURCE_TABLE} (${error && error.message ? error.message : error}). `
      + `Si estás en un entorno sin red, usa --sources-input o crea un cache en ${DEFAULT_SOURCES_CACHE}.`
    );
  }
  if (!response.ok) throw new Error(`Supabase sources HTTP ${response.status}: ${await response.text()}`);
  const rows = await response.json();
  ensureOutputsDir();
  writeJsonFile(DEFAULT_SOURCES_CACHE, { sources: rows });
  const ready = rows.filter((row) => String(row.store_key || "").trim());
  const skipped = rows.length - ready.length;
  if (skipped) console.log(`  ${skipped} fuente(s) Ads omitidas sin tienda asignada.`);
  return ready;
}

async function loadMappings(url, key, mappingsInput) {
  const rows = mappingsInput ? loadJsonArray(mappingsInput, "mappings") : await fetchMappings(url, key);
  return buildMappingIndex(rows);
}

async function fetchMappings(url, key) {
  let response;
  try {
    response = await fetch(`${url}/rest/v1/${MAPPING_TABLE}?select=*`, {
      headers: supabaseHeaders(key),
    });
  } catch (error) {
    if (fs.existsSync(DEFAULT_MAPPINGS_CACHE)) {
      console.log(`WARN: No se pudo leer ${MAPPING_TABLE} desde Supabase (${error && error.message ? error.message : error}). Usando cache local: ${DEFAULT_MAPPINGS_CACHE}`);
      return loadJsonArray(DEFAULT_MAPPINGS_CACHE, "mappings");
    }
    throw new Error(
      `No se pudo conectar a Supabase para leer ${MAPPING_TABLE} (${error && error.message ? error.message : error}). `
      + `Si estás en un entorno sin red, usa --mappings-input o crea un cache en ${DEFAULT_MAPPINGS_CACHE}.`
    );
  }
  if (!response.ok) throw new Error(`Supabase mappings HTTP ${response.status}: ${await response.text()}`);
  const rows = await response.json();
  ensureOutputsDir();
  writeJsonFile(DEFAULT_MAPPINGS_CACHE, { mappings: rows });
  return rows;
}

function buildMappingIndex(rows) {
  const map = new Map();
  for (const row of rows || []) {
    if (!row) continue;
    map.set(`${Number(row.source_id)}::${normalizeText(row.campaign_name)}`, row);
  }
  return map;
}

function loadJsonInput(filePath) {
  const data = readJsonFile(filePath);
  return Array.isArray(data) ? data : data.sources || [];
}

function loadJsonArray(filePath, label) {
  if (!fs.existsSync(filePath)) throw new Error(`No existe el archivo JSON (${label}): ${filePath}`);
  const data = readJsonFile(filePath);
  if (Array.isArray(data)) return data;
  const values = data && Array.isArray(data[label]) ? data[label] : null;
  if (!values) throw new Error(`JSON inválido (${label}): se esperaba un array o { ${label}: [...] }`);
  return values;
}

function readJsonFile(filePath) {
  const text = fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, "");
  return JSON.parse(text);
}

function ensureOutputsDir() {
  const outputsDir = path.join(process.cwd(), "outputs");
  if (!fs.existsSync(outputsDir)) fs.mkdirSync(outputsDir, { recursive: true });
}

function writeJsonFile(filePath, data) {
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2) + "\n", "utf8");
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
  if (hasGoogleServiceAccountConfig()) {
    return getSheetValues(spreadsheetId, sheetName, "A:J");
  }

  const gid = await resolveSheetGid(spreadsheetId, sheetName) || extractGidFromUrl(source.spreadsheet_url || "");
  const url = gid
    ? `https://docs.google.com/spreadsheets/d/${spreadsheetId}/export?format=csv&gid=${encodeURIComponent(gid)}`
    : `https://docs.google.com/spreadsheets/d/${spreadsheetId}/gviz/tq?tqx=out:csv&sheet=${encodeURIComponent(sheetName)}`;
  let text = "";
  try {
    const response = await fetch(url);
    if (!response.ok) {
      const hint = response.status === 403 || response.status === 404
        ? " (posible falta de permisos; usa Google Drive + --json-input)"
        : "";
      throw new Error(`Google Sheets HTTP ${response.status} en ${sheetName}${hint}`);
    }
    text = (await response.text()).replace(/^\uFEFF/, "");
  } catch (error) {
    text = fetchTextViaSystem(url, error);
  }
  const values = parseCsv(text).filter((row) => row.some((cell) => String(cell).trim()));
  if (!values.length) return [];
  return values;
}

const sheetGidCache = new Map();

async function resolveSheetGid(spreadsheetId, sheetName) {
  const cacheKey = `${spreadsheetId}::${sheetName}`;
  if (sheetGidCache.has(cacheKey)) return sheetGidCache.get(cacheKey);

  const directGid = extractGidFromUrl(sheetName) || "";
  if (directGid) {
    sheetGidCache.set(cacheKey, directGid);
    return directGid;
  }

  const url = `https://docs.google.com/spreadsheets/d/${spreadsheetId}/edit`;
  let html = "";
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Google Sheets HTML HTTP ${response.status}`);
    html = await response.text();
  } catch (error) {
    try {
      html = fetchTextViaSystem(url, error);
    } catch (_) {
      sheetGidCache.set(cacheKey, "");
      return "";
    }
  }

  const escapedName = String(sheetName || "").replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const nameNeedle = `\\"${escapedName}\\"`;
  const nameIndex = html.indexOf(nameNeedle);
  if (nameIndex === -1) {
    sheetGidCache.set(cacheKey, "");
    return "";
  }

  const recordStart = html.lastIndexOf('[21350203,"[', nameIndex);
  const recordPrefix = recordStart === -1 ? html.slice(Math.max(0, nameIndex - 1000), nameIndex) : html.slice(recordStart, nameIndex);
  const gidMatches = Array.from(recordPrefix.matchAll(/,0,\\"(\d+)\\"/g));
  const gid = gidMatches.length ? gidMatches[gidMatches.length - 1][1] : "";
  sheetGidCache.set(cacheKey, gid);
  return gid;
}

function fetchTextViaSystem(url, originalError) {
  const message = originalError && originalError.message ? originalError.message : String(originalError || "");
  const isWindows = process.platform === "win32";

  try {
    const curlCommand = isWindows ? "curl.exe" : "curl";
    const output = execFileSync(curlCommand, ["-fsSL", url], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
    if (output) return output.replace(/^\uFEFF/, "");
  } catch (_) {
    // ignore; fall back to PowerShell below when available.
  }

  if (isWindows) {
    try {
      const script = [
        "$ProgressPreference='SilentlyContinue'",
        "$ErrorActionPreference='Stop'",
        `$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 -Uri '${String(url).replace(/'/g, "''")}'`,
        "Write-Output $r.Content",
      ].join("; ");
      const output = execFileSync("powershell", ["-NoProfile", "-Command", script], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
      return (output || "").replace(/^\uFEFF/, "");
    } catch (fallbackError) {
      const fallbackMessage = fallbackError && fallbackError.message ? fallbackError.message : String(fallbackError || "");
      throw new Error(`fetch failed (${message}); fallback sistema también falló (${fallbackMessage})`);
    }
  }

  throw new Error(`fetch failed (${message}); y no hay fallback de sistema disponible en ${process.platform}`);
}

function parseValues(source, values, mappings) {
  if (!values.length) return [];
  const headerInfo = findHeaderInfo(values);
  if (!headerInfo) {
    const attempted = buildHeaderMap(values[0] || []);
    const missing = ["date", "campaign_name", "spend"].filter((name) => !(name in attempted));
    console.log(`  ${source.sheet_name}: omitida, faltan columnas ${missing.join(", ")}`);
    return [];
  }
  const { headers, startRowIndex } = headerInfo;

  const rows = [];
  for (const valueRow of values.slice(startRowIndex)) {
    const campaign = cell(valueRow, headers.campaign_name);
    if (!campaign) continue;
    // Skip accidental repeated header rows.
    if (normalizeText(campaign) === normalizeText("campaign name")) continue;
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

function findHeaderInfo(values) {
  const maxScan = Math.min(values.length, 20);
  for (let index = 0; index < maxScan; index += 1) {
    const headers = buildHeaderMap(values[index] || []);
    const hasCampaign = "campaign_name" in headers;
    const hasSpend = "spend" in headers;
    const hasDate = "date" in headers;
    if (hasCampaign && hasSpend && hasDate) return { headers, startRowIndex: index + 1 };

    // Some tabs export with an empty header for the date column (but the first column still contains the date serial).
    if (hasCampaign && hasSpend && !hasDate) {
      const sampleRow = values.slice(index + 1).find((row) => row && row.some((cellValue) => String(cellValue || "").trim()));
      if (sampleRow) {
        const candidateDate = parseDate(cell(sampleRow, 0));
        if (candidateDate) {
          headers.date = 0;
          return { headers, startRowIndex: index + 1 };
        }
      }
    }
  }
  return null;
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

function summarizeRows(rows) {
  const byStore = {};
  let mappedRows = 0;
  let spend = 0;
  for (const row of rows) {
    const storeKey = row.store_key || "";
    if (!byStore[storeKey]) {
      byStore[storeKey] = {
        rows: 0,
        mappedRows: 0,
        spend: 0,
        minDate: "",
        maxDate: "",
      };
    }
    const bucket = byStore[storeKey];
    bucket.rows += 1;
    if (row.sku) {
      mappedRows += 1;
      bucket.mappedRows += 1;
    }
    const rowSpend = Number(row.spend || 0);
    spend += rowSpend;
    bucket.spend = round(bucket.spend + rowSpend, 2);
    if (row.spend_date && (!bucket.minDate || row.spend_date < bucket.minDate)) bucket.minDate = row.spend_date;
    if (row.spend_date && (!bucket.maxDate || row.spend_date > bucket.maxDate)) bucket.maxDate = row.spend_date;
  }
  return {
    mappedRows,
    spend: round(spend, 2),
    byStore,
  };
}

function serializeErrors(errors) {
  return errors.map((entry) => ({
    sourceId: entry.source?.id || null,
    sheetName: entry.source?.sheet_name || "",
    message: entry.message || "",
  }));
}

async function upsertDailyRows(url, key, rows) {
  if (!rows.length) return;
  const params = new URLSearchParams({
    on_conflict: "spend_date,spreadsheet_id,sheet_name,campaign_name",
  });
  let response;
  try {
    response = await fetch(`${url}/rest/v1/${DAILY_TABLE}?${params}`, {
      method: "POST",
      headers: {
        ...supabaseHeaders(key),
        Prefer: "resolution=merge-duplicates,return=minimal",
      },
      body: JSON.stringify(rows),
    });
  } catch (error) {
    throw new Error(`No se pudo conectar a Supabase para upsert en ${DAILY_TABLE} (${error && error.message ? error.message : error}).`);
  }
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

function extractGidFromUrl(value) {
  const match = String(value || "").match(/[?&#]gid=(\d+)/);
  return match ? match[1] : "";
}

function round(value, digits) {
  const factor = 10 ** digits;
  return Math.round(Number(value || 0) * factor) / factor;
}

function reportErrors(errors) {
  if (!errors.length) return;
  console.log("");
  console.log(`Errores al procesar fuentes: ${errors.length}`);
  for (const entry of errors) {
    const source = entry.source || {};
    console.log(`- source_id=${source.id} sheet=${source.sheet_name}: ${entry.message}`);
  }
}

function printHelp() {
  console.log("Uso: node import_ads_to_supabase.js [opciones]");
  console.log("");
  console.log("Opciones:");
  console.log("  --dry-run                 No escribe en Supabase, solo reporta totales.");
  console.log("  --source-id <id>          Procesa solo una fuente (ad_sheet_sources.id).");
  console.log("  --json-input <archivo>    Lee valores desde JSON (fallback a Google Drive).");
  console.log("  --sources-input <archivo> Override: fuentes activas en JSON (sin Supabase).");
  console.log("  --mappings-input <archivo> Override: mapeos campaña->SKU en JSON (sin Supabase).");
  console.log("  --rows-output <archivo>   Exporta filas normalizadas a JSON (sin upsert).");
  console.log("  --help, -h                Muestra esta ayuda.");
  console.log("");
  console.log("Notas:");
  console.log("- Requiere SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY (o SUPABASE_ANON_KEY) en .env.");
  console.log("- Si el CSV público de Google Sheets devuelve 403/404, usa Google Drive para exportar a JSON y ejecuta con --json-input.");
}

module.exports = {
  importAds,
};

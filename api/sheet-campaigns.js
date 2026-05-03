const { getSheetValues, hasGoogleServiceAccountConfig } = require("../google_sheets_api");

module.exports = async function handler(req, res) {
  const spreadsheetId = String(req.query.spreadsheetId || "").trim();
  const sheetName = String(req.query.sheetName || "").trim();

  if (!/^[a-zA-Z0-9-_]{20,}$/.test(spreadsheetId)) {
    res.status(400).send("spreadsheetId invalido.");
    return;
  }
  if (!sheetName) {
    res.status(400).send("sheetName requerido.");
    return;
  }

  try {
    const values = hasGoogleServiceAccountConfig()
      ? await getSheetValues(spreadsheetId, sheetName, "B:B")
      : await fetchPublicCampaignColumn(spreadsheetId, sheetName);
    const campaigns = extractCampaigns(values);

    res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=3600");
    res.status(200).json({ campaigns: campaigns.map((name) => ({ name })) });
  } catch (error) {
    res.status(500).send(error.message || "No pude extraer campanas.");
  }
};

async function fetchPublicCampaignColumn(spreadsheetId, sheetName) {
  const params = new URLSearchParams({
    tqx: "out:csv",
    sheet: sheetName,
    range: "B:B",
  });
  const url = `https://docs.google.com/spreadsheets/d/${spreadsheetId}/gviz/tq?${params}`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Google Sheets HTTP ${response.status} en ${sheetName}`);
  const text = await response.text();
  return parseCsv(text).filter((row) => row.some((cell) => String(cell).trim()));
}

function extractCampaigns(values) {
  const campaigns = [];
  const seen = new Set();
  for (const row of values || []) {
    const raw = Array.isArray(row) ? row[0] : row;
    const name = String(raw ?? "").trim();
    if (!name || isCampaignHeader(name)) continue;
    const key = normalizeText(name);
    if (seen.has(key)) continue;
    seen.add(key);
    campaigns.push(name);
  }
  return campaigns.sort((a, b) => a.localeCompare(b, "es", { numeric: true, sensitivity: "base" }));
}

function isCampaignHeader(value) {
  return ["campaign name", "campaign", "campana"].includes(normalizeText(value));
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

function normalizeText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

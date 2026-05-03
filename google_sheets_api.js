const crypto = require("crypto");

const SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly";
const TOKEN_URL = "https://oauth2.googleapis.com/token";

let cachedToken = null;

function hasGoogleServiceAccountConfig() {
  return Boolean(process.env.GOOGLE_SERVICE_ACCOUNT_EMAIL && process.env.GOOGLE_PRIVATE_KEY);
}

async function getSheetTabs(spreadsheetId) {
  const token = await getAccessToken();
  const url = `https://sheets.googleapis.com/v4/spreadsheets/${encodeURIComponent(spreadsheetId)}?fields=sheets.properties(title)`;
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(`Google Sheets API ${response.status}: ${await response.text()}`);
  }
  const data = await response.json();
  return (data.sheets || [])
    .map((sheet) => sheet.properties?.title)
    .filter(Boolean);
}

async function getSheetValues(spreadsheetId, sheetName, range = "A:J") {
  const token = await getAccessToken();
  const sheetRange = `${quoteSheetName(sheetName)}!${range}`;
  const params = new URLSearchParams({
    majorDimension: "ROWS",
    valueRenderOption: "UNFORMATTED_VALUE",
  });
  const url = `https://sheets.googleapis.com/v4/spreadsheets/${encodeURIComponent(spreadsheetId)}/values/${encodeURIComponent(sheetRange)}?${params}`;
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(`Google Sheets API ${response.status} en ${sheetName}: ${await response.text()}`);
  }
  const data = await response.json();
  return data.values || [];
}

async function getAccessToken() {
  if (cachedToken && cachedToken.expiresAt > Date.now() + 60000) {
    return cachedToken.accessToken;
  }

  const email = process.env.GOOGLE_SERVICE_ACCOUNT_EMAIL;
  const privateKey = normalizePrivateKey(process.env.GOOGLE_PRIVATE_KEY || "");
  if (!email || !privateKey) {
    throw new Error("Faltan GOOGLE_SERVICE_ACCOUNT_EMAIL y GOOGLE_PRIVATE_KEY.");
  }

  const now = Math.floor(Date.now() / 1000);
  const assertion = signJwt(
    { alg: "RS256", typ: "JWT" },
    {
      iss: email,
      scope: SHEETS_SCOPE,
      aud: TOKEN_URL,
      iat: now,
      exp: now + 3600,
    },
    privateKey
  );

  const response = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion,
    }),
  });
  if (!response.ok) {
    throw new Error(`Google OAuth ${response.status}: ${await response.text()}`);
  }
  const data = await response.json();
  cachedToken = {
    accessToken: data.access_token,
    expiresAt: Date.now() + Number(data.expires_in || 3600) * 1000,
  };
  return cachedToken.accessToken;
}

function signJwt(header, payload, privateKey) {
  const unsigned = `${base64UrlJson(header)}.${base64UrlJson(payload)}`;
  const signer = crypto.createSign("RSA-SHA256");
  signer.update(unsigned);
  signer.end();
  return `${unsigned}.${base64Url(signer.sign(privateKey))}`;
}

function base64UrlJson(value) {
  return base64Url(Buffer.from(JSON.stringify(value)));
}

function base64Url(buffer) {
  return buffer
    .toString("base64")
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

function normalizePrivateKey(value) {
  return String(value || "").replace(/\\n/g, "\n");
}

function quoteSheetName(sheetName) {
  return `'${String(sheetName || "").replace(/'/g, "''")}'`;
}

module.exports = {
  getSheetTabs,
  getSheetValues,
  hasGoogleServiceAccountConfig,
};

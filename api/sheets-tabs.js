module.exports = async function handler(req, res) {
  const spreadsheetId = String(req.query.spreadsheetId || "").trim();
  if (!/^[a-zA-Z0-9-_]{20,}$/.test(spreadsheetId)) {
    res.status(400).send("spreadsheetId invalido.");
    return;
  }

  try {
    const response = await fetch(`https://docs.google.com/spreadsheets/d/${spreadsheetId}/edit`);
    if (!response.ok) {
      res.status(response.status).send(`Google Sheets HTTP ${response.status}`);
      return;
    }

    const html = await response.text();
    const tabs = extractTabs(html);
    if (!tabs.length) {
      res.status(404).send("No encontre pestañas visibles en ese Google Sheet.");
      return;
    }

    res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=3600");
    res.status(200).json({ tabs: tabs.map((name) => ({ name })) });
  } catch (error) {
    res.status(500).send(error.message || "No pude extraer pestañas.");
  }
};

function extractTabs(html) {
  const tabs = [];
  const pattern = /docs-sheet-tab-caption">([\s\S]*?)<\/div>/g;
  for (const match of html.matchAll(pattern)) {
    const name = decodeHtml(match[1]).trim();
    if (name && !tabs.includes(name)) tabs.push(name);
  }
  return tabs;
}

function decodeHtml(value) {
  return String(value || "")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCharCode(parseInt(code, 16)))
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

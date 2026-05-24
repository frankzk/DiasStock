const { importAds } = require("../import_ads_to_supabase");

async function handler(req, res) {
  if (!["GET", "POST"].includes(req.method || "GET")) {
    res.status(405).send("Metodo no permitido.");
    return;
  }

  const secret = process.env.CRON_SECRET || process.env.ADS_IMPORT_SECRET || "";
  if (secret) {
    const expected = `Bearer ${secret}`;
    const provided = req.headers.authorization || "";
    if (provided !== expected) {
      res.status(401).send("No autorizado.");
      return;
    }
  }

  try {
    const result = await importAds({
      sourceId: req.query.sourceId ? Number(req.query.sourceId) : null,
      dryRun: req.query.dryRun === "1" || req.query.dryRun === "true",
      loadEnv: false,
    });
    res.setHeader("Cache-Control", "no-store");
    res.status(result.ok ? 200 : 207).json(result);
  } catch (error) {
    res.status(500).send(error && error.message ? error.message : "No se pudo importar Ads.");
  }
}

handler.config = {
  maxDuration: 60,
};

module.exports = handler;

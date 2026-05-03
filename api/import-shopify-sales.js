const { importShopifySales } = require("../shopify_sales_importer");

async function handler(req, res) {
  if (!["GET", "POST"].includes(req.method || "GET")) {
    res.status(405).send("Metodo no permitido.");
    return;
  }

  const secret = process.env.CRON_SECRET || process.env.SHOPIFY_IMPORT_SECRET || "";
  if (secret) {
    const expected = `Bearer ${secret}`;
    const provided = req.headers.authorization || "";
    if (provided !== expected) {
      res.status(401).send("No autorizado.");
      return;
    }
  }

  try {
    const result = await importShopifySales({
      days: req.query.days,
      storeKey: req.query.store || req.query.storeKey || "",
      endDate: req.query.endDate || "",
      runDate: req.query.runDate || "",
      dryRun: req.query.dryRun === "1" || req.query.dryRun === "true",
    });
    res.setHeader("Cache-Control", "no-store");
    res.status(result.ok ? 200 : 207).json(result);
  } catch (error) {
    res.status(500).send(error && error.message ? error.message : "No se pudo importar Shopify.");
  }
}

handler.config = {
  maxDuration: 60,
};

module.exports = handler;

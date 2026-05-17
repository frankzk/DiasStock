const {
  importShopifySales,
  loadEnv,
  parseCliArgs,
  printHelp,
} = require("./shopify_sales_importer");

main().catch((error) => {
  console.error(error && error.message ? error.message : error);
  process.exitCode = 1;
});

async function main() {
  loadEnv();
  const args = parseCliArgs(process.argv.slice(2));
  if (args.help) {
    printHelp();
    return;
  }

  const result = await importShopifySales(args);
  console.log(`Shopify ventas ${result.startDate}..${result.endDate}`);
  console.log(`Tiendas: ${result.stores}, filas: ${result.rows}, unidades: ${result.unitsSold || 0}`);
  if (result.funnelEnabled) {
    console.log(`Funnel Shopify: ${result.funnelRows || 0} fila(s) en ${result.funnelTable}`);
    if (result.funnelWriteWarning) {
      console.warn(`Funnel aviso: ${result.funnelWriteWarning}`);
    }
  }

  for (const store of result.results || []) {
    console.log(
      `  ${store.storeKey} ${store.storeName}: ${store.rows} fila(s), `
      + `${store.unitsSold} unidad(es), ${store.orders} orden(es)`
      + (result.funnelEnabled ? `, funnel ${store.funnelRows || 0} (${store.funnelSource || "sin fuente"})` : "")
    );
    if (store.skippedLineItems) {
      console.log(`    ${store.skippedLineItems} linea(s) omitidas sin SKU o cantidad.`);
    }
    if (store.funnelWarning) {
      console.warn(`    Funnel aviso: ${store.funnelWarning}`);
    }
  }

  if (result.dryRun) console.log("Dry-run: no se escribio en Supabase.");
  if (result.funnelErrors && result.funnelErrors.length) {
    const fallbackNote = result.funnelOrderFallback
      ? "se uso fallback de pedidos donde fue posible"
      : "no se escriben metricas incompletas sin sesiones/cart adds";
    console.warn(`Advertencias funnel ShopifyQL; ${fallbackNote}:`);
    for (const error of result.funnelErrors) {
      console.warn(`  ${error.storeKey} ${error.storeName}: ${error.message}`);
    }
  }
  if (result.errors && result.errors.length) {
    for (const error of result.errors) {
      console.error(`  ${error.storeKey} ${error.storeName}: ${error.message}`);
    }
    process.exitCode = 1;
  } else if (!result.dryRun) {
    console.log("Listo: ventas Shopify guardadas en Supabase.");
  }
}

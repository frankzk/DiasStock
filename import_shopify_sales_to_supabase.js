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

  for (const store of result.results || []) {
    console.log(
      `  ${store.storeKey} ${store.storeName}: ${store.rows} fila(s), `
      + `${store.unitsSold} unidad(es), ${store.orders} orden(es)`
    );
    if (store.skippedLineItems) {
      console.log(`    ${store.skippedLineItems} linea(s) omitidas sin SKU o cantidad.`);
    }
  }

  if (result.dryRun) console.log("Dry-run: no se escribio en Supabase.");
  if (result.errors && result.errors.length) {
    for (const error of result.errors) {
      console.error(`  ${error.storeKey} ${error.storeName}: ${error.message}`);
    }
    process.exitCode = 1;
  } else if (!result.dryRun) {
    console.log("Listo: ventas Shopify guardadas en Supabase.");
  }
}

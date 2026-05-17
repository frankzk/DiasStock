const fs = require("fs");
const path = require("path");

const SALES_TABLE = "shopify_sales_daily";
const RUNS_TABLE = "shopify_sales_import_runs";
const FUNNEL_TABLE = "product_funnel_daily";
const DEFAULT_API_VERSION = "2026-04";
const DEFAULT_TIMEZONE = "America/Lima";
const DEFAULT_DAYS = 7;
const SHOPIFYQL_QUERY = `
query ShopifyqlQuery($query: String!) {
  shopifyqlQuery(query: $query) {
    tableData {
      columns {
        name
        dataType
        displayName
      }
      rows
    }
    parseErrors
  }
}
`;

async function importShopifySales(options = {}) {
  const days = normalizePositiveInt(options.days, DEFAULT_DAYS);
  const timezone = options.timezone || process.env.SHOPIFY_SALES_TIMEZONE || DEFAULT_TIMEZONE;
  const runDate = options.runDate || todayInTimeZone(timezone);
  const endDate = options.endDate || addDaysIso(runDate, -1);
  const startDate = addDaysIso(endDate, -(days - 1));
  const storeKeyFilter = normalizeKey(options.storeKey || "");
  const importFunnel = options.funnel !== false && String(process.env.SHOPIFY_FUNNEL_ENABLED || "1") !== "0";
  const writeOrderFallback = options.funnelOrderFallback === true
    || String(process.env.SHOPIFY_FUNNEL_WRITE_ORDER_FALLBACK || "0") === "1";
  const stores = discoverStores()
    .filter((store) => !storeKeyFilter || normalizeKey(store.key) === storeKeyFilter)
    .filter((store) => store.shopifyUrl && store.shopifyToken);

  if (!stores.length) {
    return {
      ok: true,
      dryRun: Boolean(options.dryRun),
      table: SALES_TABLE,
      funnelTable: FUNNEL_TABLE,
      funnelEnabled: importFunnel,
      days,
      startDate,
      endDate,
      stores: 0,
      rows: 0,
      funnelRows: 0,
      skippedStores: discoverStores().length,
      message: "No hay tiendas con STORE_XX_SHOPIFY_URL y STORE_XX_SHOPIFY_TOKEN.",
      results: [],
      errors: [],
    };
  }

  const allRows = [];
  const allFunnelRows = [];
  const runRows = [];
  const results = [];
  const errors = [];
  const funnelErrors = [];
  const funnelReplaceStoreKeys = new Set();
  let funnelWriteWarning = "";

  for (const store of stores) {
    try {
      const result = await fetchStoreSales(store, { days, startDate, endDate, timezone });
      let funnelRows = [];
      let funnelSource = "";
      let funnelWarning = "";
      if (importFunnel) {
        const orderFallbackRows = salesRowsToFunnelRows(result.rows);
        try {
          const shopifyqlRows = await fetchStoreFunnel(store, { startDate, endDate, timezone });
          if (shopifyqlRows.length) {
            funnelRows = mergeFunnelRows(orderFallbackRows, shopifyqlRows);
            funnelSource = shopifyqlRows.some((row) => String(row.source || "").startsWith("shopifyql_")
              && row.source !== "shopifyql_sales")
              ? "shopifyql"
              : "shopifyql_sales";
            funnelReplaceStoreKeys.add(store.key);
          } else if (writeOrderFallback) {
            funnelRows = orderFallbackRows;
            funnelSource = "shopify_orders";
            funnelWarning = "ShopifyQL no devolvio filas; se usaron pedidos como fallback.";
            funnelReplaceStoreKeys.add(store.key);
          } else {
            funnelWarning = "ShopifyQL no devolvio filas; no se actualizo product_funnel_daily para evitar metricas incompletas.";
          }
        } catch (error) {
          funnelWarning = error && error.message ? error.message : String(error);
          funnelErrors.push({
            storeKey: store.key,
            storeName: store.name,
            message: funnelWarning,
          });
          if (writeOrderFallback) {
            funnelRows = orderFallbackRows;
            funnelSource = "shopify_orders";
            funnelReplaceStoreKeys.add(store.key);
          }
        }
        allFunnelRows.push(...funnelRows);
      }
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
        funnelRows: funnelRows.length,
        funnelSource,
        funnelWarning,
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
    let funnelWritesEnabled = importFunnel && allFunnelRows.length > 0;
    const disableFunnelWrites = (error) => {
      if (!isMissingFunnelTableError(error)) return false;
      funnelWritesEnabled = false;
      funnelWriteWarning = `Falta la tabla ${FUNNEL_TABLE} en Supabase; se guardaron ventas, pero se omitio el funnel. Ejecuta dashboard/product-funnel-schema.sql para habilitarlo.`;
      return true;
    };

    for (const runRow of runRows) {
      await deleteSalesWindow(url, key, runRow.store_key, startDate, endDate);
      if (funnelWritesEnabled && funnelReplaceStoreKeys.has(runRow.store_key)) {
        try {
          await deleteFunnelWindow(url, key, runRow.store_key, startDate, endDate);
        } catch (error) {
          if (!disableFunnelWrites(error)) throw error;
        }
      }
      if (runDate > endDate) {
        await deleteSalesWindow(url, key, runRow.store_key, runDate, runDate);
        if (funnelWritesEnabled && funnelReplaceStoreKeys.has(runRow.store_key)) {
          try {
            await deleteFunnelWindow(url, key, runRow.store_key, runDate, runDate);
          } catch (error) {
            if (!disableFunnelWrites(error)) throw error;
          }
        }
      }
    }
    if (allRows.length) await upsertSalesRows(url, key, allRows);
    if (funnelWritesEnabled && allFunnelRows.length) {
      try {
        await upsertFunnelRows(url, key, allFunnelRows);
      } catch (error) {
        if (!disableFunnelWrites(error)) throw error;
      }
    }
    if (runRows.length) await upsertRunRows(url, key, runRows);
  }

  return {
    ok: errors.length === 0,
    dryRun: Boolean(options.dryRun),
    table: SALES_TABLE,
    runsTable: RUNS_TABLE,
    funnelTable: FUNNEL_TABLE,
    funnelEnabled: importFunnel,
    days,
    runDate,
    startDate,
    endDate,
    stores: stores.length,
    rows: allRows.length,
    funnelRows: allFunnelRows.length,
    funnelOrderFallback: writeOrderFallback,
    unitsSold: allRows.reduce((sum, row) => sum + Number(row.units_sold || 0), 0),
    results,
    errors,
    funnelErrors,
    funnelWriteWarning,
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

async function fetchStoreFunnel(store, options) {
  const customQuery = buildCustomFunnelShopifyql(store, options.startDate, options.endDate);
  if (customQuery) {
    const tableData = await runShopifyqlFunnelQuery(store, customQuery);
    return parseShopifyqlFunnelRows(tableData, store, null, "shopifyql_custom");
  }

  const catalog = await fetchStoreProductCatalog(store);
  const attempts = [
    {
      name: "shopifyql_product_sku",
      query: buildProductSkuFunnelShopifyql(options.startDate, options.endDate),
      parser: (tableData) => parseShopifyqlFunnelRows(tableData, store, catalog, "shopifyql_product_sku"),
    },
    {
      name: "shopifyql_landing_path_cart",
      query: buildLandingPathFunnelShopifyql(options.startDate, options.endDate, "sessions, sessions_with_cart_additions"),
      parser: (tableData) => parseLandingPathFunnelRows(tableData, store, catalog, "shopifyql_landing_path"),
    },
    {
      name: "shopifyql_landing_path_sessions",
      query: buildLandingPathFunnelShopifyql(options.startDate, options.endDate, "sessions"),
      parser: (tableData) => parseLandingPathFunnelRows(tableData, store, catalog, "shopifyql_landing_path"),
    },
    {
      name: "shopifyql_landing_path_page_views",
      query: buildLandingPathFunnelShopifyql(options.startDate, options.endDate, "page_views"),
      parser: (tableData) => parseLandingPathFunnelRows(tableData, store, catalog, "shopifyql_landing_path_page_views"),
    },
    {
      name: "shopifyql_sales",
      query: buildSalesFunnelShopifyql(options.startDate, options.endDate),
      parser: (tableData) => parseShopifyqlFunnelRows(tableData, store, catalog, "shopifyql_sales"),
    },
  ];

  const errors = [];
  for (const attempt of attempts) {
    try {
      const tableData = await runShopifyqlFunnelQuery(store, attempt.query);
      const rows = attempt.parser(tableData);
      if (rows.length) return rows;
      errors.push(`${attempt.name}: 0 filas utiles`);
    } catch (error) {
      errors.push(`${attempt.name}: ${error && error.message ? error.message : String(error)}`);
    }
  }

  if (errors.length) {
    throw new Error(`ShopifyQL ${store.key} no devolvio funnel util. ${errors.join(" | ")}`);
  }
  return [];
}

async function runShopifyqlFunnelQuery(store, query) {
  const apiVersion = process.env.SHOPIFY_API_VERSION || DEFAULT_API_VERSION;
  const domain = normalizeShopifyDomain(store.shopifyUrl);
  const response = await fetch(`https://${domain}/admin/api/${apiVersion}/graphql.json`, {
    method: "POST",
    headers: {
      "X-Shopify-Access-Token": store.shopifyToken,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      query: SHOPIFYQL_QUERY,
      variables: { query },
    }),
  });
  if (!response.ok) {
    throw new Error(`ShopifyQL ${store.key} HTTP ${response.status}: ${await response.text()}`);
  }

  const payload = await response.json();
  if (payload.errors && payload.errors.length) {
    throw new Error(`ShopifyQL ${store.key}: ${payload.errors.map((item) => item.message).join("; ")}`);
  }

  const result = payload.data && payload.data.shopifyqlQuery;
  if (!result) throw new Error(`ShopifyQL ${store.key}: respuesta vacia.`);
  if (result.parseErrors && result.parseErrors.length) {
    const message = result.parseErrors.map((item) => item.message || String(item)).join("; ");
    throw new Error(`ShopifyQL ${store.key} parse error: ${message}`);
  }

  return result.tableData || {};
}

async function fetchStoreProductCatalog(store) {
  const apiVersion = process.env.SHOPIFY_API_VERSION || DEFAULT_API_VERSION;
  const domain = normalizeShopifyDomain(store.shopifyUrl);
  let url = `https://${domain}/admin/api/${apiVersion}/products.json`;
  let params = {
    limit: "250",
    fields: "id,title,handle,variants",
  };
  const handleToProduct = new Map();
  const skuToProduct = new Map();
  const titleToProduct = new Map();
  const titleCollisions = new Set();

  while (url) {
    const response = await fetch(`${url}${params ? `?${new URLSearchParams(params)}` : ""}`, {
      headers: {
        "X-Shopify-Access-Token": store.shopifyToken,
        "Content-Type": "application/json",
      },
    });
    if (!response.ok) {
      throw new Error(`Shopify products ${store.key} HTTP ${response.status}: ${await response.text()}`);
    }

    const data = await response.json();
    for (const product of data.products || []) {
      const productId = product.id ? String(product.id) : "";
      const title = String(product.title || "").trim();
      const handle = normalizeHandle(product.handle);
      const variants = (product.variants || [])
        .map((variant) => ({
          id: variant.id ? String(variant.id) : "",
          sku: String(variant.sku || "").trim(),
        }))
        .filter((variant) => variant.sku);
      const variantSkus = Array.from(new Set(variants.map((variant) => variant.sku)));
      const primaryVariant = variants[0] || {};
      const sku = primaryVariant.sku || variantSkus[0] || "";
      if (!sku) continue;

      const entry = {
        sku,
        productName: title || sku,
        productId,
        variantId: primaryVariant.id || "",
        handle,
        productUrl: buildStorefrontProductUrl(store, handle),
        variantSkus,
        ambiguous: variantSkus.length > 1,
      };
      if (handle) handleToProduct.set(handle, entry);

      for (const variant of variants) {
        if (!variant.sku) continue;
        skuToProduct.set(normalizeKey(variant.sku), {
          ...entry,
          sku: variant.sku,
          variantId: variant.id || "",
        });
      }

      const titleKey = normalizeLookupText(title);
      if (titleKey) {
        if (titleToProduct.has(titleKey)) titleCollisions.add(titleKey);
        else titleToProduct.set(titleKey, entry);
      }
    }

    const nextUrl = parseNextLink(response.headers.get("link") || "");
    url = nextUrl;
    params = null;
  }

  for (const titleKey of titleCollisions) titleToProduct.delete(titleKey);
  return { handleToProduct, skuToProduct, titleToProduct };
}

function buildFunnelShopifyql(store, startDate, endDate) {
  return buildCustomFunnelShopifyql(store, startDate, endDate) || buildSalesFunnelShopifyql(startDate, endDate);
}

function buildCustomFunnelShopifyql(store, startDate, endDate) {
  const template = process.env[`STORE_${store.key}_FUNNEL_SHOPIFYQL_QUERY`]
    || process.env.SHOPIFY_FUNNEL_SHOPIFYQL_QUERY
    || "";
  if (!template) return "";
  return String(template)
    .replace(/\{START_DATE\}/g, startDate)
    .replace(/\{END_DATE\}/g, endDate);
}

function buildProductSkuFunnelShopifyql(startDate, endDate) {
  return [
    "FROM sessions",
    "SHOW sessions, sessions_with_cart_additions",
    "GROUP BY day, product_title, product_variant_sku",
    `SINCE ${startDate}`,
    `UNTIL ${endDate}`,
    "ORDER BY day ASC",
    "LIMIT 10000",
  ].join(" ");
}

function buildLandingPathFunnelShopifyql(startDate, endDate, metrics) {
  return [
    "FROM sessions",
    `SHOW ${metrics}`,
    "GROUP BY day, landing_page_path",
    `SINCE ${startDate}`,
    `UNTIL ${endDate}`,
    "ORDER BY day ASC",
    "LIMIT 10000",
  ].join(" ");
}

function buildSalesFunnelShopifyql(startDate, endDate) {
  return [
    "FROM sales",
    "SHOW quantity_ordered",
    "WHERE line_type = 'product'",
    "GROUP BY day, product_title, product_variant_sku",
    `SINCE ${startDate}`,
    `UNTIL ${endDate}`,
    "ORDER BY day ASC",
    "LIMIT 10000",
  ].join(" ");
}

function parseShopifyqlFunnelRows(tableData, store, catalog, sourceName = "shopifyql") {
  const columns = Array.isArray(tableData.columns) ? tableData.columns : [];
  const rows = Array.isArray(tableData.rows) ? tableData.rows : [];
  const now = new Date().toISOString();
  const parsedRows = [];

  for (const row of rows) {
    const funnelDate = String(readShopifyqlCell(row, columns, ["day", "date", "funnel_date"]) || "").slice(0, 10);
    let sku = String(readShopifyqlCell(row, columns, [
      "product_variant_sku",
      "Product variant SKU",
      "SKU de variante de producto",
      "SKU de variante del producto",
      "variant_sku",
      "sku",
    ]) || "").trim();

    const productName = String(readShopifyqlCell(row, columns, [
      "product_title",
      "Product title",
      "titulo del producto",
      "titulo de producto",
      "product_name",
      "product",
    ]) || "").trim();
    let matched = null;
    if (catalog && sku) {
      matched = catalog.skuToProduct && catalog.skuToProduct.get(normalizeKey(sku));
    }
    if (!sku && catalog && productName) {
      matched = catalog.titleToProduct.get(normalizeLookupText(productName));
      if (matched) sku = matched.sku;
    }
    if (!funnelDate || !sku) continue;

    const sessions = normalizeInt(readShopifyqlCell(row, columns, ["sessions"]));
    const cartAdds = normalizeInt(readShopifyqlCell(row, columns, [
      "cart_adds",
      "add_to_carts",
      "added_to_cart",
      "added_to_carts",
      "sessions_with_cart_additions",
      "Sessions with cart additions",
    ]));
    const ordersCount = normalizeInt(readShopifyqlCell(row, columns, [
      "quantity_ordered",
      "Quantity ordered",
      "cantidad pedida",
      "orders",
      "orders_count",
      "net_items_sold",
      "Net items sold",
      "articulos netos vendidos",
    ]));

    parsedRows.push({
      funnel_date: funnelDate,
      store_key: store.key,
      store_name: store.name,
      sku,
      product_name: productName || matched?.productName || sku,
      product_handle: matched?.handle || null,
      product_url: matched?.productUrl || null,
      landing_page_path: null,
      page_views: 0,
      sessions,
      cart_adds: cartAdds,
      orders_count: ordersCount,
      source: sourceName,
      updated_at: now,
    });
  }

  return parsedRows;
}

function parseLandingPathFunnelRows(tableData, store, catalog, sourceName) {
  if (!catalog || !catalog.handleToProduct || !catalog.handleToProduct.size) return [];
  const columns = Array.isArray(tableData.columns) ? tableData.columns : [];
  const rows = Array.isArray(tableData.rows) ? tableData.rows : [];
  const now = new Date().toISOString();
  const parsedRows = [];

  for (const row of rows) {
    const funnelDate = String(readShopifyqlCell(row, columns, ["day", "date", "funnel_date"]) || "").slice(0, 10);
    const landingPath = String(readShopifyqlCell(row, columns, [
      "landing_page_path",
      "Landing page path",
      "Ruta de la pagina de destino",
      "Ruta de la página de destino",
      "page_path",
      "path",
    ]) || "").trim();
    const handle = extractProductHandleFromPath(landingPath);
    const matched = handle ? catalog.handleToProduct.get(handle) : null;
    if (!funnelDate || !matched || !matched.sku) continue;

    const sessions = normalizeInt(readShopifyqlCell(row, columns, [
      "sessions",
      "visits",
      "visitas",
    ]));
    const pageViews = normalizeInt(readShopifyqlCell(row, columns, [
      "page_views",
      "Page views",
      "vistas de pagina",
      "Vistas de página",
    ]));
    const cartAdds = normalizeInt(readShopifyqlCell(row, columns, [
      "cart_adds",
      "add_to_carts",
      "added_to_cart",
      "added_to_carts",
      "added_to_cart_sessions",
      "sessions_with_cart_additions",
      "Sessions with cart additions",
    ]));

    parsedRows.push({
      funnel_date: funnelDate,
      store_key: store.key,
      store_name: store.name,
      sku: matched.sku,
      product_name: matched.productName || matched.sku,
      product_handle: matched.handle || handle,
      product_url: matched.productUrl || buildStorefrontProductUrl(store, handle),
      landing_page_path: normalizeLandingPath(landingPath),
      page_views: pageViews,
      sessions: sessions || pageViews,
      cart_adds: cartAdds,
      orders_count: 0,
      source: sourceName,
      updated_at: now,
    });
  }

  return parsedRows;
}

function readShopifyqlCell(row, columns, candidates) {
  const normalizedCandidates = candidates.map(normalizeColumnName);
  if (Array.isArray(row)) {
    for (let index = 0; index < columns.length; index += 1) {
      const names = columnNameCandidates(columns[index]).map(normalizeColumnName);
      if (names.some((name) => normalizedCandidates.includes(name))) {
        return unwrapShopifyqlValue(row[index]);
      }
    }
    return undefined;
  }

  if (row && typeof row === "object") {
    for (const [key, value] of Object.entries(row)) {
      if (normalizedCandidates.includes(normalizeColumnName(key))) {
        return unwrapShopifyqlValue(value);
      }
    }
  }

  return undefined;
}

function columnNameCandidates(column) {
  if (!column || typeof column !== "object") return [];
  return [column.name, column.displayName, column.key, column.field].filter(Boolean);
}

function unwrapShopifyqlValue(value) {
  if (value && typeof value === "object") {
    if (Object.prototype.hasOwnProperty.call(value, "value")) return value.value;
    if (Object.prototype.hasOwnProperty.call(value, "data")) return value.data;
    if (Object.prototype.hasOwnProperty.call(value, "formattedValue")) return value.formattedValue;
  }
  return value;
}

function salesRowsToFunnelRows(rows) {
  const now = new Date().toISOString();
  return rows.map((row) => ({
    funnel_date: row.sale_date,
    store_key: row.store_key,
    store_name: row.store_name,
    sku: row.sku,
    product_name: row.product_name || row.sku,
    product_handle: null,
    product_url: null,
    landing_page_path: null,
    page_views: 0,
    sessions: 0,
    cart_adds: 0,
    orders_count: normalizeInt(row.orders_count),
    source: "shopify_orders",
    updated_at: now,
  }));
}

function mergeFunnelRows(fallbackRows, importedRows) {
  const byKey = new Map();
  for (const row of fallbackRows) {
    byKey.set(`${row.funnel_date}||${row.store_key}||${row.sku}`, row);
  }
  for (const row of aggregateFunnelRows(importedRows)) {
    const key = `${row.funnel_date}||${row.store_key}||${row.sku}`;
    const fallback = byKey.get(key);
    byKey.set(key, {
      ...row,
      product_name: row.product_name || (fallback && fallback.product_name) || row.sku,
      orders_count: row.orders_count || (fallback ? fallback.orders_count : 0),
    });
  }
  return Array.from(byKey.values());
}

function aggregateFunnelRows(rows) {
  const byKey = new Map();
  for (const row of rows) {
    const key = `${row.funnel_date}||${row.store_key}||${row.sku}`;
    if (!byKey.has(key)) {
      byKey.set(key, { ...row });
      continue;
    }

    const current = byKey.get(key);
    current.product_name = current.product_name || row.product_name;
    current.product_handle = current.product_handle || row.product_handle;
    current.product_url = current.product_url || row.product_url;
    current.landing_page_path = joinDistinctText(current.landing_page_path, row.landing_page_path);
    current.page_views = normalizeInt(current.page_views) + normalizeInt(row.page_views);
    current.sessions = normalizeInt(current.sessions) + normalizeInt(row.sessions);
    current.cart_adds = normalizeInt(current.cart_adds) + normalizeInt(row.cart_adds);
    current.orders_count = normalizeInt(current.orders_count) + normalizeInt(row.orders_count);
    current.source = joinDistinctText(current.source, row.source);
    current.updated_at = row.updated_at || current.updated_at;
  }
  return Array.from(byKey.values());
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

async function upsertFunnelRows(url, key, rows) {
  for (const chunk of chunkRows(rows, 500)) {
    const response = await fetch(`${url}/rest/v1/${FUNNEL_TABLE}?on_conflict=funnel_date,store_key,sku`, {
      method: "POST",
      headers: supabaseHeaders(key, {
        Prefer: "resolution=merge-duplicates,return=minimal",
      }),
      body: JSON.stringify(chunk),
    });
    if (!response.ok) {
      throw new Error(`Supabase ${FUNNEL_TABLE} HTTP ${response.status}: ${await response.text()}`);
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

async function deleteFunnelWindow(url, key, storeKey, startDate, endDate) {
  const params = new URLSearchParams({
    store_key: `eq.${storeKey}`,
    funnel_date: `gte.${startDate}`,
  });
  params.append("funnel_date", `lte.${endDate}`);
  const response = await fetch(`${url}/rest/v1/${FUNNEL_TABLE}?${params}`, {
    method: "DELETE",
    headers: supabaseHeaders(key, {
      Prefer: "return=minimal",
    }),
  });
  if (!response.ok) {
    throw new Error(`Supabase delete ${FUNNEL_TABLE} HTTP ${response.status}: ${await response.text()}`);
  }
}

function isMissingFunnelTableError(error) {
  const message = error && error.message ? error.message : String(error || "");
  return message.includes(FUNNEL_TABLE)
    && (
      message.includes("PGRST205")
      || message.includes("Could not find the table")
      || message.includes("schema cache")
      || message.includes("HTTP 404")
    );
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
  const args = {
    dryRun: false,
    days: DEFAULT_DAYS,
    storeKey: "",
    endDate: "",
    runDate: "",
    funnel: true,
    funnelOrderFallback: false,
    help: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") args.dryRun = true;
    else if (arg === "--funnel") args.funnel = true;
    else if (arg === "--no-funnel") args.funnel = false;
    else if (arg === "--funnel-order-fallback") args.funnelOrderFallback = true;
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
  node import_shopify_sales_to_supabase.js [--dry-run] [--days 7] [--store KA] [--end-date YYYY-MM-DD] [--no-funnel]

Importa ventas diarias Shopify por SKU a Supabase (${SALES_TABLE}).
Tambien intenta importar funnel por SKU a Supabase (${FUNNEL_TABLE}) via ShopifyQL.
Usa --funnel-order-fallback solo si quieres guardar pedidos sin sesiones/cart adds cuando ShopifyQL no este disponible.
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

function normalizeNumber(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  let text = String(value ?? "").trim();
  if (!text) return 0;
  text = text.replace(/[^\d,.-]/g, "");
  if (!text || text === "-" || text === "." || text === ",") return 0;
  const lastComma = text.lastIndexOf(",");
  const lastDot = text.lastIndexOf(".");
  if (lastComma >= 0 && lastDot >= 0) {
    text = lastComma > lastDot
      ? text.replace(/\./g, "").replace(",", ".")
      : text.replace(/,/g, "");
  } else if (lastComma >= 0) {
    text = text.replace(",", ".");
  }
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

function normalizeInt(value) {
  return Math.max(0, Math.round(normalizeNumber(value)));
}

function normalizePositiveInt(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

function normalizeKey(value) {
  return String(value || "").trim().toLowerCase();
}

function normalizeLookupText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function normalizeHandle(value) {
  let text = String(value || "").trim();
  try {
    text = decodeURIComponent(text);
  } catch (_error) {
    // Keep raw handle when Shopify returns an already-decoded or malformed value.
  }
  return text.toLowerCase().replace(/^\/+|\/+$/g, "");
}

function extractProductHandleFromPath(value) {
  let text = String(value || "").trim();
  if (!text) return "";
  try {
    if (/^https?:\/\//i.test(text)) text = new URL(text).pathname;
  } catch (_error) {
    // Fall back to regex parsing below.
  }
  const match = text.match(/\/products\/([^/?#]+)/i);
  return match ? normalizeHandle(match[1]) : "";
}

function normalizeLandingPath(value) {
  let text = String(value || "").trim();
  if (!text) return "";
  try {
    if (/^https?:\/\//i.test(text)) text = new URL(text).pathname;
  } catch (_error) {
    // Keep the raw landing path if Shopify returns a malformed URL.
  }
  return text.replace(/^https?:\/\/[^/]+/i, "").split("#")[0] || text;
}

function buildStorefrontProductUrl(store, handle) {
  const normalizedHandle = normalizeHandle(handle);
  if (!normalizedHandle) return "";
  const domain = normalizeShopifyDomain(store && store.shopifyUrl);
  return domain ? `https://${domain}/products/${normalizedHandle}` : `/products/${normalizedHandle}`;
}

function joinDistinctText(left, right) {
  const values = [];
  for (const value of [left, right]) {
    const text = String(value || "").trim();
    if (!text) continue;
    for (const part of text.split(" | ")) {
      const normalized = part.trim();
      if (normalized && !values.includes(normalized)) values.push(normalized);
    }
  }
  return values.join(" | ");
}

function normalizeColumnName(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

module.exports = {
  SALES_TABLE,
  RUNS_TABLE,
  FUNNEL_TABLE,
  importShopifySales,
  loadEnv,
  parseCliArgs,
  printHelp,
};

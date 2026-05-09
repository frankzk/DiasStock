const STOCK_IMAGE_BUCKET = process.env.STOCK_IMAGE_BUCKET || "stock-images";
const MAX_IMAGE_BYTES = 6 * 1024 * 1024;

module.exports = async function handler(req, res) {
  const config = getConfig();
  if (!config.url || !config.serviceKey) {
    res.status(500).send("Faltan SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY.");
    return;
  }

  try {
    const caller = await getCaller(config, req);
    if (!caller.isAdmin) {
      res.status(403).send("Solo administradores pueden subir fuentes de stock.");
      return;
    }

    if (req.method === "GET") {
      await ensureBucket(config);
      res.status(200).json(await listStockImages(config, String(req.query.storeKey || "")));
      return;
    }

    if (req.method === "POST") {
      await ensureBucket(config);
      const body = await readJsonBody(req);
      const result = await uploadStockImage(config, body, caller);
      res.status(200).json(result);
      return;
    }

    res.status(405).send("Metodo no permitido.");
  } catch (error) {
    res.status(500).send(error && error.message ? error.message : "No se pudo guardar la imagen de stock.");
  }
};

function getConfig() {
  return {
    url: String(process.env.SUPABASE_URL || "").replace(/\/$/, ""),
    serviceKey: process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_KEY || "",
  };
}

async function getCaller(config, req) {
  const token = String(req.headers.authorization || "").replace(/^Bearer\s+/i, "").trim();
  if (!token) throw new Error("Falta token de sesion.");

  const userResponse = await fetch(`${config.url}/auth/v1/user`, {
    headers: {
      apikey: config.serviceKey,
      Authorization: `Bearer ${token}`,
    },
  });
  if (!userResponse.ok) throw new Error(`Sesion invalida: ${await userResponse.text()}`);
  const user = await userResponse.json();

  const profileResponse = await fetch(
    `${config.url}/rest/v1/dashboard_user_profiles?select=user_id,role,active&user_id=eq.${encodeURIComponent(user.id)}&limit=1`,
    { headers: serviceHeaders(config.serviceKey) }
  );
  if (!profileResponse.ok) throw new Error(`Perfil HTTP ${profileResponse.status}: ${await profileResponse.text()}`);
  const profiles = await profileResponse.json();
  const profile = profiles[0] || null;

  return {
    id: user.id,
    email: user.email || "",
    isAdmin: Boolean(profile && profile.active !== false && profile.role === "admin"),
  };
}

async function ensureBucket(config) {
  const bucketResponse = await fetch(`${config.url}/storage/v1/bucket/${encodeURIComponent(STOCK_IMAGE_BUCKET)}`, {
    headers: serviceHeaders(config.serviceKey),
  });
  if (bucketResponse.ok) return;
  const bucketError = await bucketResponse.text();
  if (bucketResponse.status !== 404 && !isMissingBucketError(bucketResponse.status, bucketError)) {
    throw new Error(`Storage bucket HTTP ${bucketResponse.status}: ${bucketError}`);
  }

  const createResponse = await fetch(`${config.url}/storage/v1/bucket`, {
    method: "POST",
    headers: serviceHeaders(config.serviceKey),
    body: JSON.stringify({
      id: STOCK_IMAGE_BUCKET,
      name: STOCK_IMAGE_BUCKET,
      public: false,
      file_size_limit: MAX_IMAGE_BYTES,
      allowed_mime_types: ["image/jpeg", "image/png", "image/webp"],
    }),
  });
  if (!createResponse.ok && createResponse.status !== 409) {
    throw new Error(`Crear bucket HTTP ${createResponse.status}: ${await createResponse.text()}`);
  }
}

function isMissingBucketError(status, body) {
  if (status === 404) return true;
  try {
    const payload = JSON.parse(body || "{}");
    return payload.statusCode === "404" || payload.statusCode === 404 || payload.error === "Bucket not found";
  } catch {
    return String(body || "").toLowerCase().includes("bucket not found");
  }
}

async function listStockImages(config, storeKey) {
  const prefix = storeKey ? `${safeSegment(storeKey)}/` : "";
  const response = await fetch(`${config.url}/storage/v1/object/list/${encodeURIComponent(STOCK_IMAGE_BUCKET)}`, {
    method: "POST",
    headers: serviceHeaders(config.serviceKey),
    body: JSON.stringify({
      prefix,
      limit: 80,
      offset: 0,
      sortBy: { column: "created_at", order: "desc" },
    }),
  });
  if (!response.ok) throw new Error(`Listar imagenes HTTP ${response.status}: ${await response.text()}`);
  const items = await response.json();
  const uploads = (items || [])
    .filter((item) => item && item.name && item.id)
    .map((item) => normalizeStorageObject(item, prefix))
    .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  return { bucket: STOCK_IMAGE_BUCKET, uploads };
}

function normalizeStorageObject(item, prefix) {
  const objectName = item.name.startsWith(prefix) ? item.name : `${prefix}${item.name}`;
  const fileName = objectName.split("/").pop() || objectName;
  const dateMatch = fileName.match(/^(\d{4}-\d{2}-\d{2})__/);
  const storeKey = objectName.split("/")[0] || "";
  return {
    id: item.id,
    storeKey,
    objectName,
    fileName,
    uploadedDate: dateMatch ? dateMatch[1] : "",
    created_at: item.created_at || item.updated_at || "",
    updated_at: item.updated_at || item.created_at || "",
    size: Number(item.metadata?.size || item.metadata?.contentLength || 0),
    mimeType: item.metadata?.mimetype || item.metadata?.mimeType || "",
  };
}

async function uploadStockImage(config, body, caller) {
  const storeKey = safeSegment(body.storeKey);
  const storeName = String(body.storeName || storeKey).trim();
  const uploadedDate = normalizeDate(body.uploadedDate);
  const fileName = safeFileName(body.fileName || "stock.jpg");
  const contentType = String(body.contentType || "").toLowerCase();
  const dataBase64 = String(body.dataBase64 || "");

  if (!storeKey) throw new Error("Tienda requerida.");
  if (!uploadedDate) throw new Error("Fecha de subida requerida.");
  if (!contentType.startsWith("image/")) throw new Error("El archivo debe ser una imagen.");

  const buffer = Buffer.from(dataBase64, "base64");
  if (!buffer.length) throw new Error("Imagen vacia.");
  if (buffer.length > MAX_IMAGE_BYTES) throw new Error("La imagen supera 6 MB.");

  const stamp = new Date().toISOString().replace(/[-:.]/g, "").replace("T", "-").slice(0, 15);
  const objectName = `${storeKey}/${uploadedDate}__${stamp}__${fileName}`;
  const response = await fetch(`${config.url}/storage/v1/object/${encodeURIComponent(STOCK_IMAGE_BUCKET)}/${encodePath(objectName)}`, {
    method: "POST",
    headers: {
      apikey: config.serviceKey,
      Authorization: `Bearer ${config.serviceKey}`,
      "Content-Type": contentType,
      "x-upsert": "false",
      "cache-control": "3600",
    },
    body: buffer,
  });
  if (!response.ok) throw new Error(`Subir imagen HTTP ${response.status}: ${await response.text()}`);

  return {
    ok: true,
    bucket: STOCK_IMAGE_BUCKET,
    upload: {
      storeKey,
      storeName,
      objectName,
      fileName: objectName.split("/").pop(),
      uploadedDate,
      size: buffer.length,
      mimeType: contentType,
      uploadedBy: caller.email,
      created_at: new Date().toISOString(),
    },
  };
}

function safeSegment(value) {
  return String(value || "").trim().toUpperCase().replace(/[^A-Z0-9_-]/g, "");
}

function safeFileName(value) {
  const clean = String(value || "stock.jpg")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 90);
  return clean || "stock.jpg";
}

function normalizeDate(value) {
  const raw = String(value || "").trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : "";
}

function encodePath(value) {
  return String(value).split("/").map(encodeURIComponent).join("/");
}

function serviceHeaders(key, extra = {}) {
  return {
    apikey: key,
    Authorization: `Bearer ${key}`,
    "Content-Type": "application/json",
    ...extra,
  };
}

async function readJsonBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") return req.body ? JSON.parse(req.body) : {};
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString("utf8");
  return raw ? JSON.parse(raw) : {};
}

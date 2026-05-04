const ALLOWED_ROLES = new Set(["admin", "viewer"]);

module.exports = async function handler(req, res) {
  const config = getConfig();
  if (!config.url || !config.serviceKey) {
    res.status(500).send("Faltan SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY.");
    return;
  }

  try {
    const caller = await getCaller(config, req);
    if (!caller.isAdmin) {
      res.status(403).send("Solo administradores pueden gestionar usuarios.");
      return;
    }

    if (req.method === "GET") {
      res.status(200).json(await listUsers(config));
      return;
    }

    if (req.method === "POST") {
      const body = await readJsonBody(req);
      const result = await upsertDashboardUser(config, body);
      res.status(200).json(result);
      return;
    }

    res.status(405).send("Metodo no permitido.");
  } catch (error) {
    res.status(500).send(error && error.message ? error.message : "No se pudo gestionar usuarios.");
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
    profile,
    isAdmin: Boolean(profile && profile.active !== false && profile.role === "admin"),
  };
}

async function listUsers(config) {
  const [profiles, access] = await Promise.all([
    restJson(config, "dashboard_user_profiles?select=*&order=email.asc"),
    restJson(config, "user_store_access?select=*&order=store_key.asc"),
  ]);

  const storesByUser = new Map();
  for (const row of access) {
    if (!storesByUser.has(row.user_id)) storesByUser.set(row.user_id, []);
    storesByUser.get(row.user_id).push(row.store_key);
  }

  return {
    users: profiles.map((profile) => ({
      user_id: profile.user_id,
      email: profile.email,
      role: profile.role,
      active: profile.active !== false,
      store_keys: storesByUser.get(profile.user_id) || [],
      created_at: profile.created_at,
      updated_at: profile.updated_at,
    })),
  };
}

async function upsertDashboardUser(config, body) {
  const email = String(body.email || "").trim().toLowerCase();
  const userId = String(body.user_id || "").trim();
  const password = String(body.password || "");
  const role = ALLOWED_ROLES.has(body.role) ? body.role : "viewer";
  const active = body.active !== false;
  const storeKeys = Array.isArray(body.store_keys)
    ? Array.from(new Set(body.store_keys.map((value) => String(value || "").trim()).filter(Boolean)))
    : [];

  if (!email) throw new Error("Email requerido.");
  if (!userId && password.length < 8) throw new Error("La contrasena inicial debe tener al menos 8 caracteres.");

  const authUser = userId
    ? await updateAuthUser(config, userId, { email, password })
    : await createAuthUser(config, { email, password });
  const finalUserId = authUser.id || userId;
  if (!finalUserId) throw new Error("No se pudo resolver el user_id.");

  await upsertProfile(config, {
    user_id: finalUserId,
    email,
    role,
    active,
    updated_at: new Date().toISOString(),
  });
  await replaceStoreAccess(config, finalUserId, storeKeys);

  return {
    user_id: finalUserId,
    email,
    role,
    active,
    store_keys: storeKeys,
  };
}

async function createAuthUser(config, { email, password }) {
  const response = await fetch(`${config.url}/auth/v1/admin/users`, {
    method: "POST",
    headers: serviceHeaders(config.serviceKey),
    body: JSON.stringify({
      email,
      password,
      email_confirm: true,
    }),
  });
  if (!response.ok) throw new Error(`Crear usuario HTTP ${response.status}: ${await response.text()}`);
  return response.json();
}

async function updateAuthUser(config, userId, { email, password }) {
  const payload = { email };
  if (password) payload.password = password;
  const response = await fetch(`${config.url}/auth/v1/admin/users/${encodeURIComponent(userId)}`, {
    method: "PUT",
    headers: serviceHeaders(config.serviceKey),
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Actualizar usuario HTTP ${response.status}: ${await response.text()}`);
  return response.json();
}

async function upsertProfile(config, profile) {
  const response = await fetch(`${config.url}/rest/v1/dashboard_user_profiles?on_conflict=user_id`, {
    method: "POST",
    headers: serviceHeaders(config.serviceKey, {
      Prefer: "resolution=merge-duplicates,return=minimal",
    }),
    body: JSON.stringify(profile),
  });
  if (!response.ok) throw new Error(`Perfil HTTP ${response.status}: ${await response.text()}`);
}

async function replaceStoreAccess(config, userId, storeKeys) {
  const deleteResponse = await fetch(
    `${config.url}/rest/v1/user_store_access?user_id=eq.${encodeURIComponent(userId)}`,
    {
      method: "DELETE",
      headers: serviceHeaders(config.serviceKey, { Prefer: "return=minimal" }),
    }
  );
  if (!deleteResponse.ok) throw new Error(`Accesos delete HTTP ${deleteResponse.status}: ${await deleteResponse.text()}`);

  if (!storeKeys.length) return;
  const rows = storeKeys.map((store_key) => ({ user_id: userId, store_key }));
  const insertResponse = await fetch(`${config.url}/rest/v1/user_store_access`, {
    method: "POST",
    headers: serviceHeaders(config.serviceKey, { Prefer: "return=minimal" }),
    body: JSON.stringify(rows),
  });
  if (!insertResponse.ok) throw new Error(`Accesos insert HTTP ${insertResponse.status}: ${await insertResponse.text()}`);
}

async function restJson(config, path) {
  const response = await fetch(`${config.url}/rest/v1/${path}`, {
    headers: serviceHeaders(config.serviceKey),
  });
  if (!response.ok) throw new Error(`Supabase HTTP ${response.status}: ${await response.text()}`);
  return response.json();
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

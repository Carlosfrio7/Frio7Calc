// Cloudflare Pages Function — guarda/lee el horario en un KV namespace.
// Ruta pública: /api/horario?code=<codigo-familiar>
// Requiere un binding de KV llamado HORARIO_KV en el proyecto de Pages.
// v1.1 — binding KV HORARIO_KV conectado.

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET,PUT,POST,OPTIONS",
  "access-control-allow-headers": "content-type"
};

function json(body, status) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status: status || 200,
    headers: { ...CORS, "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }
  });
}

function limpiarCodigo(c) {
  return (c || "").toString().trim().toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 40);
}

export async function onRequest(context) {
  const { request, env } = context;

  if (request.method === "OPTIONS") {
    return new Response(null, { headers: CORS });
  }

  // Comprobar que el KV está configurado
  if (!env.HORARIO_KV) {
    return json({ error: "sin_kv", mensaje: "Falta configurar el almacenamiento (HORARIO_KV) en Cloudflare." }, 500);
  }

  const url = new URL(request.url);
  const code = limpiarCodigo(url.searchParams.get("code"));
  if (!code) return json({ error: "sin_codigo" }, 400);

  const key = "horario:" + code;

  try {
    if (request.method === "GET") {
      const val = await env.HORARIO_KV.get(key);
      return json(val || "null");
    }

    if (request.method === "PUT" || request.method === "POST") {
      const body = await request.text();
      if (body.length > 500000) return json({ error: "demasiado_grande" }, 413);
      await env.HORARIO_KV.put(key, body);
      return json({ ok: true });
    }

    return json({ error: "metodo_no_permitido" }, 405);
  } catch (e) {
    return json({ error: "fallo_servidor", mensaje: String(e) }, 500);
  }
}

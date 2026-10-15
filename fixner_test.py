#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fixner_test.py — Script de prueba de integración con la API de Fixner.

Valida DOS cosas y va imprimiendo lo que recibe:

  FLUJO 1 (solo lectura, seguro):
    1) GET /v2/work-orders?project-id=<PROJECT_ID>&limit=50&page=0
       -> imprime código, nombre, estado e id de cada OT.
    2) GET /v2/work-orders/<id> de la primera OT
       -> imprime el objeto completo en JSON indentado.

  FLUJO 2 (escritura, CREA un registro real en producción):
    3) POST /v2/time-records enlazando OT + operario + inicio/fin (hoy 09:00-11:00)
       -> imprime la respuesta (debería ser 201 Created con el id del registro).

SEGURIDAD: el FLUJO 2 va en DRY-RUN por defecto (imprime el cuerpo que ENVIARÍA
y NO lo manda). Para crear el registro de verdad hay que pasar --confirm-write.

Uso:
    python fixner_test.py                 # Flujo 1 + Flujo 2 en dry-run (no escribe)
    python fixner_test.py --confirm-write # Flujo 1 + POST real (crea registro)
    python fixner_test.py --only-read     # solo Flujo 1
    python fixner_test.py --page 1         # página concreta del listado (por defecto 0)

Requisitos: Python 3.8+ y la librería `requests`  (pip install requests)
Credenciales e IDs: en un archivo .env (ver .env.example). NUNCA van en el código.
"""

import argparse
import datetime as dt
import json
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("Falta la librería 'requests'. Instálala con:  pip install requests")


# ============================================================================
#  CONFIGURACIÓN DE LA API
# ============================================================================
BASE_URL = "https://api.fixner.com"
API_VERSION = "v2"
PAGE_SIZE = 50          # los listados paginan a 50 por página (limit)
TIMEOUT = 30            # segundos


# ============================================================================
#  NOMBRES DE CAMPO  —  ⚠️  CONFIRMAR CON LA DOCUMENTACIÓN DE FIXNER
#  ----------------------------------------------------------------------------
#  No se inventan nombres: se centralizan aquí para que, en cuanto Fixner
#  confirme la estructura exacta, se cambien en un único sitio.
#  El script imprime SIEMPRE el JSON crudo, así que aunque alguno no coincida
#  verás igualmente la respuesta completa y podrás ajustar estas constantes.
# ============================================================================

# --- Campos del objeto Work Order (para el resumen del listado) ---
WO_FIELD_ID     = "id"       # identificador de la OT
WO_FIELD_CODE   = "code"     # código de la OT            (¿"code" / "reference" / "number"?)
WO_FIELD_NAME   = "name"     # nombre/descripción de la OT (¿"name" / "title"?)
WO_FIELD_STATUS = "status"   # estado de la OT            (¿"status" / "state"?)

# Clave donde la API devuelve la lista de resultados en un listado paginado.
# Candidatas típicas: "data", "items", "results"  (o una lista "pelada").
LIST_DATA_KEYS = ("data", "items", "results", "workOrders", "work-orders")

# --- Cuerpo del POST /v2/time-records  (CAMPOS OBLIGATORIOS, a confirmar) ---
#  Según lo que Fixner confirme, el cuerpo enlaza la OT, el operario y el tramo
#  horario. Estos son los NOMBRES DE CLAVE que hay que verificar:
TR_FIELD_WORK_ORDER = "work-order-id"  # (OBLIGATORIO) id de la OT del paso 2
TR_FIELD_WORKER     = "worker-id"      # (OBLIGATORIO) id del operario (WORKER_ID)
TR_FIELD_START      = "start"          # (OBLIGATORIO) fecha/hora de inicio
TR_FIELD_END        = "end"            # (OBLIGATORIO) fecha/hora de fin
#  Posibles campos ADICIONALES que algunas instalaciones exigen (descomentar si aplica):
#  TR_FIELD_PROJECT  = "project-id"    # a veces la OT ya lo implica
#  TR_FIELD_TYPE     = "type"          # tipo de tiempo (mano de obra, desplazamiento…)
#  TR_FIELD_NOTE     = "description"   # nota/observaciones

#  Formato de fecha/hora que espera la API.  A CONFIRMAR si lleva zona horaria:
#   - "%Y-%m-%dT%H:%M:%S"        -> 2026-10-15T09:00:00        (naive/local)
#   - "%Y-%m-%dT%H:%M:%S%z"      -> 2026-10-15T09:00:00+02:00  (con offset)
#   - ISO-8601 UTC con 'Z'       -> 2026-10-15T09:00:00Z
DATETIME_FMT = "%Y-%m-%dT%H:%M:%S"


# ============================================================================
#  UTILIDADES
# ============================================================================
def load_env(path=".env"):
    """Carga variables de un archivo .env a os.environ (parser mínimo, sin dependencias)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def need(name):
    """Obtiene una variable de entorno obligatoria o aborta con mensaje claro."""
    val = os.environ.get(name)
    if not val:
        sys.exit(f"Falta la variable '{name}' en el .env (o en el entorno). Revisa .env.example")
    return val


def dump(obj):
    """Devuelve un objeto como JSON indentado y legible (con acentos)."""
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(obj)


def line(char="-", n=70):
    print(char * n)


def api(session, method, path, *, params=None, json_body=None):
    """
    Hace una petición y muestra código y cuerpo de la respuesta.
    Devuelve (status_code, parsed_json_or_text).  No lanza: el control de
    errores HTTP se gestiona mostrando el código y el cuerpo (requisito).
    """
    url = f"{BASE_URL}/{API_VERSION}/{path.lstrip('/')}"
    print(f"\n>>> {method} {url}")
    if params:
        print(f"    params: {params}")
    if json_body is not None:
        print(f"    body:\n{dump(json_body)}")
    try:
        resp = session.request(method, url, params=params, json=json_body, timeout=TIMEOUT)
    except requests.exceptions.RequestException as exc:
        print(f"!!! Error de red/conexión: {exc}")
        return None, None

    # cuerpo: intentamos JSON; si no, texto plano
    try:
        payload = resp.json()
        body_str = dump(payload)
    except ValueError:
        payload = resp.text
        body_str = payload

    ok = 200 <= resp.status_code < 300
    marca = "OK " if ok else "ERROR"
    print(f"<<< [{marca}] HTTP {resp.status_code}")
    print(body_str if body_str else "(cuerpo vacío)")
    if not ok:
        print(f"!!! La API devolvió un error HTTP {resp.status_code} (ver cuerpo arriba).")
    return resp.status_code, payload


def extract_list(payload):
    """Saca la lista de resultados de un listado paginado (envoltura variable)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in LIST_DATA_KEYS:
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


# ============================================================================
#  FLUJO 1 — RESCATAR LA OT DE LA OBRA  (solo lectura)
# ============================================================================
def flujo_1(session, project_id, page):
    line("=")
    print("FLUJO 1 · Listado de OT de la obra (solo lectura)")
    line("=")

    status, payload = api(
        session, "GET", "work-orders",
        params={"project-id": project_id, "limit": PAGE_SIZE, "page": page},
    )
    if status is None or not (200 <= status < 300):
        print("\n-> No se pudo obtener el listado de OT. Abortando flujo 1.")
        return None

    ots = extract_list(payload)
    print(f"\nOT encontradas en esta página: {len(ots)}")
    line()
    for i, ot in enumerate(ots):
        if not isinstance(ot, dict):
            print(f"  [{i}] (elemento no esperado): {ot!r}")
            continue
        print(f"  [{i}] "
              f"código={ot.get(WO_FIELD_CODE)!r}  "
              f"nombre={ot.get(WO_FIELD_NAME)!r}  "
              f"estado={ot.get(WO_FIELD_STATUS)!r}  "
              f"id={ot.get(WO_FIELD_ID)!r}")
    line()

    if not ots:
        print("-> El listado está vacío; no hay primera OT que detallar.")
        return None

    first = ots[0]
    wo_id = first.get(WO_FIELD_ID) if isinstance(first, dict) else None
    if wo_id is None:
        print(f"-> No encuentro el campo id ('{WO_FIELD_ID}') en la primera OT. "
              f"Revisa WO_FIELD_ID arriba con el JSON crudo mostrado.")
        return None

    print(f"\n-> Detalle de la primera OT (id={wo_id}):")
    api(session, "GET", f"work-orders/{wo_id}")
    return wo_id


# ============================================================================
#  FLUJO 2 — PICAR HORAS  (escritura: crea un registro real)
# ============================================================================
def build_time_record_body(work_order_id, worker_id):
    """
    Construye el cuerpo del POST /v2/time-records.

    CAMPOS OBLIGATORIOS (a confirmar con Fixner — ver constantes TR_FIELD_*):
      - work-order-id : id de la OT (del paso 2)
      - worker-id     : id del operario (WORKER_ID)
      - start         : fecha/hora de inicio
      - end           : fecha/hora de fin

    El tramo es, como pide el enunciado, HOY de 09:00 a 11:00.
    """
    today = dt.date.today()
    start = dt.datetime.combine(today, dt.time(9, 0, 0))
    end = dt.datetime.combine(today, dt.time(11, 0, 0))
    return {
        TR_FIELD_WORK_ORDER: work_order_id,
        TR_FIELD_WORKER:     worker_id,
        TR_FIELD_START:      start.strftime(DATETIME_FMT),
        TR_FIELD_END:        end.strftime(DATETIME_FMT),
        # Descomentar/añadir aquí los campos adicionales que Fixner confirme como obligatorios.
    }


def flujo_2(session, work_order_id, worker_id, confirm_write):
    line("=")
    print("FLUJO 2 · Picar horas en la OT (ESCRITURA)")
    line("=")

    body = build_time_record_body(work_order_id, worker_id)
    print("Cuerpo que se enviaría a POST /v2/time-records:")
    print(dump(body))

    print("\n*** AVISO: esto crea un registro REAL en producción. ***")

    if not confirm_write:
        print("\n[DRY-RUN] No se ha enviado nada (faltó --confirm-write).")
        print("          Revisa el cuerpo de arriba y, si es correcto, vuelve a lanzar con:")
        print("              python fixner_test.py --confirm-write")
        return

    print("\n--confirm-write activo: ENVIANDO el POST real...")
    status, _ = api(session, "POST", "time-records", json_body=body)
    if status == 201:
        print("\n-> 201 Created: registro de horas creado correctamente.")
    elif status is not None and 200 <= status < 300:
        print(f"\n-> {status}: petición aceptada (se esperaba 201; revisa la respuesta).")
    else:
        print("\n-> El POST no se completó correctamente (ver código y cuerpo arriba).")


# ============================================================================
#  MAIN
# ============================================================================
def main():
    parser = argparse.ArgumentParser(description="Prueba de integración con la API de Fixner.")
    parser.add_argument("--confirm-write", action="store_true",
                        help="Envía de verdad el POST de time-records (crea un registro real).")
    parser.add_argument("--only-read", action="store_true",
                        help="Ejecuta solo el Flujo 1 (listado y detalle de OT).")
    parser.add_argument("--page", type=int, default=0,
                        help="Página del listado de OT (por defecto 0).")
    parser.add_argument("--env", default=".env", help="Ruta del archivo .env (por defecto ./.env).")
    args = parser.parse_args()

    load_env(args.env)
    token = need("FIXNER_TOKEN")
    project_id = need("PROJECT_ID")
    worker_id = os.environ.get("WORKER_ID")  # obligatorio solo para el flujo 2

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })

    print(f"Base URL : {BASE_URL}")
    print(f"Versión  : {API_VERSION}")
    print(f"Obra     : PROJECT_ID={project_id}")
    print(f"Operario : WORKER_ID={worker_id or '(no definido)'}")

    wo_id = flujo_1(session, project_id, args.page)

    if args.only_read:
        print("\n--only-read: se omite el Flujo 2 (escritura).")
        return
    if wo_id is None:
        print("\nNo hay OT válida del Flujo 1; se omite el Flujo 2.")
        return
    if not worker_id:
        print("\nFalta WORKER_ID en el .env; se omite el Flujo 2 (escritura).")
        return

    flujo_2(session, wo_id, worker_id, args.confirm_write)


if __name__ == "__main__":
    main()

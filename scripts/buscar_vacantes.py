"""SBV · Sistema de Búsqueda de Vacantes (GlobusHire).

Busca vacantes remotas nuevas de setter y closer con la API de Claude
(búsqueda web) y actualiza vacantes.json, que lee la página de GHL.

Variables de entorno:
  ANTHROPIC_API_KEY  clave de la API de Anthropic (secreto del repo)
  SBV_MODEL          modelo a usar (por defecto claude-sonnet-5-5)
  SBV_ORIGEN         "programada" o "manual"
"""

import datetime as dt
import json
import os
import re
import sys
import unicodedata
from zoneinfo import ZoneInfo

ARCHIVO = os.path.join(os.path.dirname(__file__), "..", "vacantes.json")
TZ = ZoneInfo("America/Bogota")
DIAS_NUEVA = 14      # una vacante nueva debe tener máximo 14 días
DIAS_LISTA = 45      # la lista conserva vacantes de los últimos 45 días
MAX_TURNOS = 14
CAMPOS = ["tipo", "titulo", "empresa", "ubicacion", "pago", "horario", "idioma",
          "requisitos", "alerta", "publicada", "link", "fuente"]

PROMPT = """Eres el buscador de vacantes del SBV (Sistema de Búsqueda de Vacantes) de GlobusHire.
Hoy es {hoy}. Busca en internet vacantes NUEVAS de SETTER y CLOSER remotas para una comunidad de
hispanohablantes de Latinoamérica que trabajan como setters y closers remotos para empresas de EE.UU.

Fuentes que han dado buenos resultados: weremoto.com/categoria-de-trabajo/ventas, worklatam.com/jobs,
vacantesremotas.com, remotive.com, jobgether.com, páginas de empleo (Teamtailor, Workable, Breezy) de
agencias LATAM como Pavago, Valatam, HireLATAM y Freelance Latin America, y Workana.
Búsquedas sugeridas: "remote appointment setter LATAM", "bilingual Spanish appointment setter remote",
"setter remoto español", "DM setter remote", "high ticket closer remoto LATAM",
"closer de ventas remoto español", "SDR remote Latin America cold calling".

Incluye una vacante solo si TODO esto se cumple:
- El rol es de setter (agenda citas o reuniones: appointment setter, DM setter, SDR/BDR que agenda,
  cold caller que agenda) o de closer (cierra ventas por llamada o Zoom, high ticket closer,
  account executive de cierre, inside sales que cierra).
- Es 100% remota y acepta candidatos desde Latinoamérica (o en español). Descarta las que exigen
  residir en EE.UU. o permiso de trabajo de EE.UU.
- Fue publicada en los últimos {dias} días y la página sigue activa. Abre la página de la vacante con
  web_fetch para confirmar la fecha y los datos; si no puedes confirmarla, descártala.
- No pide pagar por entrenamiento, kit, membresía ni inversión inicial. Descarta esquemas multinivel.
- No está en esta lista de vacantes que ya tenemos (compara el link y también empresa + título):
{existentes}

Para cada vacante escribe, en español y con tus palabras (no copies párrafos del anuncio):
tipo ("setter" o "closer"), titulo (corto, que explique el rol), empresa (y el sector entre paréntesis
si ayuda), ubicacion (ej. "Remoto · LATAM"), pago (exacto como lo publica la vacante, o "No publicado"),
horario (días y horas con zona horaria, o "No especificado"), idioma (ej. "Inglés C1"),
requisitos (1–2 frases con lo más importante y cómo se aplica), alerta ("" o un aviso corto como
"Solo comisión, sin sueldo base", "Perfil senior", "Pago por hora muy bajo",
"Reclutador con correo personal: verifica la empresa antes de compartir datos"),
publicada (fecha YYYY-MM-DD), link (URL directa de la vacante) y fuente (nombre del sitio).

Prioriza calidad sobre cantidad: entre 3 y 10 vacantes nuevas es ideal. Trabaja con agilidad.
Cuando termines, llama UNA vez a la herramienta guardar_vacantes con la lista (vacía si no encontraste
ninguna que cumpla)."""

HERRAMIENTA_GUARDAR = {
    "name": "guardar_vacantes",
    "description": "Guarda las vacantes nuevas encontradas. Llamar una sola vez al final.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vacantes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {c: {"type": "string"} for c in CAMPOS},
                    "required": CAMPOS,
                },
            }
        },
        "required": ["vacantes"],
    },
}


def ahora_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_link(url):
    url = (url or "").strip().lower()
    url = re.sub(r"[?#].*$", "", url)
    url = re.sub(r"^https?://(www\.)?", "", url)
    return url.rstrip("/")


def slug(texto, largo=40):
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    texto = re.sub(r"[^a-zA-Z0-9]+", "-", texto.lower()).strip("-")
    return texto[:largo].strip("-") or "vacante"


def fecha(texto):
    try:
        return dt.date.fromisoformat((texto or "")[:10])
    except ValueError:
        return None


def clave_empresa_titulo(v):
    return slug(v.get("empresa", ""), 60) + "|" + slug(v.get("titulo", ""), 60)


def validar(nuevas, existentes, hoy):
    """Filtra lo que devolvió el modelo: campos, fechas, duplicados."""
    vistos_link = {norm_link(v.get("link")) for v in existentes}
    vistos_et = {clave_empresa_titulo(v) for v in existentes}
    ids = {v.get("id") for v in existentes}
    buenas = []
    for v in nuevas or []:
        v = {c: str(v.get(c, "") or "").strip() for c in CAMPOS}
        v["tipo"] = v["tipo"].lower()
        if v["tipo"] not in ("setter", "closer"):
            continue
        if not v["titulo"] or not re.match(r"^https?://", v["link"]):
            continue
        f = fecha(v["publicada"])
        if not f or f > hoy or (hoy - f).days > DIAS_NUEVA:
            continue
        nl, et = norm_link(v["link"]), clave_empresa_titulo(v)
        if nl in vistos_link or et in vistos_et:
            continue
        vistos_link.add(nl)
        vistos_et.add(et)
        base = f"{slug(v['fuente'], 20)}-{v['tipo']}-{slug(v['empresa'] or v['titulo'], 40)}"
        vid, n = base, 2
        while vid in ids:
            vid, n = f"{base}-{n}", n + 1
        ids.add(vid)
        v["id"] = vid
        v["publicada"] = f.isoformat()
        v["agregada"] = hoy.isoformat()
        buenas.append(v)
    return buenas


def combinar(existentes, nuevas, hoy):
    todas = nuevas + existentes
    todas = [v for v in todas if (fecha(v.get("publicada")) or hoy) >= hoy - dt.timedelta(days=DIAS_LISTA)]
    todas.sort(key=lambda v: v.get("publicada", ""), reverse=True)
    orden = ["id"] + CAMPOS + ["agregada"]
    return [{k: v.get(k, "") for k in orden} for v in todas]


def buscar(existentes, hoy):
    import anthropic

    client = anthropic.Anthropic()
    modelo = os.environ.get("SBV_MODEL") or "claude-sonnet-5-5"
    lista = "\n".join(f"- {v.get('empresa', '')} | {v.get('titulo', '')} | {v.get('link', '')}"
                      for v in existentes) or "(ninguna)"
    mensajes = [{"role": "user", "content": PROMPT.format(hoy=hoy.isoformat(), dias=DIAS_NUEVA, existentes=lista)}]
    herramientas = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 12},
        {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 12, "max_content_tokens": 6000},
        HERRAMIENTA_GUARDAR,
    ]
    for turno in range(MAX_TURNOS):
        r = client.messages.create(model=modelo, max_tokens=16000, tools=herramientas, messages=mensajes)
        print(f"turno {turno + 1}: stop_reason={r.stop_reason} uso={r.usage}", flush=True)
        mensajes.append({"role": "assistant", "content": r.content})
        for bloque in r.content:
            if getattr(bloque, "type", "") == "tool_use" and bloque.name == "guardar_vacantes":
                return bloque.input.get("vacantes", [])
        if r.stop_reason == "pause_turn":
            continue
        resultados = [{"type": "tool_result", "tool_use_id": b.id, "content": "Usa guardar_vacantes."}
                      for b in r.content if getattr(b, "type", "") == "tool_use"]
        mensajes.append({"role": "user", "content": resultados + [{
            "type": "text",
            "text": "Llama ahora a guardar_vacantes con las vacantes que cumplen (lista vacía si no hay ninguna)."}]})
    raise RuntimeError("El modelo no entregó la lista de vacantes a tiempo.")


def main():
    inicio = ahora_utc()
    hoy = dt.datetime.now(TZ).date()
    with open(ARCHIVO, encoding="utf-8") as f:
        datos = json.load(f)
    existentes = datos.get("vacantes", [])

    encontradas = buscar(existentes, hoy)
    nuevas = validar(encontradas, existentes, hoy)
    print(f"El modelo devolvió {len(encontradas)}; nuevas válidas: {len(nuevas)}", flush=True)
    for v in nuevas:
        print(f"  + [{v['tipo']}] {v['titulo']} — {v['empresa']}", flush=True)

    salida = {
        "actualizado": hoy.isoformat(),
        "actualizado_en": ahora_utc(),
        "ultima_busqueda": {
            "origen": os.environ.get("SBV_ORIGEN", "programada"),
            "inicio": inicio,
            "fin": ahora_utc(),
            "nuevas": len(nuevas),
            "setter": sum(v["tipo"] == "setter" for v in nuevas),
            "closer": sum(v["tipo"] == "closer" for v in nuevas),
        },
        "vacantes": combinar(existentes, nuevas, hoy),
    }
    with open(ARCHIVO, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=1)
        f.write("\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### SBV · {len(nuevas)} vacantes nuevas\n\n")
            for v in nuevas:
                f.write(f"- **{v['tipo'].title()}**: {v['titulo']} — {v['empresa']} ([link]({v['link']}))\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # el workflow queda en rojo y GHL muestra el error
        print(f"ERROR: {e}", file=sys.stderr)
        raise

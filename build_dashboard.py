# -*- coding: utf-8 -*-
"""
Genera index.html (dashboard LID) a partir de lid_metricas.db.
Se corre a mano o desde reporte_lid_ga4.py despues de cada actualizacion semanal.

Uso:
    python build_dashboard.py
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.expanduser("~/Desktop/lid_metricas.db")
OUTPUT_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}
MESES_CORTO = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
    7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic",
}

CAMPOS_PONDERADOS = [
    "engagement_rate", "pct_directo", "pct_organico", "pct_social",
    "pct_referral", "pct_email", "pct_mobile", "pct_desktop",
    # Desglose de dispositivo sobre sesiones totales (las columnas de
    # arriba son sobre sesiones con interaccion). Ver el docstring de
    # ga4_api_lid.extraer_dispositivo.
    "pct_mobile_total", "pct_desktop_total",
]


def leer_semanas(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT semana_inicio, semana_fin, sesiones_totales,
               pct_directo, pct_organico, pct_social, pct_referral, pct_email,
               pct_mobile, pct_desktop, pct_mobile_total, pct_desktop_total,
               engagement_rate, paginas_por_sesion,
               usuarios_totales, usuarios_nuevos, usuarios_recurrentes
        FROM metricas_sitio
        ORDER BY semana_inicio
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def leer_notas_por_semana(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT semana_inicio, titulo, url, vistas, tiempo_lectura_promedio,
               es_landing_page, sesiones_como_landing, alerta_bajo_desempeno,
               fecha_publicacion
        FROM metricas_notas
    """)
    por_semana = {}
    for row in cur.fetchall():
        semana_inicio = row[0]
        por_semana.setdefault(semana_inicio, []).append({
            "titulo": row[1], "url": row[2], "vistas": row[3],
            "tiempo_lectura_promedio": row[4], "es_landing_page": row[5],
            "sesiones_como_landing": row[6], "alerta_bajo_desempeno": row[7],
            "fecha_publicacion": row[8],
        })
    return por_semana


def leer_tags_por_semana(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT semana_inicio, titulo, url, vistas,
               tiempo_en_pagina_promedio, sesiones_como_landing
        FROM metricas_tags
    """)
    por_semana = {}
    for row in cur.fetchall():
        semana_inicio = row[0]
        por_semana.setdefault(semana_inicio, []).append({
            "titulo": row[1], "url": row[2], "vistas": row[3],
            "tiempo_en_pagina_promedio": row[4], "sesiones_como_landing": row[5],
        })
    return por_semana


def mes_de_semana(fecha_inicio_str, fecha_fin_str):
    """Asigna la semana al mes calendario donde caen mas de sus 7 dias."""
    fi = datetime.strptime(fecha_inicio_str, "%Y-%m-%d")
    conteo = {}
    for i in range(7):
        d = fi + timedelta(days=i)
        clave = (d.year, d.month)
        conteo[clave] = conteo.get(clave, 0) + 1
    return max(conteo.items(), key=lambda kv: kv[1])[0]


def agregar_mensual(semanas, notas_por_semana):
    meses = {}
    for fila in semanas:
        anio, mes = mes_de_semana(fila["semana_inicio"], fila["semana_fin"])
        clave = f"{anio:04d}-{mes:02d}"
        m = meses.setdefault(clave, {
            "anio": anio, "mes": mes,
            "sesiones_totales": 0, "usuarios_totales": 0,
            "usuarios_nuevos": 0, "usuarios_recurrentes": 0,
            "_sumas": {c: 0.0 for c in CAMPOS_PONDERADOS},
            "titulos_unicos": set(), "titulos_alerta": set(),
            "semanas": [],
        })
        m["semanas"].append(fila["semana_inicio"])
        s = fila["sesiones_totales"] or 0
        m["sesiones_totales"] += s
        m["usuarios_totales"] += fila["usuarios_totales"] or 0
        m["usuarios_nuevos"] += fila["usuarios_nuevos"] or 0
        m["usuarios_recurrentes"] += fila["usuarios_recurrentes"] or 0
        for campo in CAMPOS_PONDERADOS:
            valor = fila.get(campo)
            if valor is not None:
                m["_sumas"][campo] += valor * s

        for nota in notas_por_semana.get(fila["semana_inicio"], []):
            m["titulos_unicos"].add(nota["titulo"])
            if nota["alerta_bajo_desempeno"]:
                m["titulos_alerta"].add(nota["titulo"])

    resultado = []
    for clave in sorted(meses.keys()):
        m = meses[clave]
        total_s = m["sesiones_totales"] or 1
        fila_out = {
            "mes": clave,
            "mes_label": f"{MESES_CORTO[m['mes']]} {m['anio']}",
            "mes_label_largo": f"{MESES_ES[m['mes']]} de {m['anio']}",
            "sesiones_totales": m["sesiones_totales"],
            "usuarios_totales": m["usuarios_totales"],
            "usuarios_nuevos": m["usuarios_nuevos"],
            "usuarios_recurrentes": m["usuarios_recurrentes"],
            "notas_unicas": len(m["titulos_unicos"]),
            "notas_alerta": len(m["titulos_alerta"]),
            "n_semanas": len(m["semanas"]),
        }
        for campo in CAMPOS_PONDERADOS:
            fila_out[campo] = m["_sumas"][campo] / total_s
        resultado.append(fila_out)
    return resultado


def agregar_notas_por_mes(semanas, notas_por_semana, top_n=100):
    """
    Para cada mes calendario, agrega las notas de todas sus semanas:
    vistas sumadas, tiempo de lectura promedio ponderado por vistas de
    cada semana, y sesiones_como_landing sumadas. Dedup por titulo (una
    nota que aparece en varias semanas del mes no se cuenta dos veces).
    Devuelve {mes: [ {titulo, url, vistas, tiempo_lectura_promedio,
    sesiones_como_landing}, ... ]} con el top_n por vistas.
    """
    acumulados = {}
    for fila in semanas:
        anio, mes = mes_de_semana(fila["semana_inicio"], fila["semana_fin"])
        clave = f"{anio:04d}-{mes:02d}"
        bucket = acumulados.setdefault(clave, {})
        for nota in notas_por_semana.get(fila["semana_inicio"], []):
            titulo = nota["titulo"]
            vistas = nota["vistas"] or 0
            entrada = bucket.setdefault(titulo, {
                "titulo": titulo, "url": nota["url"] or "",
                "vistas": 0, "_suma_tiempo_ponderado": 0.0,
                "sesiones_como_landing": 0.0,
            })
            entrada["vistas"] += vistas
            entrada["_suma_tiempo_ponderado"] += (nota["tiempo_lectura_promedio"] or 0.0) * vistas
            entrada["sesiones_como_landing"] += nota["sesiones_como_landing"] or 0
            if not entrada["url"] and nota["url"]:
                entrada["url"] = nota["url"]

    resultado = {}
    for clave, bucket in acumulados.items():
        filas = []
        for entrada in bucket.values():
            tiempo = (
                entrada["_suma_tiempo_ponderado"] / entrada["vistas"]
                if entrada["vistas"] > 0 else 0.0
            )
            filas.append({
                "titulo": entrada["titulo"],
                "url": entrada["url"],
                "vistas": entrada["vistas"],
                "tiempo_lectura_promedio": tiempo,
                "sesiones_como_landing": entrada["sesiones_como_landing"],
            })
        filas.sort(key=lambda f: f["vistas"], reverse=True)
        resultado[clave] = filas[:top_n]
    return resultado


def agregar_tags_por_mes(semanas, tags_por_semana, top_n=100):
    """
    Igual que agregar_notas_por_mes pero para metricas_tags: agrega por
    titulo (dedup), vistas sumadas, tiempo en pagina promedio ponderado
    por vistas, sesiones_como_landing sumadas.
    """
    acumulados = {}
    for fila in semanas:
        anio, mes = mes_de_semana(fila["semana_inicio"], fila["semana_fin"])
        clave = f"{anio:04d}-{mes:02d}"
        bucket = acumulados.setdefault(clave, {})
        for tag in tags_por_semana.get(fila["semana_inicio"], []):
            titulo = tag["titulo"]
            vistas = tag["vistas"] or 0
            entrada = bucket.setdefault(titulo, {
                "titulo": titulo, "url": tag["url"] or "",
                "vistas": 0, "_suma_tiempo_ponderado": 0.0,
                "sesiones_como_landing": 0.0,
            })
            entrada["vistas"] += vistas
            entrada["_suma_tiempo_ponderado"] += (tag["tiempo_en_pagina_promedio"] or 0.0) * vistas
            entrada["sesiones_como_landing"] += tag["sesiones_como_landing"] or 0
            if not entrada["url"] and tag["url"]:
                entrada["url"] = tag["url"]

    resultado = {}
    for clave, bucket in acumulados.items():
        filas = []
        for entrada in bucket.values():
            tiempo = (
                entrada["_suma_tiempo_ponderado"] / entrada["vistas"]
                if entrada["vistas"] > 0 else 0.0
            )
            filas.append({
                "titulo": entrada["titulo"],
                "url": entrada["url"],
                "vistas": entrada["vistas"],
                "tiempo_en_pagina_promedio": tiempo,
                "sesiones_como_landing": entrada["sesiones_como_landing"],
            })
        filas.sort(key=lambda f: f["vistas"], reverse=True)
        resultado[clave] = filas[:top_n]
    return resultado


def formatear_semana_label(fecha_inicio_str):
    d = datetime.strptime(fecha_inicio_str, "%Y-%m-%d")
    return f"{d.day} {MESES_CORTO[d.month]}"


def preparar_datos():
    conn = sqlite3.connect(DB_PATH)
    semanas = leer_semanas(conn)
    notas_por_semana = leer_notas_por_semana(conn)
    tags_por_semana = leer_tags_por_semana(conn)
    conn.close()

    for fila in semanas:
        fila["semana_label"] = formatear_semana_label(fila["semana_inicio"])

    meses = agregar_mensual(semanas, notas_por_semana)
    notas_por_mes = agregar_notas_por_mes(semanas, notas_por_semana)
    tags_por_mes = agregar_tags_por_mes(semanas, tags_por_semana)

    semana_mas_reciente = semanas[-1]["semana_inicio"]
    notas_semana_actual = sorted(
        notas_por_semana.get(semana_mas_reciente, []),
        key=lambda n: n["vistas"], reverse=True,
    )
    alertas_semana_actual = sorted(
        [n for n in notas_semana_actual if n["alerta_bajo_desempeno"]],
        key=lambda n: n["vistas"],
    )
    tags_semana_actual = sorted(
        tags_por_semana.get(semana_mas_reciente, []),
        key=lambda t: t["vistas"], reverse=True,
    )

    return {
        "semanas": semanas,
        "meses": meses,
        "notas_por_mes": notas_por_mes,
        "tags_por_mes": tags_por_mes,
        "semana_mas_reciente": semana_mas_reciente,
        "semana_mas_reciente_label": formatear_semana_label(semana_mas_reciente),
        "semana_mas_reciente_fin_label": formatear_semana_label(semanas[-1]["semana_fin"]),
        "notas_semana_actual": notas_semana_actual,
        "alertas_semana_actual": alertas_semana_actual,
        "tags_semana_actual": tags_semana_actual,
        "generado": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }


def render_html(datos):
    data_json = json.dumps(datos, ensure_ascii=False)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html"), encoding="utf-8") as f:
        template = f.read()
    return template.replace("__DASHBOARD_DATA__", data_json)


def main():
    datos = preparar_datos()
    html = render_html(datos)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"OK: index.html generado con {len(datos['semanas'])} semanas y {len(datos['meses'])} meses.")
    print(f"Semana mas reciente: {datos['semana_mas_reciente']} ({len(datos['notas_semana_actual'])} notas, "
          f"{len(datos['alertas_semana_actual'])} en alerta)")


if __name__ == "__main__":
    main()

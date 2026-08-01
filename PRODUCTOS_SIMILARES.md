# Productos similares y sustitutos — informe

**Fecha:** 2026-07-31
**Alcance:** primera versión determinística de "Productos similares" en la página de detalle. Sin IA ni embeddings. No se tocó `reranking.py`, el índice FTS5 (solo se agregó un índice SQL normal en `categoria`), `capa_intencion.py`, proyectos ni comparación.

---

## 1. Resumen

Se agregó una sección "Productos similares" (hasta 6) al final de la página de detalle. El algoritmo (`similares.py`) puntúa candidatos con señales explícitas y auditables — familia, subcategoría, tokens del nombre, marca, peso, tokens de la descripción — sacados con SQL directo sobre `productos`, nunca vía FTS5. Si nada califica con confianza, la sección no aparece.

## 2. Archivos modificados

**Backend:**
- `similares.py` (nuevo) — el algoritmo completo.
- `database/proyecta.db` — índice nuevo en `categoria` (aditivo).
- `api/main.py` — nueva ruta `GET /productos/similares`; se extrajo `_serializar_producto()` para no duplicar la lógica de campos opcionales entre `/buscar` y esta ruta nueva.
- `tests/test_similares.py` (nuevo) — 7 pruebas.

**Frontend:**
- `app/hooks/useProductosSimilares.ts` (nuevo).
- `app/components/ProductosSimilares.tsx` (nuevo) — reutiliza `ProductCard` sin modificarlo.
- `app/producto/[id]/page.tsx` — agrega la sección al final.

## 3. Ejemplos reales (verificación manual en las 6 categorías pedidas)

**Cemento** — objetivo: *Cemento Gris Por Kilo* (El Lagar)
```
[15] Cemento Blanco Por Kilo                          misma_subcategoria, tokens:cemento,por, misma_marca
[13] Concreto Seco Cemento Arena Piedra 40 Kg Supermix misma_subcategoria, tokens:cemento, misma_marca
[13] Mortero Seco Cemento Arena 40 Kg Supermix         misma_subcategoria, tokens:cemento, misma_marca
[10] Cemento Gris Uso General Alto Desempeño 50 Kg     misma_subcategoria, tokens:cemento,gris
```

**Pintura** — objetivo: *Pintura 3 en 1 Ultra Dry Coat Blanco Cubeta Lanco* (El Lagar)
```
[14] Pintura 3 en 1 Dry Coat Satin Deep Cubeta Lanco       misma_subcategoria, tokens:coat,cubeta,dry,lanco,pintura
[14] Pintura 3 en 1 Ultra Dry Coat Blanco Galon Lanco      misma_subcategoria, tokens:blanco,coat,dry,lanco,pintura,ultra
[ 8] Pintura Anticorrosiva Durex Mate Blanco Cubeta Lanco  tokens:blanco,cubeta,lanco,pintura
```

**Taladros** — objetivo: *Taladro percutor 1/2" 750 W Daewoo* (EPA)
```
[17] Taladro rotacional 3/8" 450 W Daewoo             misma_subcategoria, tokens:daewoo,taladro, peso_similar
[14] Taladro de impacto 1/2" 1050 W Daewoo            misma_subcategoria, tokens:daewoo,taladro
[14] Taladro percutor alámbrico 1/2" 120-220 V Stanley misma_subcategoria, tokens:percutor,taladro
```
*Caso límite honesto:* un taladro de núcleo de Carbone Store (categoría "Herramientas Con Cable", sin subcategoría) no obtuvo ninguna recomendación — correcto, no hay candidatos confiables, no se rellena con nada débil (ver captura `similares_desktop.png` más abajo).

**Tubería PVC** — objetivo: *Tubo PVC SDR17 4" x 6 m* (EPA)
```
[19] Tubo PVC SDR17 3" x 6 m         misma_subcategoria, tokens:pvc,sdr17,tubo, peso_similar
[17] Tubo PVC SDR41 4" x 6 m         misma_subcategoria, tokens:pvc,tubo, peso_similar
[17] Tubo PVC sanitario 4" 6 m blanco misma_subcategoria, tokens:pvc,tubo, peso_similar
```

**Tornillos** — objetivo: *Tornillo gypsum punta broca 1" 1.000 uds* (EPA)
```
[21] Tornillo gypsum punta broca 2" 500 uds                 misma_subcategoria, tokens:gypsum,punta,tornillo,uds, peso_similar
[21] Tornillo gypsum galvanizado punta broca 1 1/2" 500 uds  misma_subcategoria, tokens:gypsum,punta,tornillo,uds, peso_similar
[19] Tornillo torlack punta broca 8x1" 500 uds               misma_subcategoria, tokens:punta,tornillo,uds, peso_similar
```

**Cable eléctrico** — objetivo: *Cable TGP 2x14 Viakon negro* (EPA)
```
[14] Cable TSJ-N 2x16 AWG Viakon negro    misma_subcategoria, tokens:cable,negro,por,precio,viakon
[14] Cable TGP 3x14 Viakon negro          misma_subcategoria, tokens:cable,negro,por,precio,tgp,viakon
[14] Cable THHN #6 Viakon azul            misma_subcategoria, tokens:cable,por,precio,viakon
```

## 4. Métricas de cobertura

Muestra de 300 productos reales (75 por proveedor, semilla fija para reproducibilidad):

| | |
|---|---|
| Cobertura (≥1 recomendación) | **290/300 (96.7%)** |
| Obtienen las 6 completas | 257/300 (85.7%) |
| Obtienen 3-5 | 17/300 (5.7%) |
| Obtienen 1-2 | 16/300 (5.3%) |
| Sin recomendación (sección oculta) | 10/300 (3.3%) |
| Usan familia_id | 7/290 (solo Pinturas la tiene) |
| Usan señales de respaldo | 283/290 |

Cobertura por proveedor: EPA 100%, El Lagar 100%, Ferretería Brenes 94.7% (¡incluso sin marca ni descripción!), Carbone Store 92%.

**Rendimiento:** 100.8ms promedio por consulta, 246ms máximo, sobre 300 llamadas reales contra la base completa (30,681 productos) — rápido para cargarlo junto con la página de detalle.

## 5. Limitaciones encontradas

1. **Carbone Store tiene la cobertura más baja (92%)** porque su campo `categoria` (viene de `product_type` de Shopify) a veces es casi único por producto — ej. "Taladros Inalámbricos" o "Tornillos para Drywall" con **un solo producto** en toda la categoría. Como el algoritmo exige coincidencia de categoría como filtro base, esos productos no tienen ni un candidato posible. No se puede arreglar sin tocar el crawler de Carbone (fuera de alcance de esta fase) — documentado, no oculto.
2. **`familia_id` casi no se usa (7/290 en la muestra)** porque solo cubre Pinturas. Para el resto del catálogo el sistema depende enteramente de las señales de respaldo — que en la práctica funcionan bien (96.7% de cobertura igual), pero es la razón por la que la prioridad de familia rara vez se ve en la práctica fuera de pinturas.
3. **El "peso" de EPA no tiene unidad confirmada** (se documentó ya en `ENRIQUECIMIENTO_CATALOGO.md`) — la señal `peso_similar` es una tolerancia relativa (20%), no una comparación en una unidad real conocida. Funciona como bonus débil, nunca decide una recomendación por sí sola (el umbral mínimo exige subcategoría o tokens de nombre además).
4. **La bolsa de palabras de la descripción es simple** (primeras 40 palabras significativas, sin peso por posición ni relevancia) — es intencionalmente la señal más débil (tope +4 puntos) precisamente porque es la menos confiable; nunca por sí sola supera el umbral mínimo.
5. **No hay noción de "sustituto exacto" vs. "producto relacionado”** — el sistema no distingue explícitamente entre "mismo producto, otro proveedor" y "producto parecido pero no intercambiable". Las razones (`_razones`) permiten auditar esa distinción manualmente, pero la UI de hoy no la expone al usuario.

## 6. Capturas

Desktop, caso con resultados (Taladro percutor Daewoo, EPA → 6 taladros similares reales): `similares_desktop_ok2.png`.
Desktop, caso límite honesto sin resultados (taladro de núcleo Carbone Store, sección oculta): `similares_desktop.png`.
Mobile, caso con resultados (limpiador de juntas EPA → 6 productos de limpieza reales, layout de 1 columna): `similares_mobile.png`.

Las tres están en el scratchpad de esta sesión — se muestran también en el resumen de esta conversación.

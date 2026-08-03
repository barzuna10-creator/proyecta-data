# Release checklist — Proyecta CR Beta v1.0

Último sprint antes de empezar pruebas con ingenieros reales. Revisión completa de calidad — sin funcionalidades nuevas, sin cambios de arquitectura, sin tocar motor de búsqueda ni crawlers. Solo se corrigieron problemas reales que afectan la percepción de calidad.

Este documento cubre el trabajo de los dos últimos sprints de esta fase (auditoría de confianza + este sprint final de QA), ya que ambos se comitean juntos como el cierre de la beta.

---

## Bugs encontrados

### De la auditoría de confianza (sprint anterior)
1. Tuteo y voseo mezclados en toda la aplicación — 17 instancias en 5 archivos, incluyendo un caso con ambos dentro de la misma oración.
2. Formato de moneda inconsistente en "Mis proyectos" (`toLocaleString()` directo en vez del helper compartido `formatearMonto()`) — mismo tipo de bug de decimales inconsistentes ya corregido antes en otra pantalla, colado en un archivo que nunca se había tocado.
3. Fecha de creación del proyecto mostrada en formato técnico crudo (`2026-08-03`) en vez de formato legible (`03/08/2026`).
4. Metaetiqueta de descripción del sitio desactualizada (mensaje genérico previo al reposicionamiento hacia remodelaciones) y en tuteo.

### De este sprint final de QA
5. **Sin página 404 personalizada** — cualquier enlace roto o URL mal escrita caía en la página por defecto de Next.js, sin marca ni estilo, mientras el resto de la aplicación tiene una identidad visual cuidada.
6. **Miniaturas de galería de imágenes sin texto accesible** — en el detalle de producto, cada miniatura es un enlace que abre la imagen en tamaño completo, pero no tenía ningún nombre accesible (`alt=""` en la imagen, sin `aria-label` en el enlace) — invisible para un lector de pantalla.
7. **Sin estilo de foco de teclado consistente** — varios botones y enlaces de la aplicación (buscador, estados vacíos, comparador, detalle de producto) dependían del contorno azul por defecto del navegador en vez de un estilo propio, generando una experiencia de teclado visualmente inconsistente con el resto de la interfaz.
8. **Animación de entrada sin respetar la preferencia de movimiento reducido del sistema operativo** — la animación `fade-in-up`, usada en casi toda tarjeta y bloque de contenido, se reproducía siempre, sin importar si el usuario configuró su sistema para minimizar animaciones.
9. **Imágenes de producto sin carga diferida** — en una grilla de 50 resultados, las 50 imágenes se cargaban de inmediato aunque la mayoría estuviera fuera de pantalla.

---

## Bugs corregidos

Los 9 bugs listados arriba fueron corregidos en su totalidad. Detalle de archivos:

| Archivo | Corrección |
|---|---|
| `app/page.tsx` | Voseo en mensaje de resultados |
| `app/components/EmptyState.tsx` | Voseo en los 6 estados vacíos/error compartidos |
| `app/components/AgregarAProyecto.tsx` | Voseo en "no tenés proyectos" |
| `app/proyectos/[id]/page.tsx` | Voseo en los 10 mensajes de error de la cotización |
| `app/proyectos/page.tsx` | Formato de moneda unificado con `formatearMonto()` |
| `app/components/proyecto/FichaProyecto.tsx` | Formato de fecha DD/MM/AAAA |
| `app/layout.tsx` | Metaetiqueta de descripción actualizada y en voseo |
| `app/not-found.tsx` (nuevo) | Página 404 con la identidad visual de la aplicación |
| `app/producto/[id]/page.tsx` | `aria-label` en enlaces de miniaturas de galería |
| `app/globals.css` | Foco de teclado consistente (regla global, no invasiva) + `prefers-reduced-motion` |
| `app/components/ProductCard.tsx` | `loading="lazy"` en imagen de tarjeta |
| `app/components/FamilyCard.tsx` | `loading="lazy"` en imagen de tarjeta |
| `app/components/proyecto/ItemProyectoRow.tsx` | `loading="lazy"` en imagen de ítem |
| `app/comparar/page.tsx` | `loading="lazy"` en imágenes del comparador |

## Mejoras visuales

Todas las de la auditoría de confianza (voseo consistente, formato de fecha y moneda profesional) más, de este sprint: página 404 con marca propia, foco de teclado visible y coherente con el color de acento en toda la app (verificado con navegación real por teclado), y animaciones que respetan la configuración de accesibilidad del sistema operativo del usuario.

---

## Hallazgos investigados y descartados (falsos positivos)

Documentados porque se investigaron a fondo antes de decidir no tocarlos — no son omisiones, son verificaciones que salieron bien:

- **Dos `<h1>` en el detalle de producto**: parecía un error de jerarquía, pero son mutuamente excluyentes (uno para el estado "producto no encontrado", otro para el producto cargado) — nunca coexisten en el DOM.
- **Jerarquía de encabezados en Cotización**: parecía saltar de h1 a h3 directamente, pero al contar los encabezados de los componentes hijos (Ficha del proyecto y Resumen de la cotización, ambos h2) la jerarquía está correctamente anidada.
- **Iconografía**: la app usa caracteres de texto (−, +, ×, ←) en vez de una librería de íconos SVG — es una decisión consistente en toda la aplicación, no una mezcla de estilos.
- **Validaciones numéricas** (área del proyecto, porcentajes de indirectos/imprevistos/utilidad): ya rechazan correctamente valores negativos, vacíos o no numéricos revirtiendo al valor anterior.
- **Enlaces internos**: se verificaron todas las rutas hardcodeadas de la aplicación — ninguna rota o mal escrita.
- **Ortografía**: sin dobles espacios ni palabras duplicadas consecutivas en el texto visible.
- **Responsive en tablet (768px) y mobile angosto (360px)**: sin desbordamiento horizontal en ninguna pantalla probada.

## Cosas que conscientemente decidí NO cambiar

- **Una imagen de producto de EPA** (un taladro Einhell) muestra un ícono de "video no disponible" en vez de la foto real. Se investigó a fondo: no es una imagen rota (carga perfectamente, es un JPEG real sin error), es el archivo que EPA mismo sirve para ese producto — un problema de datos del proveedor, no de cómo la aplicación lo procesa. Se confirmó que no es un patrón repetido en el catálogo (ningún otro producto comparte esa imagen). Corregirlo de verdad requeriría inspeccionar el contenido visual de cada imagen del catálogo, que es una funcionalidad nueva fuera del alcance de este sprint.
- **Migrar `<img>` a `<Image />` de Next.js**: eslint lo sugiere por rendimiento, pero es un cambio de mayor alcance (pipeline de optimización de imágenes, configuración de dominios remotos permitidos) que no calza con "no cambiés la arquitectura" a días de empezar pruebas con usuarios.
- **El error de eslint preexistente en `useProductosSimilares.ts`**: viene de una fase muy anterior de la sesión, no se originó en este trabajo, no bloquea build ni afecta el comportamiento en producción. Se decidió no tocarlo para no mezclar una corrección de otra área del código en este sprint de cierre.
- **Aumentar la cobertura de obra gruesa o agregar más proveedores**: ya documentado extensamente en `ESTRATEGIA_EXPANSION_PROVEEDORES.md`, `COBERTURA_VIVIENDA_TIPICA.md` y `COBERTURA_POR_TIPO_PROYECTO.md` — es una decisión de negocio y de datos, no una corrección de calidad de este sprint.

## Riesgos conocidos

- La imagen aislada de EPA descrita arriba puede aparecer si un ingeniero busca justo ese producto durante una sesión de prueba — impacto cosmético mínimo, no bloqueante.
- No se hizo un análisis cuantitativo de tamaño de bundle (esta versión de Next.js/Turbopack no imprime tamaños por ruta en el build) — el rendimiento se evaluó de forma cualitativa (cero errores de consola, carga diferida agregada donde correspondía), no con herramientas de medición dedicadas.
- El catálogo depende de EPA para la mayoría de los materiales de obra gruesa (varilla, agregados, block) — no es un bug de esta beta, es una limitación de cobertura ya documentada en los análisis de negocio previos; puede notarse si un ingeniero de prueba intenta cotizar un proyecto de construcción completa en vez de una remodelación.
- El entorno de producción (Render/Vercel) está desactualizado respecto a este código local por varios sprints — nada de lo corregido en esta sesión existe todavía en producción hasta que se despliegue.

---

## Checklist de despliegue

- [ ] Confirmar `NEXT_PUBLIC_API_URL` de producción apunta al backend real, no a localhost.
- [ ] Verificar que el backend de producción tenga desplegado el mismo código que este repositorio local (producción está detrás por varios sprints según el estado conocido de esta sesión).
- [ ] Correr `npm run build` en el entorno de destino y confirmar que compila sin errores (verificado localmente: limpio).
- [ ] Verificar que CORS del backend permita el dominio real de producción del frontend.
- [ ] Confirmar que la base de datos de producción tiene datos reales y actualizados, no una copia de desarrollo.
- [ ] Verificar HTTPS/certificado SSL activo en el dominio de producción.
- [ ] Smoke test manual post-deploy: buscar un material real, ver el detalle, crear un proyecto, agregar un ítem, revisar la cotización completa.
- [ ] Confirmar que no quedan proyectos ni datos de prueba en la base de datos de producción antes de invitar usuarios reales.

## Checklist para empezar pruebas con usuarios

- [ ] Entorno accesible por una URL pública real (no localhost) para que los ingenieros participantes lo usen sin instalar nada.
- [ ] Protocolo de validación ya diseñado (`PROTOCOLO_VALIDACION_USUARIOS.md`): reclutar 5-8 ingenieros con proyecto activo, siguiendo el perfil ya definido.
- [ ] Formulario de observación (sección 9 del protocolo) impreso o disponible en tablet durante las sesiones.
- [ ] Confirmar que el catálogo tiene datos frescos antes de las sesiones (fecha de la última actualización de precios).
- [ ] Tener presente `COBERTURA_POR_TIPO_PROYECTO.md`: si es posible, dirigir a los participantes hacia proyectos de remodelación (baño, cocina, tapia, cochera) en vez de construcción completa, que es donde el catálogo hoy responde mejor.
- [ ] Tener este documento a mano durante las sesiones — ningún riesgo conocido listado arriba debería tomar al equipo por sorpresa si aparece.

---

## Verificación ejecutada

`tsc --noEmit`, `eslint`, `next build` — todo pasa limpio (único error de eslint preexistente, sin relación con este sprint, confirmado sin diff). Backend: 118 pruebas automatizadas OK, `verificar_catalogo.py` todas las verificaciones pasan. Recorrido real con Playwright por las 6 pantallas en tres tamaños de viewport (desktop, tablet 768px, mobile 360px), navegación por teclado verificada con foco visible, página 404 verificada con status HTTP real. Cero errores de consola en todo el recorrido. Datos de prueba de la verificación eliminados; catálogo de proyectos en la línea base (12 proyectos / 26 ítems).

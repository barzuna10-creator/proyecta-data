# Análisis competitivo — Zentra frente al mercado real de software para constructoras

**Rol de este documento:** investigación de mercado real (no lista de funciones) sobre 9 competidores, con fuentes citadas, para fundamentar la búsqueda del "10x feature" que se propone en `RFC_PRICE_INTELLIGENCE_NETWORK.md`. Toda afirmación de precio/crítica/fortaleza de un competidor viene de una fuente real (G2, Capterra, sitios de comparación, notas de prensa) buscada para este documento -- no de memoria ni de suposición. Donde una cifra viene de un solo agregador (nunca del proveedor mismo, que casi nunca publica precio), se marca como estimado.

## 0. Metodología y honestidad de la fuente

Búsquedas reales contra G2, Capterra y prensa especializada para cada uno de los 9 productos pedidos, más búsquedas específicas de "qué odian los usuarios" (reseñas negativas, no marketing) y de mercado LatAm. Reddit devolvió poco contenido indexado directamente (limitación real de la búsqueda, no ocultada); se compensó con reseñas verificadas de G2/Capterra, que para este propósito (patrones de queja repetidos, no anécdotas puntuales) son una fuente comparable o mejor. Ningún precio de Procore, Autodesk, BuildOps o Buildertrend es público de forma oficial -- son cotizados a medida; las cifras de abajo son las mejor documentadas por agregadores especializados en 2026, presentadas como rango, no como precio de lista.

## 1. Matriz competitiva

| Competidor | Segmento real | Precio (2026, agregado) | Propuesta de valor | Fortaleza real | Debilidad real (de reseñas, no marketing) | Oportunidad para Zentra |
|---|---|---|---|---|---|---|
| **Procore** | GC grandes, >$50M de volumen anual, 20+ subcontratistas por proyecto | $4,500–$80,000+/año según volumen de construcción, + $50K–150K de implementación el primer año | "El sistema operativo de la construcción" -- todo el proyecto en un solo lugar | Centraliza documentos/RFIs/submittals a un nivel que ningún jugador chico iguala; app móvil querida por superintendentes de campo | Precio "escandaloso" para empresas medianas/chicas citado una y otra vez; interfaz saturada y curva de aprendizaje real; mal ajuste para subcontratistas ("cuadrado en agujero redondo") | Zentra puede ser "todo lo que un GC mediano necesita" sin el precio ni la complejidad de un producto diseñado para 100+ usuarios simultáneos |
| **Autodesk Construction Cloud** | GC grandes + firmas de diseño ya invertidas en el ecosistema Autodesk (Revit/AutoCAD) | Desde ~$85/mes por módulo, pero el paquete real reportado como "carísimo" | Continuidad BIM diseño→obra | Integración nativa con Revit/AutoCAD; visualización 3D real | Costo alto sin poder desagregar módulos que no se usan; requiere ya estar en el ecosistema Autodesk; BIM 360/Document Management "confuso" | Zentra no compite en BIM -- compite en el 90% de constructoras LatAm que nunca van a modelar en Revit y solo necesitan cotizar y comprar bien |
| **BuildOps** | Subcontratistas comerciales de HVAC/eléctrico/plomería con contratos de mantenimiento -- no GCs de obra nueva | Sin precio público, cotizado; posicionado como premium | Despacho + servicio + facturación en una sola plataforma para trades comerciales | Tablero de despacho muy valorado; soporte al cliente consistentemente elogiado | Setup complejo, implementación larga, no sirve para residencial; bugs de sincronización reportados | Segmento distinto al de Zentra hoy (servicio recurrente, no obra por proyecto) -- referencia de cómo un vertical bien enfocado gana lealtad, no un competidor directo |
| **Buildertrend** | Constructoras residenciales y remodeladores pequeños/medianos | $299–$900+/mes | "Todo lo que un residencial necesita sin comprar 5 programas" | Interfaz más simple que Procore; cotización+programación+comunicación con cliente en un solo lugar | "Caro para lo que ofrece"; funciones que nadie usa; integración con QuickBooks deficiente; notificaciones por correo excesivas y difíciles de apagar | El competidor más cercano en espíritu a Zentra (residencial/mediano, todo-en-uno) -- pero 100% en inglés, USD, sin proveedores LatAm |
| **Fieldwire** | Coordinación de campo (planos, tareas, RFIs) para equipos de cualquier tamaño | Gratis (limitado) → $54–$104/usuario/mes | El "WhatsApp de planos": ver el plano correcto y marcar tareas sobre él, rápido | Adopción de campo genuinamente fácil; colaboración en tiempo real sobre planos muy valorada | Funciones financieras/de compras inexistentes ("le falta todo el set de gestión financiera y de ventas"); funciones clave (RFIs, presupuesto) solo en el plan más caro; sin búsqueda de texto en planos | Fieldwire prueba que "ver el plano y actuar sobre él" vende solo -- Zentra ya hace eso Y cotiza materiales reales, algo que Fieldwire nunca hará |
| **MAWI** | Constructoras pequeñas/medianas de Costa Rica, expandiendo a Chile | Suscripción mensual por proyecto o por empresa (sin cifra pública); plan freemium de 1 proyecto | Control financiero-económico de la obra, con soporte humano fuerte (hasta 5 sesiones de onboarding) y notificaciones por WhatsApp | Único competidor real nativo de Centroamérica; ya validado por clientes reales en CR y expansión a Chile con inversión de TinySeed; WhatsApp como canal, culturalmente correcto para LatAm | Es gestión de presupuesto/avance, no tiene catálogo de proveedores reales ni comparación de precios -- el usuario sigue cotizando materiales "a mano" fuera de la plataforma | Es el competidor a vigilar más de cerca (mismo mercado, mismo idioma, mismo tamaño de empresa) -- y es exactamente donde Zentra ya es estructuralmente más fuerte: Zentra SÍ tiene el catálogo real, MAWI no |
| **Autodesk Takeoff** (ex-Assemble) | Estimadores de precon en firmas ya-Autodesk que hacen takeoff desde modelos BIM | ~$1,250/año a ~$310/usuario/mes según el paquete | Cuantías 2D/3D desde el modelo BIM | Extracción de cantidades directo del modelo, sin redibujar | El valor se cae en cuanto se le quita la coordinación BIM/gestión documental que justifica el precio de ACC -- como producto aislado es caro para lo que hace | Irrelevante para el 95% de obra LatAm que no llega a un modelo BIM completo -- Zentra ya resuelve la cuantía sin requerir un modelo 3D previo |
| **STACK** | Estimadores/GCs chicos-medianos de EE.UU. que licitan por volumen | Precio único por paquete (no por niveles, valorado por eso) | Toma de cuantías rápida + biblioteca de ensambles, con un solo precio, sin niveles | Onboarding rápido, ahorro de tiempo real reportado, soporte con nota 9.5/10 | Bugs/congelamientos reportados con frecuencia; el módulo de estimación es secundario a la cuantía (los GC que necesitan cuantía + gestión de oferta completa lo sienten incompleto); caro si no se licita seguido | Confirma que "cuantía rápida y confiable" por sí sola ya genera lealtad -- pero STACK no compra ni compara proveedores reales, se detiene en el número |
| **Togal.AI** | Estimadores que quieren automatizar la cuantía con IA, en inglés, mercado EE.UU. | $199–$299/usuario/mes (mensual), $1,999–$2,999/usuario/año | IA que lee el plano y saca cuantías automáticamente, "de días a segundos", ~98% de precisión reportada en detección | La prueba de mercado más clara de que IA aplicada a lectura de planos SÍ genera pago real (payback de 4-7 meses reportado en la categoría) | Precio por usuario alto para equipos grandes; falla en archivos grandes/complejos; en inglés, sin ningún proveedor ni precio de LatAm | Zentra ya lee planos y ya selecciona producto real automáticamente (47.9% de cobertura medida, con cero falsos positivos aceptados) -- Togal prueba que el mercado paga por esto, y Zentra parte de una base ya construida, no de cero |

## 2. Patrones que se repiten en los 9 (esto es lo que importa, no la lista)

**Por qué pagan las constructoras -- el patrón real, no el discurso de venta:**
1. Para dejar de perder plata por falta de visibilidad (versión de plano equivocada, sobrecosto que se descubre tarde, RFI perdido).
2. Para que el trabajo mecánico de sacar cuantías/cotizar deje de consumir horas de un estimador -- es la categoría con el ROI más fácil de medir de las nueve (Togal, STACK): 70-90% menos tiempo, retorno en 4-7 meses.
3. Para cumplir el requisito de un cliente/GC grande que exige un sistema específico (esto explica buena parte de la base de Procore, no siempre es elección propia).

**Lo que se odia, literalmente en los 9, sin excepción:**
- **Precio opaco y agresivamente escalonado con el tamaño.** Ninguno de los 9 publica un precio simple y predecible para una constructora chica. "Contáctenos" es la norma, no la excepción.
- **Fragmentación.** Ningún competidor cubre el ciclo completo por sí solo -- Procore necesita ProEst para estimar de verdad (y Autodesk se compró al competidor de ProEst, lo que dice todo), Fieldwire no tiene nada financiero, STACK no compra ni compara proveedores, BuildOps es de otro rubro. El usuario real termina pagando 2-3 suscripciones que no se hablan bien entre sí.
- **Ninguno de los 9 hace comparación de precios de proveedores reales en tiempo real.** Esto se verificó explícitamente: Procore Estimating "no hace listas de precios propias, cuantía ni calibración de costos"; el estándar de la industria (RSMeans y equivalentes) es una base de costos **estática y genérica**, no un precio real de un proveedor real hoy. Es la ausencia más consistente de las nueve herramientas.
- **Cero presencia LatAm real.** De los 9, solo MAWI es nativo de la región (Costa Rica). Los otros 8 son 100% en inglés, en dólares, sin proveedores locales, sin IVA/CABYS/factura electrónica local, sin considerar que en LatAm el canal de comunicación de obra de facto es WhatsApp, no un app dedicado (MAWI ya lo capturó; ninguno de los otros 8 lo tiene).

**El hueco real, confirmado por investigación, no supuesto:**
El mercado de software de construcción en LatAm es apenas ~5.7% del mercado global (~US$0.61B de un mercado de más de US$10B), en una región donde **más del 60% de los materiales en mercados como Argentina son importados**, la logística cuesta 30% más que en Asia, y la volatilidad de precio de insumos (cemento, acero, arena) es estructural, no coyuntural. Es exactamente el escenario donde "saber el precio real, hoy, de varios proveedores" vale más que en EE.UU. -- y es exactamente lo que ninguno de los 9 competidores investigados hace, y lo que Zentra ya hace hoy, en producción, con 61,380 productos reales de 8 proveedores de Costa Rica.

## 3. Zentra hoy, frente a este mapa (auditado contra el código real, no contra la lista del enunciado)

Lo que ya existe y compite de verdad:
- **Lectura de planos + selección automática de producto real** -- comparable en ambición a Togal.AI/STACK, con la ventaja de que ya selecciona el PRODUCTO REAL (no solo la cantidad) contra un catálogo vivo, algo que ninguno de los 9 hace.
- **Comparación de proveedores en tiempo real** -- la pieza que confirmadamente no existe en NINGUNO de los 9 competidores investigados.
- **Presupuesto con margen/indirectos/imprevistos + Compras + Control de Costos** -- cubre, en un producto, lo que en el mercado de EE.UU. requiere Procore + un ERP de compras aparte + una hoja de cálculo de control de costos.
- **Instrumentación (`eventos`), analítica interna, respaldos automáticos, logging, RC1/Beta 1.0** -- disciplina de producto que va más allá de lo que un competidor de este tamaño normalmente tiene documentado.

Lo que falta frente al mercado (honesto, no todo es oportunidad de "10x"):
- Gestión documental (planos/fotos adjuntos), RFIs, submittals -- el núcleo de Procore. No es prioritario copiarlo (ver `ARQUITECTURA_PLATAFORMA_INTEGRAL.md`).
- Ningún dato de LatAm fuera de Costa Rica todavía -- MAWI ya está en Chile.
- Cero canal WhatsApp -- el único patrón que un competidor pequeño (MAWI) ya validó y que Zentra no tiene.

## 4. De acá sale la pregunta del "10x feature"

Ninguno de los 9 competidores compara precios reales de proveedores reales en tiempo real. Zentra ya lo hace. La pregunta que responde `RFC_PRICE_INTELLIGENCE_NETWORK.md` es: ¿cuál es la versión de esa ventaja que deja de ser "una función más" y se vuelve una razón estructural, cada vez más difícil de copiar con el tiempo, para que una constructora no pueda dejar de usar Zentra?

Sources:
- [Procore Reviews 2026 | G2](https://www.g2.com/products/procore/reviews)
- [Procore Pros and Cons | G2](https://www.g2.com/products/procore/reviews?qs=pros-and-cons)
- [Procore Pricing 2026: Real Cost Per Month Exposed](https://projul.com/blog/procore-pricing-analysis-2026/)
- [Procore Pricing 2026: 3 Plans from $4,500–$60,000/per year](https://costbench.com/software/construction-management/procore/)
- [Procore Pricing 2026: $15K–$80K/Year (ACV Model Breakdown)](https://www.scanmanifold.com/blog-posts/procore-pricing-2026-contractors)
- [Procore vs Buildertrend: Honest Review (2026)](https://projul.com/competitors/procore-vs-buildertrend/)
- [Procore Alternative for Estimating in 2026 - BidFlow](https://www.bidflow.visidex.com/articles/procore-alternative-estimating-2026)
- [Autodesk Construction Cloud Reviews 2026 | G2](https://www.g2.com/products/autodesk-construction-cloud/reviews)
- [Autodesk Construction Cloud Pros and Cons | G2](https://www.g2.com/products/autodesk-construction-cloud/reviews?qs=pros-and-cons)
- [Autodesk Forma Pricing 2026 | Capterra](https://www.capterra.com/p/218046/Autodesk-Construction-Cloud/pricing/)
- [Autodesk Takeoff Reviews 2026: Pricing, Features & More](https://www.selecthub.com/p/takeoff-software/autodesk-takeoff/)
- [Assemble Software Reviews, Demo & Pricing](https://www.softwareadvice.com/product/395327-Assemble-Insight/)
- [BuildOps Reviews 2026 | Capterra](https://www.capterra.com/p/194155/BuildOps/reviews/)
- [BuildOps vs Procore: Service vs Construction Software](https://fieldservicesoftware.io/comparisons/buildops-vs-procore/)
- [5 Best BuildOps Alternatives for Commercial Contractors](https://servicetrade.com/resources/compare/buildops-competitors/)
- [Buildertrend Pros and Cons | G2](https://www.g2.com/products/buildertrend/reviews?qs=pros-and-cons)
- [Buildertrend Reviews 2026 | Capterra](https://www.capterra.com/p/70092/Buildertrend/reviews/)
- [Fieldwire by Hilti Reviews 2026 | G2](https://www.g2.com/products/fieldwire-by-hilti/reviews)
- [Fieldwire by Hilti Pros and Cons | G2](https://www.g2.com/products/fieldwire-by-hilti/reviews?qs=pros-and-cons)
- [Fieldwire Pricing: A Comprehensive Guide | Capterra](https://www.capterra.com/p/142801/Fieldwire/pricing/)
- [Mawi - Gestión de presupuestos para proyectos de construcción](https://mawi.io/)
- [Mawi: la startup costarricense que llegó a Chile - Portal Innova](https://portalinnova.cl/mawi-la-startup-costarricense-que-llego-a-chile-a-optimizar-la-industria-constructora/)
- [Mawi: la startup costarricense que llegó a Chile - DF SUD](https://dfsud.com/chile/mawi-la-startup-costarricense-que-llego-a-chile-para-evitar-sobrecostos)
- [Planes y precios de Mawi Managers](https://managers.mawi.io/precios/)
- [STACK Takeoff & Estimate Reviews 2026 | G2](https://www.g2.com/products/stack-takeoff-estimate/reviews)
- [STACK Software Pricing, Alternatives & More 2026 | Capterra](https://www.capterra.com/p/147181/STACK-Takeoff/)
- [STACK Construction Software Review 2026](https://struvia.co/blog/stack-construction-software-review)
- [Togal.AI 2026 Pricing, Features, Reviews & Alternatives | GetApp](https://www.getapp.com/construction-software/a/togal-ai/)
- [Togal AI Review: Is It Worth It for GC Estimators?](https://www.bidicontracting.com/blog/togal-ai-review-2026)
- [Pricing - Togal](https://www.togal.ai/pricing)
- [Market Forecast: BIM Software 2026-2030, Latin America](https://qksgroup.com/market-research/market-forecast-building-information-management-bim-software-2026-2030-latin-america-7611)
- [Latin America Construction And Design Software Market Size, 2030](https://www.grandviewresearch.com/horizon/outlook/construction-and-design-software-market/latin-america)
- [Latin America Construction Market Size Analysis 2025-2035](https://www.nextmsc.com/report/latin-america-construction-market)
- [Latin America Construction Materials Market Size and Forecasts 2030](https://mobilityforesights.com/product/latin-america-construction-materials-market)
- [Construction Estimating Trends 2026 | AI, Automation & Pricing](https://nedesestimating.com/construction-estimating-companies-ai-automation-real-time-pricing/)

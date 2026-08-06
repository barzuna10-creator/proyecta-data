# Release Candidate 1

Implementa exclusivamente los 6 puntos pedidos, cada uno para eliminar un
bloqueo específico y nombrado en `BETA_1.0_CHECKLIST.md`. Nada más se
tocó -- ninguna funcionalidad nueva, ninguna mejora de UX, ningún módulo
fuera de lo que estos 6 puntos requerían para existir de verdad.

---

## Los 6 cambios, cada uno contra el bloqueo que elimina

### 1. Autenticación real -- elimina el hallazgo 4.1

**Antes**: `X-Propietario-Id` era un UUID que el propio navegador
generaba y enviaba, sin que el servidor verificara nada.

**Ahora**: cuentas reales con contraseña (`api/auth.py`) -- hash
PBKDF2-HMAC-SHA256 con sal por usuario (200,000 iteraciones, librería
estándar, sin dependencias nuevas), sesiones server-side con token
opaco y expiración de 30 días (mismo patrón que `token_compartido`, ya
usado en este código base para el link de solo lectura). El cambio de
mayor apalancamiento de todo el sprint: `api/identidad.py` es el único
punto por el que pasan los 11 endpoints que ya usaban
`Depends(obtener_propietario_id)` -- reescribir esa única función para
que verifique un token real, en vez de confiar en el header, protegió
los 11 sin tocar ni un router.

**Un bug real encontrado durante la propia verificación**: la primera
versión de `POST /auth/logout` no tenía `Header(...)` en el parámetro
`authorization` -- FastAPI lo trataba como un query param invisible en
vez de leer el encabezado real, así que `cerrar_sesion()` nunca se
llamaba y el token seguía funcionando después de "cerrar sesión".
Encontrado probando el flujo real con `curl`, no por inspección de
código -- corregido, y se agregó una prueba de regresión específica
(`test_cerrar_sesion_invalida_el_token_de_inmediato`) para que no pueda
volver a pasar en silencio.

### 2. Respaldo automático -- elimina los hallazgos 1.4 y 5.1

**Antes**: `database/respaldar_db.py` existía y funcionaba, pero nada lo
ejecutaba -- "una promesa sin cumplir".

**Ahora**: `api/main.py` agenda el respaldo desde un hilo en segundo
plano al arrancar el proceso -- una vez al iniciar, y después cada 6
horas -- sin depender de que alguien configure un cron en la plataforma
de despliegue. Verificado en vivo: al reiniciar el servidor de
desarrollo, el log muestra `✅ Respaldo creado: database/respaldos/
proyecta_...db` sin que nadie lo dispare a mano.

### 3. Logging estructurado -- elimina los hallazgos 1.1 y 8.2

**Antes**: cero registro de errores del backend -- ni una integración de
logging, ni un solo `try/except` con traza.

**Ahora**: `api/observabilidad.py`, un middleware que registra cada
request (método, ruta, estado, duración, si había sesión) a
`logs/proyecta_api.log` y a consola, más traza completa en cualquier
excepción no manejada. Mismo patrón que `capa_intencion.py` (el único
precedente de logging que ya existía en este código base), aplicado a
la API completa en vez de a un solo módulo experimental. Verificado en
vivo contra el flujo real de Playwright -- el log muestra, línea por
línea, el registro, el feedback, el logout, el login fallido con
contraseña incorrecta (`estado=401`) y el logout final.

### 4. Canal de feedback -- elimina el hallazgo 7.1

**Antes**: cero mecanismo para que un ingeniero reporte un problema
dentro del producto.

**Ahora**: botón "Reportar un problema" en el `Navbar` (solo visible con
sesión activa), un formulario mínimo, y `POST /feedback` que guarda el
mensaje con el usuario y la página desde donde se mandó. Verificado en
vivo: el mensaje de prueba quedó en la tabla `feedback` con el
`usuario_id` y la ruta correctos.

### 5. Analítica mínima -- elimina el hallazgo 8.1

**Decisión deliberada**: no se construyó una segunda tubería de
analítica aparte del logging del punto 3 -- el mismo registro
(método/ruta/estado/duración/usuario, por request) ya responde "cuánto
se usa" y "dónde falla" sin duplicar el mecanismo. Construir un sistema
separado habría sido agregar algo que el logging ya cubre, exactamente
lo que esta misión pidió no hacer.

### 6. Configuración de despliegue versionada -- elimina el hallazgo 6.1

**Antes**: ningún `render.yaml` ni `Procfile` en ningún repo -- la
configuración real de producción era, literalmente, información que no
existía en ningún lado.

**Ahora**: `render.yaml` en la raíz del backend, con el comando real
(`--workers 4`, nunca `--reload`), las variables de entorno ya
documentadas en `DEPLOYMENT.md`, y -- lo más importante para el hallazgo
5.2 -- un disco persistente declarado (`/data`) con `DATABASE_PATH`
apuntando ahí. `DEPLOYMENT.md` se actualizó con el paso que sigue
siendo manual: alguien con acceso al dashboard de Render tiene que
aplicar este Blueprint o migrar el servicio existente -- tenerlo
versionado en git convierte "nadie sabe cómo está configurado" en "hay
una receta concreta, pendiente de aplicarse", pero no reemplaza
confirmar que se aplicó.

---

## Verificación

- **Backend: 452/452 pruebas, `OK`, sin regresiones** (432 preexistentes
  + 18 nuevas de `tests/test_auth.py` + 2 nuevas de `tests/test_feedback.py`).
- `npx tsc --noEmit` → limpio.
- `npx next build` → compila, 9 rutas (`/login` nueva).
- **Playwright end-to-end**, flujo real completo: acceder a `/proyectos`
  sin sesión → redirige a `/login` → registrarse con cuenta real →
  crear un proyecto → enviar feedback (confirmado guardado en la base)
  → cerrar sesión → intentar volver al proyecto sin sesión → rebota a
  `/login` de nuevo → volver a entrar con la misma cuenta → el proyecto
  sigue ahí, con el `propietario_id` correcto (confirmado contra la
  base de datos directamente) → contraseña incorrecta rechazada,
  usuario se queda en `/login` → la página pública del catálogo
  (`/`) sigue sin pedir sesión. Cero errores de consola inesperados --
  el único registrado es el 401 del intento de login con contraseña
  incorrecta, que es el comportamiento correcto de esa prueba, no un
  fallo.
- Cuentas y proyectos de prueba creados durante la verificación
  eliminados al terminar.

---

## ¿Publicarías Proyecta mañana?

**Sí, con una condición operativa que hay que resolver antes del primer
despliegue de este código, no después.**

De los 7 bloqueos identificados en `BETA_1.0_CHECKLIST.md`, 5 están
resueltos y verificados en vivo, no solo escritos:

- Sin autenticación real (4.1) → resuelto -- cuentas, contraseñas con
  hash, sesiones server-side verificadas.
- Respaldo escrito pero sin ejecutar (1.4/5.1) → resuelto -- corre solo,
  confirmado en el arranque real del servidor.
- Sin logging de errores (1.1/8.2) → resuelto -- cada request queda
  registrado, confirmado contra el log real de una corrida real.
- Sin canal de feedback (7.1) → resuelto -- confirmado guardado en la
  base de datos.
- Sin métricas de uso (8.1) → resuelto -- mismo mecanismo que el
  logging, por diseño.

**Los 2 restantes (5.2, durabilidad de datos ante un redeploy; 6.1,
configuración real de Render sin confirmar) ya no son un vacío -- son
una sola acción pendiente, con una receta concreta y versionada
(`render.yaml`): que alguien con acceso al dashboard de Render lo
aplique, o confirme que el servicio ya tiene un disco persistente antes
de desplegar este commit.** Esto no es código que falte -- es el único
paso de infraestructura que este entorno de trabajo no puede ejecutar
por sí mismo (no hay acceso al dashboard real de Render desde acá). Si
ese paso se confirma antes de invitar a los 10 ingenieros, no queda
ningún bloqueo pendiente de los identificados en `BETA_1.0_CHECKLIST.md`.
Si se despliega este código sin confirmarlo primero, el riesgo es
exactamente el mismo que documentaba el checklist anterior: un redeploy
sin disco persistente borra los datos de los 10 ingenieros sin ningún
aviso -- por eso la respuesta es un "sí" condicionado a ese único paso,
no un "sí" sin reservas.

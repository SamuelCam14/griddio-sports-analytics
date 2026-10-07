# 🚀 Guía de Onboarding para Desarrolladores (Zero to Hero)

**Proyecto:** Sports Analytics Platform  
**Audiencia:** Desarrolladores Junior, Ingenieros Fullstack y Cuants recién integrados.  
**Tiempo estimado de lectura:** 15 minutos  

Bienvenido al equipo de ingeniería de **Sports Analytics**. Esta guía está diseñada para que puedas clonar el repositorio, levantar el entorno completo en menos de 10 minutos y comprender a fondo cómo está estructurado el código sin perderte en tecnicismos innecesarios.

---

## 1. 📋 Prerrequisitos de tu Máquina Local

Antes de comenzar, asegúrate de tener instaladas las siguientes herramientas en tu sistema operativo (Linux / macOS / WSL2 en Windows):

| Herramienta | Versión Recomendada | Comando de Comprobación |
|---|---|---|
| **Docker Engine & Docker Compose** | 24.x o superior | `docker compose version` |
| **Node.js** | v20.x o v22.x LTS (v26 compatible) | `node -v` |
| **npm** | v10.x o superior | `npm -v` |
| **Python** | 3.12 o superior (Host: 3.12 - 3.14) | `python3 --version` |
| **Git** | 2.x o superior | `git --version` |

---

## 2. ⚡ Puesta en Marcha Rápida (Paso a Paso)

### Paso 1: Configurar Variables de Entorno (`.env`)
En la raíz del proyecto (`sports_analytics/`), existe un archivo de plantilla `.env.example`. Copia este archivo a `.env`:

```bash
cp .env.example .env
```

Abre `.env` con tu editor preferido. Los valores por defecto funcionan de inmediato para desarrollo local con Docker. Lo único opcional pero recomendado es tu API Key de cuotas:

```env
# Configuración de The Odds API (sin clave, las cuotas no estarán disponibles; no se generan datos sustitutos)
ODDS_API_KEY=tu_api_key_aqui
```

### Paso 2: Levantar la Infraestructura con Docker
Ejecuta el siguiente comando en la raíz del proyecto para descargar e iniciar los contenedores de PostgreSQL (TimescaleDB), Redis, la API de FastAPI y el Worker de Celery:

```bash
sudo docker compose up -d
```

Verifica que todos los contenedores estén en estado `Up`:
```bash
sudo docker compose ps
```

Deberías ver 4 servicios activos:
1. `sports_db` (Puerto `5432` expuesto en localhost)
2. `sports_redis` (Puerto `6379` expuesto en localhost)
3. `sports_api` (Puerto `8000` expuesto en localhost)
4. `sports_worker` (Worker de Celery en segundo plano)

> [!WARNING]
> **¡Atención con el Puerto 8000!**  
> El contenedor Docker `sports_api` ya está utilizando el puerto `8000` de tu máquina. **NO** intentes ejecutar `uvicorn app.main:app` manualmente en tu terminal host en el puerto 8000, o recibirás el error `[Errno 98] Address already in use`.  
> Todo cambio que hagas en el código dentro de `backend/` se sincroniza automáticamente con el contenedor gracias al volumen montado en tiempo real (`./backend:/app`).

### Paso 3: Datos iniciales y límites de los seeds
Antes de ejecutar seeds, revisa cuáles son catálogos estáticos y cuáles provienen de fuentes deportivas. No uses seeds o CSV de ejemplo para entrenamiento ni los presentes como información actual. La ingesta real y el pipeline de entrenamiento siguen pendientes: [plan de refactorización](DATA_ML_REFACTOR_PLAN.md).

No ejecutes `seed_database.py` para cargar partidos en un entorno de entrenamiento/producción hasta auditar qué datos genera y acreditar su fuente. Los catálogos estáticos de tarjetas también deben identificarse claramente como catálogos, no como cotizaciones reales. Sigue el plan de datos antes de habilitar seeds de dominio deportivo.

### Paso 4: Levantar el Servidor Frontend (React + Vite)
Abre una nueva terminal en tu máquina host y dirígete a la carpeta `frontend`:

```bash
cd frontend
npm install
npm run dev
```

El servidor de desarrollo Vite se iniciará en `http://localhost:5173`.  
Abre tu navegador en `http://localhost:5173` y verás la plataforma funcionando con sus tres módulos principales:
* 🏀 **Apuestas EV+**
* 🎯 **Micro-Parlay Builder**
* 🃏 **Sports Cards & NIL**

---

## 3. 🗺️ Mapa de Orientación: ¿Dónde Encuentro Cada Cosa?

Si necesitas modificar una funcionalidad específica, esta guía rápida te llevará directamente al archivo correcto:

### A. Lógica Matemática y Cuantitativa (`backend/app/services/math/`)
* **¿Quieres cambiar cómo se calcula el EV?** 👉 Revisa `services/math/ev.py`.
* **¿Quieres ajustar la fórmula del Criterio de Kelly o su cap de seguridad?** 👉 Revisa `services/math/kelly.py`.
* **¿Quieres ver cómo eliminamos el margen de la casa de apuestas (*vig*)?** 👉 Revisa `services/math/odds_converter.py`.
* **¿Quieres ver la simulación de quiebra estocástica de Monte Carlo?** 👉 Revisa `services/math/monte_carlo.py`.

### B. Módulo de Tarjetas Deportivas & NIL (`backend/app/services/cards/`)
* **¿Quieres ajustar el descuento por Efecto Uniforme (-52%) o multiplicadores de PSA 10?** 👉 Revisa `services/cards/multipliers.py`.
* **¿Cómo se estima el Fair Market Value (FMV)?** 👉 Revisa `services/cards/hedonic_pricing.py`.
* **¿Dónde se calculan las comisiones de eBay para arbitraje?** 👉 Revisa `services/cards/arbitrage_detector.py`.
* **¿Cómo se proyecta la serie temporal a 90 días?** 👉 Revisa `services/cards/prophet_forecaster.py`.

### C. Frontend React (`frontend/src/`)
* **¿Dónde están las llamadas HTTP a la API?** 👉 `src/services/api.ts`.
* **¿Dónde está la lógica de estado del usuario (Bankroll, Deporte activo)?** 👉 `src/store/useAppStore.ts` (Zustand).
* **¿Dónde están los Custom Hooks que manejan React Query?** 👉 `src/hooks/` (`usePredictions.ts`, `useOdds.ts`, `useParlay.ts`, `useCards.ts`).
* **¿Dónde están los componentes visuales?** 👉 `src/components/` divididos por dominio (`ui/`, `games/`, `parlay/`, `cards/`).

---

## 4. 🛠️ Flujo de Trabajo y Buenas Prácticas de Desarrollo

### Regla de Oro: Principio de Responsabilidad Única (SRP)
Cuando te asignen una tarea, **no** crees funciones gigantes que hagan múltiples cosas a la vez. Sigue este patrón:
1. Si creas una función matemática, haz que sea **pura**: que reciba números y devuelva un número o un dataclass, sin hacer llamadas a bases de datos ni peticiones HTTP.
2. Si creas una vista en React, extrae la lógica de datos hacia un Custom Hook en `src/hooks/`.
3. Nunca utilices `any` en TypeScript. Define siempre interfaces o tipos en `src/services/api.ts` o en archivos de tipos dedicados.

### Documentación de Código
Cada función en Python debe contar con docstrings detallando:
* Propósito de la función.
* Parámetros recibidos (`Args:`).
* Valor retornado (`Returns:`).
* Explicación matemática breve si aplica.

---

## 5. 🔍 Pruebas y Verificación

### Verificar que la API responde
Puedes probar rápidamente los endpoints en vivo mediante `curl`:

```bash
# Probar estado de salud
curl http://localhost:8000/health

# Consultar picks con ventaja del día
curl http://localhost:8000/api/v1/predictions/today

# Consultar catálogo de tarjetas
curl http://localhost:8000/api/v1/cards/catalog

# Consultar micro-parlays recomendados
curl http://localhost:8000/api/v1/parlay/recommended?bankroll=1000
```

También puedes abrir la documentación interactiva Swagger en tu navegador:  
👉 **`http://localhost:8000/docs`**

### Verificar Compilación del Frontend
Para verificar que no existan errores de tipos ni de sintaxis en TypeScript:
```bash
cd frontend
npm run build
```
Debe compilar en menos de 1 segundo sin advertencias ni errores.

---

## 6. ❓ Preguntas Frecuentes y Solución de Problemas (Troubleshooting)

#### P: Hice un cambio en el backend pero no lo veo reflejado en la API.
**R:** El contenedor `sports_api` corre con `--reload` en uvicorn. Si agregaste un nuevo archivo o hubo un error de sintaxis que tumbó el proceso uvicorn dentro del contenedor, revisa los logs:
```bash
sudo docker compose logs -f api
```

#### P: En el frontend aparece un error de conexión hacia la API (Network Error o 502).
**R:** 
1. Asegúrate de que el contenedor `sports_api` esté en estado `Up` ejecutando `sudo docker compose ps`.
2. Verifica que el proxy de Vite en `frontend/vite.config.ts` apunte a `http://localhost:8000`.

#### P: Necesito ejecutar un script de Python en mi entorno virtual local sin Docker.
**R:** En tu host, el puerto de PostgreSQL es `localhost:5432`. Define la variable `DATABASE_URL` al ejecutar tu script:
```bash
cd backend
# Ejecutar solo tareas cuyo propósito y origen de datos hayan sido auditados.
# No usar seed_database.py para preparar datos deportivos de entrenamiento.
```

---

¡Listo! Ya tienes todas las bases para empezar a construir y colaborar en la plataforma. Si tienes dudas sobre los modelos matemáticos de apuestas o valuación de tarjetas, consulta el [Manual Cuantitativo](QUANT_MANUAL.md) y la [Guía de Sports Cards](SPORTS_CARDS_GUIDE.md).

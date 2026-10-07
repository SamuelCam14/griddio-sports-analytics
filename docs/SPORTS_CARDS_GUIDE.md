# 🃏 Guía de Valuación de Sports Cards & Activos NIL: Explicada para Humanos

**Proyecto:** Sports Analytics Platform  
**Propósito:** Comprender cómo se valúan las tarjetas deportivas (NBA y contratos universitarios NCAA NIL), cómo predecir la depreciación del "Efecto Uniforme" y cómo detectar oportunidades de arbitraje en eBay con números reales paso a paso.  
**Versión:** 2.1.0  

---

## 1. 🌐 Las Tarjetas Deportivas como Activo Financiero

Las tarjetas coleccionables ya no son estampitas para niños en un álbum: hoy en día operan con la misma dinámica que las acciones en Wall Street o el oro.
* Hay **oferta limitada** (por ejemplo, solo existen 10 copias de una tarjeta *Gold /10* en todo el planeta).
* Hay **certificación de calidad** (empresas como PSA o Beckett examinan la tarjeta con lupa y microscopio, otorgándole una calificación del 1 al 10 que puede multiplicar su valor por 4).
* Hay **catalizadores de rendimiento**: si un novato anota 35 puntos en un partido de playoffs de la NBA, la demanda de sus tarjetas sube de golpe.

Nuestra plataforma elimina la especulación a ciegas y calcula el **Precio Justo Real (Fair Market Value o FMV)** de cada tarjeta mediante un modelo de precios hedónicos.

---

## 2. 🏛️ Modelo de Precios Hedónicos: ¿Cómo se calcula el valor de una tarjeta?

### La Idea Central
Una tarjeta no vale lo que a un vendedor se le ocurra pedir en eBay. Su valor se construye multiplicando 5 factores objetivos:

$$\text{Precio Justo (FMV)} = \text{Precio Base del Jugador} \times \text{Efecto Uniforme} \times \text{Condición (Grado)} \times \text{Rareza (Paralelo)} \times \text{Autógrafo}$$

---

### Los 5 Factores Explicados con Peras y Manzanas

1. **Precio Base del Jugador ($\text{Base}$):**  
   Cuánto vale una tarjeta estándar sin graduar de ese atleta según su talento, posición en el draft y tamaño de mercado (no es lo mismo jugar para los San Antonio Spurs que para los New York Knicks o Los Angeles Lakers).
2. **Efecto Uniforme ($\text{Uniforme}$):**  
   * **Tarjeta NBA Oficial (Prizm / Optic):** Multiplicador `1.00x` (Base 100%).
   * **Tarjeta Colegial NIL (Bowman University / Leaf):** Multiplicador `0.48x` (**-52% de descuento**). Si el jugador no viste el uniforme de su equipo NBA, el mercado le castiga más de la mitad de su valor.
3. **Condición Física / Grado ($\text{Grado}$):**  
   * **Raw (Sin Graduar):** `1.00x`
   * **PSA 9 (Mint / Casi Perfecta):** `1.65x` (+65% de prima)
   * **PSA 10 (Gem Mint / Perfección Total):** `3.85x` (Casi cuadruplica el valor debido a la enorme dificultad de obtener un 10 perfecto).
4. **Rareza del Paralelo ($\text{Paralelo}$):**  
   * **Base (Tirada regular):** `1.00x`
   * **Silver / Refractor (Brillante no numerada):** `2.20x`
   * **Purple /99 (Solo 99 en el mundo):** `5.50x`
   * **Gold /10 (Solo 10 en el mundo):** `18.00x`
   * **1/1 Black Prizm / Nebula (Única en el mundo):** `75.00x`
5. **Autógrafo ($\text{Firma}$):**  
   * **Sin Firma:** `1.00x`
   * **Sticker Auto (Firma en calcomanía pegada):** `2.30x`
   * **On-Card Auto (El jugador firmó directamente sobre la tarjeta con marcador):** `4.10x`

---

### 🏀 Ejemplo Real 1: Victor Wembanyama — 2023 Panini Prizm Silver PSA 10

Imaginemos que estamos evaluando la carta más codiciada del novato del año de los San Antonio Spurs:
* **Precio Base de Wembanyama:** $65.00 USD
* **Uniforme:** Oficial NBA (San Antonio Spurs) $\to$ Multiplicador `1.00`
* **Grado:** PSA 10 Gem Mint $\to$ Multiplicador `3.85`
* **Paralelo:** Silver Prizm $\to$ Multiplicador `2.20`
* **Autógrafo:** Ninguno $\to$ Multiplicador `1.00`

Hagamos la multiplicación paso a paso:
1. Base con Grado PSA 10:  
   $$\$65.00 \times 3.85 = \$250.25 \text{ USD}$$
2. Aplicando la rareza Silver Prizm:  
   $$\$250.25 \times 2.20 = \mathbf{\$550.55 \text{ USD}}$$

**Precio Justo de Mercado (FMV):** **$550.55 USD**.  
Si alguien en eBay la tiene en subasta o compra directa por **$350 USD**, el sistema la marca de inmediato como una compra obligatoria con ventaja.

---

## 3. 📉 El "Efecto Uniforme" y la Caída de Precios en Contratos NIL

### ¿Qué pasa cuando una estrella colegial llega a la NBA?
Antes de llegar al profesionalismo, los jugadores firman contratos universitarios (NIL) con marcas como *Bowman University*. Durante el torneo de la NCAA (March Madness) y el Draft de la NBA, estas tarjetas universitarias alcanzan precios altísimos por la emoción del momento.

Sin embargo, en cuanto salen a la venta las primeras cajas de tarjetas con el uniforme oficial de la NBA (*Panini Prizm* o *Select*), ocurre un fenómeno que hemos medido con precisión:

> **El capital de los coleccionistas rota de inmediato hacia las cartas con uniforme NBA, provocando un desplome promedio del -52% en las cartas NIL universitarias.**

---

### 📉 Ejemplo con Números Reales: Cooper Flagg (Prospecto #1 del Draft)

Comparemos dos cartas idénticas de Cooper Flagg en condición **PSA 10 con Autógrafo On-Card**:

| Atributo | Versión Colegial NIL (Bowman U) | Versión Oficial NBA (Panini Prizm) |
|---|---|---|
| **Precio Base** | $50.00 USD | $50.00 USD |
| **Efecto Uniforme** | **0.48x (-52% Descuento)** | **1.00x (Sin Castigo)** |
| **Grado PSA 10** | 3.85x | 3.85x |
| **Firma On-Card** | 4.10x | 4.10x |
| **Cálculo Final** | $\$50 \times 0.48 \times 3.85 \times 4.10$ | $\$50 \times 1.00 \times 3.85 \times 4.10$ |
| **Precio Justo (FMV)** | **$378.84 USD** | **$789.25 USD** |

**Lección Financiera para el Usuario:**  
Pagar $700 USD por una tarjeta NIL de un jugador colegial es un error financiero grave: en cuanto debute en la NBA y salgan sus tarjetas oficiales, esa tarjeta colegial perderá más de $300 USD de valor. Nuestro modelo te avisa exactamente cuándo vender tus tarjetas colegiales antes del desplome.

---

## 4. ⏳ Proyecciones a 90 Días con Facebook Prophet

### ¿Cómo anticipamos hacia dónde va el precio?
Usamos **Prophet**, una biblioteca de Inteligencia Artificial creada por Meta (Facebook) para proyectar series temporales. 

En lugar de trazar una línea recta simple, Prophet analiza:
1. **La tendencia histórica de ventas** de los últimos 60 días (obtenida de ventas reales confirmadas en eBay y 130Point).
2. **Bandas de Incertidumbre (95% de confianza):** Te muestra el escenario más pesimista ($yhat_{\text{lower}}$) y el escenario más optimista ($yhat_{\text{upper}}$).
3. **El suelo de mercado:** Identifica el momento exacto en que la caída de una carta NIL se estabiliza, señalando el punto ideal para que los coleccionistas de universidades compren a precio de liquidación.

---

## 5. 🚨 Arbitraje en eBay: Cómo Ganar Dinero con Tarjetas Subvaloradas

El módulo de arbitraje audita continuamente publicaciones activas en mercados secundarios para encontrar "gangas" matemáticas.

### La Trampa de las Comisiones (Fees)
Muchos compradores novatos ven una tarjeta con valor de **$100 USD** que alguien vende en **$80 USD**, y creen que van a ganar $20 USD. **Falso.**  
Vender una tarjeta en eBay conlleva gastos fijos y variables:
* **Comisión de eBay (Final Value Fee):** $13.25\%$
* **Costo de Transacción:** $\$0.30$ USD
* **Envío Asegurado con Rastreo:** Aprox. $\$4.50$ a $\$15.00$ USD

Para que una compra sea verdaderamente rentable, la ganancia neta debe superar al menos un **15% de retorno sobre la inversión (ROI)** después de pagar todas las comisiones.

---

### 💵 Caso Real Detectado: Stephon Castle — Panini Instant Rookie PSA 10

Revisemos una oportunidad real que el sistema detectó en el catálogo:

* **Vendedor en eBay la tiene en venta inmediata:** **$220.00 USD** (Precio de Compra).
* **Fair Market Value (FMV) calculado por nuestro modelo:** **$480.00 USD**.

Hagamos la matemática de liquidación completa:

1. **Venta al Precio Justo:** $\$480.00 \text{ USD}$
2. **Menos Comisión de eBay (13.25%):**  
   $$\$480.00 \times 0.1325 = -\$63.60 \text{ USD}$$
3. **Menos Envío Asegurado:** $-\$15.00 \text{ USD}$
4. **Menos Costo Fijo:** $-\$0.30 \text{ USD}$
5. **Dinero Limpio que Entra a tu Cuenta de Banco:**  
   $$\$480.00 - \$63.60 - \$15.00 - \$0.30 = \mathbf{\$401.10 \text{ USD}}$$

Ahora calculamos la rentabilidad real:

$$\text{Ganancia Neta} = \$401.10 \text{ (recibido)} - \$220.00 \text{ (pagado)} = \mathbf{+\$181.10 \text{ USD}}$$

$$\text{Retorno sobre la Inversión (ROI)} = \frac{\$181.10}{\$220.00} = \mathbf{+82.32\%}$$

**Acción del Algoritmo:**  
Como el ROI es **$+82.32\%$** (muy superior al umbral mínimo del $15\%$), el sistema enciende la alerta:  
> **🚨 OPORTUNIDAD EV+ (COMPRA SUBVALORADA)**  
> *"Compra en $220 USD, revende a valor justo de $480 USD y gana $181.10 USD limpios tras comisiones."*

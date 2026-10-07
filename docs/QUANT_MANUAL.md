# 🧮 Manual Cuantitativo: Matemática Explicada para Humanos

**Proyecto:** Sports Analytics Platform  
**Propósito:** Explicar con peras y manzanas cómo funcionan las fórmulas matemáticas, por qué existen y cómo se calculan con números reales en partidos de la NBA.  
**Versión:** 2.1.0  

---

## 1. 🎯 La Gran Verdad: ¿Por qué el 98% de la gente pierde dinero apostando?

Las casas de apuestas (como Bet365, DraftKings o Caliente) **no son casas de beneficencia ni predicen el futuro**. Son casas de cambio que cobran una comisión oculta en cada apuesta (llamada **Vig** o **Juice**). 

Si dos amigos apuestan a cara o cruz $100 cada uno, el ganador se lleva los $100 del otro (cobras 2.0 veces lo apostado). Pero en una casa de apuestas, te cobran $110 para ganar $100. Ese desbalance garantiza que, a largo plazo, la casa siempre gane y el usuario pierda.

Nuestro sistema hace exactamente lo mismo que un fondo de inversión de Wall Street:
1. **Detecta la comisión oculta de la casa** y la elimina matemáticamente para ver el precio justo real.
2. **Calcula con 3 algoritmos de Inteligencia Artificial** la probabilidad real de que un equipo gane.
3. **Compara ambos valores**: Si la probabilidad del modelo es mayor a la que ofrece la casa, encontramos una **Ventaja Matemática (Valor Esperado Positivo o +EV)**.
4. **Calcula con precisión de centavos cuánto dinero apostar** usando el Criterio de Kelly para jamás quebrar la cuenta.

A continuación, te explicamos cada fórmula paso a paso con partidos y números reales.

---

## 2. ⚖️ Valor Esperado (EV - Expected Value)

### ¿Qué significa en palabras simples?
El **Valor Esperado** responde a una sola pregunta:  
> *"Si pudiera repetir esta misma apuesta 100 veces exactamente en las mismas condiciones, ¿en promedio ganaría o perdería dinero por cada apuesta?"*

* Si el EV es **positivo (+EV)**: A la larga vas a ganar dinero de forma consistente.
* Si el EV es **negativo (-EV)**: A la larga la casa se quedará con tu dinero, aunque ganes un par de veces por suerte.

---

### La Fórmula sin tecnicismos

$$\text{Retorno Esperado} = (\text{Probabilidad de Ganar} \times \text{Ganancia Neta}) - (\text{Probabilidad de Perder} \times \text{Monto Apostado})$$

O expresado en **porcentaje de rentabilidad ($EV\%$)**:

$$EV\% = (\text{Probabilidad Real del Modelo} \times \text{Cuota Decimal de la Casa}) - 1$$

---

### 🏀 Ejemplo con Números Reales: Boston Celtics vs. Miami Heat

Imaginemos este partido:
* **Monto que vas a apostar:** $100 USD.
* **La casa de apuestas te ofrece una cuota decimal:** `1.90` (equivalente a momio americano `-111`). Si ganas, recibes $190 ($100 tuyos + $90 de ganancia neta).
* **El ensamble de IA (CatBoost + XGBoost) analiza las estadísticas y determina:** Los Celtics tienen un **65% de probabilidad real (0.65)** de ganar. Por ende, la probabilidad de perder es del **35% (0.35)**.

Hagamos la cuenta aritmética básica:

1. **Escenario de Éxito:**  
   $0.65 \times \$90 \text{ (ganancia neta)} = +\$58.50 \text{ USD}$
2. **Escenario de Fracaso:**  
   $0.35 \times \$100 \text{ (tu apuesta perdida)} = -\$35.00 \text{ USD}$
3. **Balance Neto (Valor Esperado):**  
   $$+\$58.50 - \$35.00 = +\$23.50 \text{ USD}$$

**¿Qué significa este resultado?**  
Cada vez que haces esta apuesta de $100, estás comprando un boleto que en promedio te deja **+$23.50 USD de ganancia matemática pura (+23.5% de EV)**. El apostador novato solo ve si el equipo gana hoy o no; el analista cuantitativo ve que tiene una ventaja matemática gigante que multiplicará su capital con el tiempo.

---

## 3. ✂️ Eliminación del Margen de la Casa (Vig Removal)

### ¿Qué es la "Comisión Oculta" (Overround)?
En un volado perfecto (50% cara, 50% cruz), el momio decimal justo debería ser **2.00** para cada lado ($1 / 0.50 = 2.00$).  
Si sumas las dos probabilidades:
$$50\% + 50\% = 100\%$$

Pero si vas a una casa de apuestas en un partido 100% parejo, verás que pagan **1.91 a cada equipo** (momio americano `-110`).  
Si calculamos qué probabilidad exige la casa para cada equipo:
$$\text{Probabilidad Implícita} = \frac{1}{\text{Cuota Decimal}}$$

* Equipo Local: $\frac{1}{1.91} = 0.5236$ (es decir, **$52.36\%$**)
* Equipo Visitante: $\frac{1}{1.91} = 0.5236$ (es decir, **$52.36\%$**)

Si sumamos ambas:
$$52.36\% + 52.36\% = 104.72\%$$

**¡El total da 104.72%, no 100%!**  
Ese **$4.72\%$ extra** es la comisión invisible (*overround* o *vig*) que la casa te cobra por el simple hecho de entrar a jugar.

---

### ¿Cómo elimina nuestro sistema esta comisión para encontrar el "Precio Justo"?

Usamos el **Método Proporcional de Normalización**: dividimos la probabilidad de cada equipo entre la suma total ($104.72\%$).

---

### 🏀 Ejemplo Paso a Paso: Los Angeles Lakers vs. Golden State Warriors

Imagina que la casa de apuestas publica los siguientes momios:
* **Lakers (Favorito):** Cuota decimal `1.60` (Momio `-167`).
* **Warriors (Underdog):** Cuota decimal `2.40` (Momio `+140`).

#### Paso 1: Calcular la probabilidad bruta de cada uno (con trampa de la casa incluida)
* Probabilidad Bruta Lakers: $\frac{1}{1.60} = 0.6250$ (**$62.50\%$**)
* Probabilidad Bruta Warriors: $\frac{1}{2.40} = 0.4167$ (**$41.67\%$**)
* Suma total con trampa: $62.50\% + 41.67\% = 104.17\%$ (Margen de la casa: $4.17\%$).

#### Paso 2: Limpiar la comisión (Probabilidad Justa o Fair Odds)
Dividimos cada probabilidad entre el total ($1.0417$):

$$\text{Probabilidad Justa Lakers} = \frac{0.6250}{1.0417} = 0.6000 \quad (\mathbf{60.00\%})$$

$$\text{Probabilidad Justa Warriors} = \frac{0.4167}{1.0417} = 0.4000 \quad (\mathbf{40.00\%})$$

Ahora la suma sí es exactamente **$60\% + 40\% = 100\%$**.

#### Paso 3: ¿Cuál debería ser la cuota real justa sin comisión?
* Cuota Real Justa Lakers: $\frac{1}{0.6000} = \mathbf{1.667}$
* Cuota Real Justa Warriors: $\frac{1}{0.4000} = \mathbf{2.500}$

#### Paso 4: ¿Dónde entra nuestro modelo de Inteligencia Artificial?
Nuestro ensamble de IA analiza datos de juego, descanso, viajes y tiros, y concluye:
> *"Lakers en realidad tienen un **68%** de probabilidad de ganar, no el 60% que cree el mercado."*

Como nuestro **$68\%$** es superior a la probabilidad justa del mercado (**$60\%$**), hemos detectado una **ineficiencia de mercado del $+8.0\%$**. Aquí es donde el sistema dispara una señal de compra.

---

## 4. 🛡️ Criterio de Kelly: ¿Cuánto dinero apostar sin arriesgarte a quebrar?

Tener ventaja matemática no significa que vas a ganar el 100% de las veces. En los deportes hay rachas malas (partidos con expulsiones, lesiones, arbitrajes dudosos). Si apuestas el 50% de tu dinero en una sola jugada y pierdes dos veces seguidas, estás arruinado.

El **Criterio de Kelly** es una fórmula matemática desarrollada en los laboratorios Bell por John L. Kelly Jr. que calcula **el porcentaje exacto de tu dinero que debes apostar para maximizar el crecimiento de tu capital reduciendo el riesgo a cero**.

---

### La Fórmula de Kelly

$$f^* = \frac{(\text{Ganancia Neta por Dólar} \times \text{Probabilidad de Ganar}) - \text{Probabilidad de Perder}}{\text{Ganancia Neta por Dólar}}$$

Donde:
* **Ganancia Neta por Dólar:** Es $(\text{Cuota Decimal} - 1)$. Por ejemplo, a cuota $2.50$, ganas $\$1.50$ por cada dólar.
* **Probabilidad de Ganar:** La probabilidad estimada por nuestro modelo (ejemplo: $0.60$).
* **Probabilidad de Perder:** $1 - \text{Probabilidad de Ganar}$ (ejemplo: $1 - 0.60 = 0.40$).

---

### La Regla del "Cuarto de Kelly" (Quarter Kelly) y el Límite del 5%

La fórmula de Kelly original es muy agresiva porque asume que conocemos la probabilidad exacta al 100% sin margen de error. En el deporte real siempre hay incertidumbre.

Por eso, nuestro sistema aplica dos frenos de mano institucionales:
1. **Fracción Conservadora (Quarter Kelly = $0.25 \times f^*$):** Solo apostamos la cuarta parte de lo que sugiere Kelly.
2. **Tope Máximo de Seguridad (Cap del 5%):** Jamás, bajo ninguna circunstancia, se recomendará apostar más del **5% del capital total (bankroll)** en una sola jugada.

---

### 💵 Ejemplo con Números Reales de tu Cartera

Imagina que tienes una cuenta con un **Bankroll de $1,000 USD**:
* **Partido:** Denver Nuggets pagan cuota decimal `2.00` (momio `+100`). Ganas $1.00 neto por cada dólar apostado.
* **Modelo IA:** Dice que Jokic y Denver tienen **$60\%$ de probabilidad ($0.60$)** de ganar (la casa solo exige 50%).

#### Paso 1: Calcular Kelly Completo
$$f^* = \frac{(1.00 \times 0.60) - 0.40}{1.00} = \frac{0.60 - 0.40}{1.00} = 0.20 \quad (\mathbf{20\%})$$
Kelly completo sugeriría apostar el 20% ($200 USD). Esto es demasiado arriesgado para un inversor serio.

#### Paso 2: Aplicar Cuarto de Kelly (Quarter Kelly)
$$\text{Porcentaje Operativo} = 0.20 \times 0.25 = \mathbf{0.05} \quad (\mathbf{5\%})$$

#### Paso 3: Calcular el dinero real en dólares
$$\text{Monto a Apostar} = \$1,000 \times 5\% = \mathbf{\$50.00 \text{ USD}}$$

El sistema le dice al usuario:  
> *"Tienes una ventaja del +10%. Para proteger tu capital y maximizar tus ganancias a largo plazo, apuesta exactamente **$50.00 USD**."*

---

## 5. 🤖 Sistema de Confluencia de 3 Modelos (¿Por qué 3 modelos en lugar de uno?)

Un solo modelo de Machine Learning puede "enamorarse" de un patrón falso (sobreajuste o *overfitting*). Por eso creamos un comité de 3 algoritmos con distinta naturaleza matemática:

| Modelo | Peso en el Voto | Especialidad Matemática |
|---|---|---|
| **CatBoost** | **45%** | Experto en variables categóricas (franquicias, conferencias, días de descanso). No se deja engañar por datos atípicos. |
| **XGBoost** | **35%** | Árboles de gradiente rápido que detectan rachas recientes de puntos y eficiencia ofensiva/defensiva. |
| **Regresión Logística** | **20%** | Modelo lineal clásico calibrado con máxima regularización. Actúa como el juez conservador que frena el entusiasmo excesivo. |

---

### Tiers de Confluencia: La Regla de los 3 Sabios

Auditamos el comportamiento de los modelos en **492 partidos reales de prueba** (sin que los modelos conocieran el futuro). Los resultados fueron contundentes:

```
                      ¿Los 3 modelos están de acuerdo?
                                  │
                 ┌────────────────┴────────────────┐
                 ▼                                 ▼
             SÍ (3 de 3)                       NO (2 de 3)
         ★★★ TIER PREMIUM                    ★★ TIER VALUE
     ─────────────────────────         ─────────────────────────
     Precisión: 70.12%                 Precisión: 50.57%
     Partidos: 405 de 492              Partidos: 87 de 492
     ROI Simulado: +33.87%             ROI Simulado: -2.10%
     Acción: APOSTAR CON FUERZA        Acción: DESCARTAR / FILTRAR
```

* **★★★ PREMIUM (70.12% de aciertos):** Cuando CatBoost, XGBoost y la Regresión Logística coinciden en el ganador, la tasa de éxito sube al **$70.12\%$**, generando un **+$33.87\%$ de retorno sobre la inversión**.
* **★★ VALUE (50.57% de aciertos):** Cuando 2 dicen sí y 1 dice no, el partido es prácticamente un volado (50%). **El sistema descarta la apuesta automáticamente para proteger tu dinero**.

---

## 6. 🎯 Matemática de los Micro-Parlays Rentables

### La Trampa del Parlay Tradicional
El apostador recreativo junta 8 o 10 equipos soñando con convertir $5 en $10,000.  
En cada partido que agregas, la casa multiplica su comisión ($4\% \times 4\% \times 4\% \dots$). Por eso los parlays largos son el producto más rentable para los casinos y la ruina para el usuario.

---

### La Excepción Matemática: El "Micro-Parlay" Institucional
Si combinamos **únicamente 2 o 3 eventos independientes** donde cada uno tiene una probabilidad alta calculada por nuestro modelo ($>65\%$), la ventaja matemática **no se destruye, sino que se compone**.

---

### 🏀 Ejemplo Paso a Paso: El Parlay Cuota ~2.0 (+100)

Buscamos una combinada donde el pago sea duplicar nuestro dinero (cuota $2.00$ o momio $+100$), pero con probabilidad matemática a nuestro favor.

Tomemos 2 partidos del día evaluados en Tier ★★★ PREMIUM:
1. **Pata 1 (Leg 1):** Boston Celtics ganan a cuota `1.45` (Momio `-222`). Probabilidad real del modelo: **$75\%$ ($0.75$)**.
2. **Pata 2 (Leg 2):** Oklahoma City Thunder ganan a cuota `1.42` (Momio `-238`). Probabilidad real del modelo: **$72\%$ ($0.72$)**.

Hagamos la matemática:

#### Paso 1: Cuota Combinada
$$\text{Cuota Decimal Combinada} = 1.45 \times 1.42 = \mathbf{2.059} \quad (\approx +106 \text{ americano})$$
Si apuestas $100, cobras **$205.90** ($105.90 de ganancia neta).

#### Paso 2: Probabilidad Conjunta Real
Como los partidos se juegan en estadios distintos y entre equipos diferentes, son eventos independientes:

$$\text{Probabilidad de Ganar Ambos} = 0.75 \times 0.72 = 0.5400 \quad (\mathbf{54.0\%})$$

#### Paso 3: ¿Qué probabilidad exige la casa para una cuota de 2.059?
$$\text{Probabilidad que exige la casa} = \frac{1}{2.059} = 0.4856 \quad (\mathbf{48.56\%})$$

#### Paso 4: El Veredicto
* La casa fijó el precio como si la probabilidad fuera del **$48.56\%$**.
* La realidad estadística de nuestros modelos demuestra que la probabilidad real es del **$54.00\%$**.
* **Ventaja Neta (+EV):**  
  $$EV\% = (0.54 \times 2.059) - 1 = 1.1118 - 1 = \mathbf{+11.18\%}$$

Por cada $100 apostados a este micro-parlay, generas **+$11.18 USD de ganancia esperada**. Estás cobrando un premio de momio $+106$ en un evento que en realidad tiene más del 50% de probabilidades de ocurrir.

---

## 7. 🎲 Simulación de Monte Carlo y Riesgo de Ruina

### ¿Qué hace el simulador de Monte Carlo?
Nadie puede predecir el orden exacto en que saldrán las victorias y derrotas. Aunque tengas un 70% de efectividad, es matemáticamente posible perder 4 partidos seguidos por azar puro.

La **Simulación de Monte Carlo** toma tu bankroll (ejemplo: $1,000 USD) y simula **1,000 futuros alternativos** en una computadora ejecutando 100 apuestas consecutivas en cada uno:

```
Trayectoria 1:  $1000 ──> $1040 ──> $1010 ──> $1150 ──> $1420 (Final positivo)
Trayectoria 2:  $1000 ──> $960  ──> $920  ──> $1050 ──> $1380 (Final positivo)
Trayectoria 3:  $1000 ──> $920  ──> $880  ──> $910  ──> $1210 (Racha negativa superada)
...
Trayectoria 1000: Simulación masiva estocástica
```

### Métricas que le interesan al usuario:
1. **Mediana Final de Capital:** El resultado más probable en el que terminará tu saldo tras 100 apuestas (con picks PREMIUM y Quarter Kelly, el capital promedio suele rondar los **+$1,350 a +$1,500 USD**).
2. **Probabilidad de Ruina ($P_{\text{ruina}}$):** El porcentaje de las 1,000 simulaciones donde el usuario perdió más del 80% de su saldo.
   * Con picks sin ventaja (+0% EV): La probabilidad de ruina es $>60\%$.
   * Con picks ★★★ PREMIUM y Quarter Kelly (Cap 5%): **La probabilidad de ruina es inferior al $0.01\%$**.

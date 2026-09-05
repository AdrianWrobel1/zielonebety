# PLAN ONLY — NO IMPLEMENTATION PERFORMED

# Kompleksowy Kierunek Wizualny i UX Frontendu Platformy Zielone Bety
**Profesjonalny Standard Analityki Sportowej i Decision-Support (Vanilla JS/CSS SPA)**

Data opracowania: Wrzesień 2026  
Środowisko docelowe: Zielone Bety v2.0 (FastAPI + HTML5 / Modern CSS / Vanilla JS SPA)  
Status: Gotowy do zatwierdzenia przez Inżyniera / Użytkownika  

---

## 1. Design Direction Summary

Niniejszy dokument definiuje **spójny, docelowy kierunek wzorniczy (Design Direction) oraz architekturę UX** dla całej warstwy prezentacyjnej platformy **Zielone Bety**. 

Kierunek powstał w oparciu o weryfikację audytu `design-plans/frontend-ui-audit.md`, inspekcję działającego środowiska w runtime przez `agent-browser`, analizę modeli wyceny matematycznej oraz zasady unikania generycznego AI-slopu (`anti-ui-slop`, `ui-ux-pro-max`).

### Kluczowa teza projektowa:
Platforma Zielone Bety nie jest kalkulatorem bukmacherskim ani generycznym panelem administratora. Jest **wysokowydajnym terminalem analityczno-decyzyjnym (Quantitative Betting Intelligence & Decision Workspace)**. Każdy piksel, kolor, margines i animacja mają służyć jednemu celowi: **umożliwieniu użytkownikowi natychmiastowej oceny dodatniej wartości oczekiwanej (Net EV), zweryfikowania konsensusu rynkowego i podjęcia bezpiecznej decyzji inwestycyjnej w ułamku sekundy**.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    ZIELONE BETY — DESIGN SYSTEM CONTRACT                     │
├──────────────────────────────────────────────────────────────────────────────┤
│  1. STACK DISCIPLINE:      HTML5 + Modern CSS + Vanilla JS (Zero Rewrites)   │
│  2. SEMANTIC COLOR:        Green = Verified Net EV Value (NOT decorative)    │
│  3. DATA DENSITY:          Balanced Analytics (Compact rhythm, 0 whitespace) │
│  4. DECISION HIERARCHY:    Value & Net EV first, context second, diag on demand │
│  5. SURFACE ARCHITECTURE:  Solid Slate + 1px micro-borders + Slide-over Drawer│
│  6. MOTION PHILOSOPHY:     Functional & crisp (<=220ms), zero visual fluff   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Verified Audit Findings

Na podstawie analizy kodu źródłowego (`web/index.html`, `web/styles.css`, `web/app.js`) oraz inspekcji interfejsu w przeglądarce, zweryfikowano i sklasyfikowano wnioski z audytu:

### 2.1. Verified Findings (Rzeczywiste, potwierdzone problemy w kodzie i UI)

| ID | Zidentyfikowany problem | Potwierdzenie w kodzie / runtime | Realny wpływ na UX | Severity |
|---|---|---|---|---|
| **VF-01** | **Odwrócona hierarchia wiersza na `/playerprops`** | W `web/app.js` (linie 3580–3612) kolumny ułożone są w kolejności: `Type` (1) $\rightarrow$ `Player` (2) $\rightarrow$ `Match` (3) $\rightarrow$ `Market` (4) $\rightarrow$ `Polish Odds` (5) $\rightarrow$ `Reference` (6) $\rightarrow$ **`Net EV` (7)** $\rightarrow$ `Trend` (8) $\rightarrow$ **`Action/Status` (9)**. | Użytkownik musi przeskanować wzrokiem 7 kolumn, aby dowiedzieć się, czy zakład w ogóle generuje zysk. | `P1` (Krytyczny) |
| **VF-02** | **Deformacja tabeli przez panel boczny** | W `styles.css` (linia 1354) `.props-layout-grid` przełącza tabelę na `flex: 2` i `#prop-detail-container` na `flex: 1.2`. | Kliknięcie `Inspect →` drastycznie ściska 9-kolumnową tabelę, powodując ucinanie tekstu, zawijanie w 4 linijki i utratę czytelności. | `P1` (Krytyczny) |
| **VF-03** | **Przeładowany formularz filtrów na `/playerprops`** | W `web/index.html` (linie 1016–1149) `.props-controls-grid` renderuje 12 kontrolek w jednym bloku. | Formularz zajmuje > 260px wysokości, wypychając właściwe okazje poniżej linii zgięcia (fold). | `P2` (Wysoki) |
| **VF-04** | **Niski kontrast etykiet w trybie jasnym (Light Theme)** | W `styles.css` (linie 1478, 1581, 1605, 1636) klasy `.quality-flag-tag` (`#F87171`), `.rejection-chip` (`#FCA5A5`), `.prop-type-badge.player` (`#A5B4FC`) mają stałe jasne kolory tekstu. | Na tle `#FFFFFF` / `#F3F4F6` kontrast wynosi 2.1:1 – 2.8:1, naruszając wymóg WCAG AA (4.5:1). | `P2` (Wysoki) |
| **VF-05** | **Brak lepkich nagłówków (`sticky table headers`)** | `.data-table th` w `styles.css` nie posiada `position: sticky`. | Przy liście 50 pozycji użytkownik podczas przewijania traci informację o nazwach kolumn. | `P2` (Wysoki) |
| **VF-06** | **Mieszanie wymiarów w zakładkach `/playerprops`** | 10 przycisków zakładek (`Top Valuebets`, `Player`, `Team`, `Superbet`, `Betclic`, `Below Threshold`, `Ref Gap` itd.) miesza podmioty, bukmacherów i kody błędów. | Dezorientacja: użytkownik nie wie, które filtry się wykluczają, a które nakładają. | `P2` (Wysoki) |
| **VF-07** | **Mieszanie języka polskiego i angielskiego** | W `web/app.js` linia 3417: `"No player props match... Brak zakwalifikowanych propsów..."`. | Wrażenie niedopracowania i braku spójności produktu. | `P3` (Średni) |
| **VF-08** | **Brak wskaźników Skeleton Loading** | Podczas zapytań `fetch()` tabela wyświetla statyczny tekst `Loading...` lub czyści zawartość. | Migotanie ekranu i skoki układu (layout shift). | `P3` (Średni) |

### 2.2. Design Hypotheses (Wymagające decyzji projektowej)

- **DH-01**: Zastąpienie in-flow side panelu przez **Slide-over Sheet (Drawer)** wysuwany z prawej krawędzi ekranu o stałej szerokości 520px (z zachowaniem 100% szerokości i geometrii tabeli w tle).
- **DH-02**: Zmiana palety akcentowej: rezygnacja z zieleni jako koloru dekoracyjnego (obramowania, tła kart, ikony neutralne) na rzecz precyzyjnego błękitu/indygo dla interfejsu i zarezerwowanie **zieleni szmaragdowej wyłącznie dla wskaźników Net EV > 0% i etykiet VALUE BET**.
- **DH-03**: Wprowadzenie 2-poziomowego paska filtrów: linia podstawowa (Search, Stat, Min EV, Time Horizon) + wysuwany popover/pasek zaawansowany.
- **DH-04**: Redukcja 9 kolumn tabeli do **5 zintegrowanych bloków analitycznych** o stałym rytmie wizualnym.

### 2.3. Implementation Details (Do rozstrzygnięcia w fazie kodu)

- Dokładne wartości pikselowe krzywych przejść (np. `cubic-bezier(0.16, 1, 0.3, 1)` dla draweru).
- Mapowanie unikalnych `id` z atrybutami `<label for="...">`.
- Optymalizacja `requestAnimationFrame` dla ewentualnych wykresów mini-sparkline.

---

## 3. Visual Personality

Zielone Bety musi komunikować **matematyczną precyzję, pewność i szybkość działania**.

### Czym produkt JEST:
- **Professional Betting Analytics Terminal**: Narzędzie zbliżone w estetyce do nowoczesnych terminali finansowych i platform quant-tradingowych (np. Koyfin, Bloomberg, Linear, Raycast).
- **Decision-Support Engine**: Interfejs zoptymalizowany pod kątem natychmiastowej odpowiedzi na pytania: *Gdzie jest zysk? Jaka jest pewność konsensusu? U którego bukmachera złożyć zakład przed zmianą linii?*
- **Data-Dense & Clean**: Zwarte, czytelne komponenty, doskonała czytelność liczb w fontach stałopozycyjnych (tabular figures).

### Czym produkt NIE JEST (Anti-UI-Slop Contract):
- ❌ **Nie jest generycznym dashboardem AI**: Brak przypadkowych różowo-fioletowych gradientów, sztucznych świecących kropel i wielkich okrągłych kart.
- ❌ **Nie jest neonowym crypto-kasynem**: Brak świecących na zielono/czerwono krawędzi wokół wszystkich elementów.
- ❌ **Nie jest Bootstrap adminem**: Brak topornych szarych ramek, domyślnych inputów przeglądarki i wielkich pustych przestrzeni.
- ❌ **Nie jest chaotycznym zbiorem emoji**: Zastąpienie emoji systemowymi, wektorowymi ikonami SVG o spójnej grubości linii (1.5px).

---

## 4. Color Direction

Zaprojektowano dwumodułowy system kolorystyczny z **absolutną dyscypliną semantyczną**.

### 4.1. Zasada nadrzędna semantyki kolorów
> **Kolor szmaragdowy (Green) jest kolorem ZYSKU i WARTOŚCI DODATNIEJ, a nie elementem dekoracyjnym layoutu.**  
> Żadne obramowanie, neutralny przycisk nawigacji ani element statyczny nie mogą używać jaskrawej zieleni, aby nie wywoływać fałszywych sygnałów u użytkownika.

### 4.2. Paleta Tokenów — Dark Mode (Domyślny motyw terminala)

```css
:root {
  /* Podłoża i Powierzchnie (Slate / Obsidian) */
  --surface-canvas: #090D14;         /* Główne tło aplikacji */
  --surface-card: #111622;           /* Karty, kontenery tabel */
  --surface-elevated: #161D2C;       /* Panele wysuwane, drawery, modale */
  --surface-hover: #1C2538;          /* Stan najechania wiersza/przycisku */
  --surface-active: #232E46;         /* Stan aktywny / wciśnięty */
  --surface-input: #0C101A;          /* Tło pól formularzy */
  --surface-table-header: #0E131F;   /* Lepki nagłówek tabeli */

  /* Obramowania i Separatory */
  --border-subtle: rgba(255, 255, 255, 0.07);  /* Domyślne podziały wierszy */
  --border-strong: rgba(255, 255, 255, 0.14);  /* Krawędzie kart i kontrolek */
  --border-focus: #3B82F6;                     /* Stan focusu klawiatury */

  /* Typografia i Hierarchia Tekstu */
  --text-primary: #F8FAFC;          /* Nagłówki, kluczowe dane, kursy (Contrast 14:1) */
  --text-secondary: #94A3B8;        /* Etykiety, metadane, drugorzędne (Contrast 6.5:1) */
  --text-muted: #64748B;            /* Podpisy pomocnicze, jednostki (Contrast 4.5:1) */
  --text-inverse: #090D14;          /* Tekst na jasnych tłach akcentowych */

  /* Akcent Strukturalny & Brand (Niebieski / Indigo precyzji) */
  --brand-primary: #3B82F6;         /* Przyciski główne, aktywne zakładki nawigacji */
  --brand-hover: #2563EB;
  --brand-subtle: rgba(59, 130, 246, 0.12);

  /* Kolory Semantyczne — WYŁĄCZNIE DLA WARTOŚCI I STATUSÓW */
  --val-positive: #10B981;          /* Net EV > 0%, Status VALUE BET */
  --val-positive-bg: rgba(16, 185, 129, 0.14);
  --val-positive-border: rgba(16, 185, 129, 0.35);
  --val-positive-high: #34D399;     /* Net EV >= 8% (High Value) */
  --val-positive-high-bg: rgba(16, 185, 129, 0.22);

  --val-warning: #F59E0B;           /* Below Threshold, Margines bliski 0 */
  --val-warning-bg: rgba(245, 158, 11, 0.12);
  --val-warning-border: rgba(245, 158, 11, 0.30);

  --val-negative: #EF4444;          /* Rejection, Ujemny EV, Błąd */
  --val-negative-bg: rgba(239, 68, 68, 0.12);
  --val-negative-border: rgba(239, 68, 68, 0.30);

  --val-reference: #0EA5E9;         /* Zagraniczny konsensus / Fair Odds */
  --val-reference-bg: rgba(14, 165, 233, 0.12);
  --val-reference-border: rgba(14, 165, 233, 0.30);
}
```

### 4.3. Paleta Tokenów — Light Mode (`[data-theme="light"]`)

```css
[data-theme="light"] {
  --surface-canvas: #F8FAFC;
  --surface-card: #FFFFFF;
  --surface-elevated: #FFFFFF;
  --surface-hover: #F1F5F9;
  --surface-active: #E2E8F0;
  --surface-input: #FFFFFF;
  --surface-table-header: #F1F5F9;

  --border-subtle: #E2E8F0;
  --border-strong: #CBD5E1;
  --border-focus: #2563EB;

  --text-primary: #0F172A;
  --text-secondary: #475569;
  --text-muted: #64748B;
  --text-inverse: #FFFFFF;

  --brand-primary: #2563EB;
  --brand-hover: #1D4ED8;
  --brand-subtle: #EFF6FF;

  /* Semantyczne z zachowaniem WCAG AA >= 4.5:1 na białym tle */
  --val-positive: #047857;          /* Ciemniejsza zieleń szmaragdowa dla kontrastu */
  --val-positive-bg: #ECFDF5;
  --val-positive-border: #A7F3D0;
  --val-positive-high: #065F46;
  --val-positive-high-bg: #D1FAE5;

  --val-warning: #B45309;           /* Ciemny bursztyn */
  --val-warning-bg: #FFFBEB;
  --val-warning-border: #FDE68A;

  --val-negative: #B91C1C;          /* Ciemna czerwień */
  --val-negative-bg: #FEF2F2;
  --val-negative-border: #FECACA;

  --val-reference: #0369A1;         /* Ciemny błękit */
  --val-reference-bg: #F0F9FF;
  --val-reference-border: #BAE6FD;
}
```

---

## 5. Typography Direction

### 5.1. Dobór Rodzin Krojów
1. **Krój Podstawowy (UI / Text)**: `'Outfit', -apple-system, BlinkMacSystemFont, 'Inter', sans-serif`  
   - Nowoczesny, geometryczny bezszeryf z otwartymi aperturami, gwarantujący natychmiastową czytelność nazwisk zawodników i rynków.
2. **Krój Danych / Kursów / Telemetrii**: `'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace`  
   - Niezbędny dla kursów bukmacherskich, stawek, procentów Net EV i znaczników czasowych.
   - **Obowiązkowa reguła CSS**:
     ```css
     .mono, .tabular-nums, .data-table td, .metric-num {
       font-feature-settings: "tnum" 1, "zero" 1;
       font-variant-numeric: tabular-nums;
     }
     ```
   - Eliminuje „drżenie” kolumn i przesunięcia wierszy przy automatycznym odświeżaniu danych z API (co 10 s).

### 5.2. Skala Typograficzna

| Poziom / Przeznaczenie | Rozmiar (rem/px) | Grubość (Weight) | Letter Spacing | Kolor |
|---|---|---|---|---|
| **Page Title (H1)** | `1.45rem` (23.2px) | `700` (Bold) | `-0.02em` | `var(--text-primary)` |
| **Section / Card Title (H2/H3)** | `1.05rem` (16.8px) | `600` (SemiBold) | `-0.01em` | `var(--text-primary)` |
| **Metric Hero Number** | `1.65rem` (26.4px) | `700` (Bold, Mono) | `-0.02em` | `var(--text-primary)` |
| **Table Column Header (`th`)** | `0.72rem` (11.5px) | `600` (SemiBold) | `+0.05em` | `var(--text-muted)` (Uppercase) |
| **Table Main Data / Player Name** | `0.92rem` (14.7px) | `600` (SemiBold) | `0` | `var(--text-primary)` |
| **Table Secondary Info / Subtext** | `0.75rem` (12.0px) | `400` (Regular) | `0` | `var(--text-secondary)` |
| **Micro Metadata / Footers** | `0.70rem` (11.2px) | `400` (Regular) | `+0.02em` | `var(--text-muted)` |
| **Odds / Net EV Hero Pill** | `0.95rem` (15.2px) | `700` (Bold, Mono) | `0` | `var(--val-positive)` |

---

## 6. Density & Spacing Direction

### 6.1. Wybór Poziomu Gęstości: **Balanced Analytics Density**

Dlaczego nie „Spacious SaaS”?  
- Typowy SaaS (np. Stripe Marketing, Notion) używa paddingów wierszy 16–24px. W produkcie analityki sportowej sprawiłoby to, że na ekranie mieściłoby się zaledwie 4–5 okazji, zmuszając do nieustannego scrollowania.

Dlaczego nie „Ultra-Dense Raw Terminal”?  
- Czysty terminal (jak surowy Bloomberg 4px) bez oddechu sprawia, że złożone dane wielowymiarowe (kursy, konsensus 4 bukmacherów, próba L10, status polskiego podatku) zlewają się w jedną plamę tekstu.

**Rozwiązanie: Balanced Analytics**:
- Wysokość wiersza tabeli: `48px – 56px` (zawiera 2 linie zorganizowanego kontekstu: główna dana + miniaturowy opis).
- Padding wewnętrzny komórek tabeli: `0.65rem 0.85rem` (pion / poziom).
- Siatka odstępów (Spacing Scale):
  ```css
  --space-1: 0.25rem; /* 4px */
  --space-2: 0.50rem; /* 8px */
  --space-3: 0.75rem; /* 12px */
  --space-4: 1.00rem; /* 16px */
  --space-5: 1.25rem; /* 20px */
  --space-6: 1.50rem; /* 24px */
  --space-8: 2.00rem; /* 32px */
  ```

---

## 7. Surface / Component Direction

### 7.1. Karty i Kontenery Powierzchni
- Rezygnacja z nadmiernego, zasobożernego `backdrop-filter: blur(12px)` na wszystkich kartach wewnętrznych.
- Solidne, wydajne tło `--surface-card` z precyzyjnym obramowaniem 1px `--border-subtle` oraz subtelnym cieniem głębi:
  ```css
  .card {
    background: var(--surface-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md); /* 8px */
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2), 0 4px 12px rgba(0, 0, 0, 0.15);
  }
  ```

### 7.2. System Przycisków (Buttons)
1. **Primary Button (`.btn-primary`)**:
   - Tło: `var(--brand-primary)` (Niebieski), tekst: `#FFFFFF`.
   - Hover: `var(--brand-hover)` z lekkim cieniem akcentowym.
2. **Secondary / Outline Button (`.btn-outline`)**:
   - Tło: `transparent`, obramowanie: `var(--border-strong)`, tekst: `var(--text-primary)`.
   - Hover: `var(--surface-hover)`.
3. **Ghost / Action Icon Button (`.btn-icon`)**:
   - Dyskretny przycisk z obwódką pojawiającą się dopiero przy najechaniu.
4. **Inspect Button (`.btn-inspect`)**:
   - Dedykowany przycisk w tabeli: kompaktowy, wyrazisty, otwierający drawer szczegółów (`Inspect ➔`).

### 7.3. Formularze i Kontrolki (Inputs & Selects)
- Kompaktowe pola o stałej wysokości `34px` dla kontrolek filtrowania (`.form-control-sm`).
- Tło `--surface-input`, wyraźny stan `:focus` z obramowaniem `--border-focus` i delikatnym ringiem `box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2)`.

---

## 8. `/playerprops` UX Direction (Główny Case Study)

Widok `/playerprops` stanowi rdzeń decyzyjny całego systemu. Wdrożona zostanie gruntowna reorganizacja architektury informacji bez modyfikowania backendu.

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                             /playerprops DOCELOWA ARCHITEKTURA WIDOKU                            │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 1. HEADER: Tytuł + Zakres (PLAYER/TEAM/ALL) + Metryki Skanu + Przycisk [Scan Props]            │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 2. SUMMARY KPI BAR: 4 zwięzłe kafelki metryk (Discovered | Polish Quotes | Bettable | Ref Valid) │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 3. FILTER TOOLBAR (2-poziomowy):                                                                 │
│    [Wyszukiwarka: "Saka, Over 1.5"] [Statystyka ▾] [Min Net EV: 3.0%] [Horyzont: 7D ▾] [Więcej ▾]│
│    └─► Rozwijany panel: Ligi, Pozycje, Linie, Bukmacherzy, Statusy, Sortowanie, Reset Filtrów   │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 4. TAB NAVIGATION: [🏆 Qualified Value Bets (12)]   [🔬 Pipeline Candidates & Diagnostics (84)]  │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 5. GŁÓWNA TABELA ANTY-SLOP (5 Zintegrowanych Kolumn):                                            │
│    [VALUE & NET EV] │ [KANDYDAT & RYNEK] │ [KURSY PL (EXEC)] │ [FAIR & REF] │ [TREND & AKCJA]   │
│    -----------------┼--------------------+-------------------+--------------+----------------   │
│    [+14.2% Net EV]  │ B. Saka            │ Superbet: 2.45    │ Fair: 1.88   │ 8/10 (80%)        │
│    VALUE BET        │ Over 1.5 SOT       │ (eff: 2.16)       │ (4 ref src)  │ Avg: 2.1          │
│    Gross: +29.8%    │ ARS vs CHE • 21:00 │ Betclic: 2.30     │ Prob: 53.2%  │ [Inspect ➔]       │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
                              ▲
                              │ (Kliknięcie wiersza / Inspect)
                              ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                   SLIDE-OVER INSPECTOR DRAWER (520px — Bez deformacji tabeli)                   │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [Nagłówek kandydata] Bukkayo Saka — Over 1.5 Shots on Target (Arsenal vs Chelsea)        [✕ Zamknij]│
│                                                                                                  │
│ [Zakładka 1: Matematyka Net EV & Podatki]                                                        │
│ • Rozbicie Gross EV vs Net EV po 12% podatku obrotowym (TaxEngine)                               │
│ • Porównanie efektywności: Superbet (2.45 -> eff: 2.156) vs Betclic 0% promo (2.30)              │
│                                                                                                  │
│ [Zakładka 2: Macierz Konsensusu Zagranicznego]                                                   │
│ • Kursy: Bet365 (1.90), Unibet (1.88), Pinnacle (1.93), Betfair (1.91)                           │
│ • Wyliczone Fair Odds: 1.88 • Prawdopodobieństwo bez marży: 53.19%                               │
│                                                                                                  │
│ [Zakładka 3: Historia Formy StatsHub]                                                            │
│ • Ostatnie 10 meczów: [✓][✓][✓][✗][✓][✓][✓][✗][✓][✓] (8/10)                                      │
│ • Średnia dom / wyjazd, minuty na mecz, bezpośrednie starcia H2H                                 │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 8.1. Zintegrowany Układ 5 Kolumn Tabeli `/playerprops`

Zamiast 9 wąskich, chaotycznych kolumn, wprowadzamy **5 czytelnych stref analitycznych**:

1. **Kolumna 1: VALUE & DECISION (Kotwica decyzyjna)**:
   - Duży, kontrastowy badge Net EV (np. `+14.2% Net EV` na tle `--val-positive-bg`).
   - Etykieta statusu decyzyjnego: `VALUE BET` (zielony), `BELOW THRESHOLD` (żółty), `REF GAP` (niebieski).
   - Metadane: Gross EV i przewaga punktowa (`Gross: +29.8% • Edge: +12.4pp`).
2. **Kolumna 2: CANDIDATE & MARKET (Podmiot i linia)**:
   - Nazwisko zawodnika / nazwa drużyny (pogrubione `0.95rem` z małym tagiem pozycji `FWD` / `MID` lub roli).
   - Badge rynku i linii: np. `OVER 1.5 Shots on Target` (wyraźny, z ikoną statystyki).
   - Nazwa meczu, liga i godzina rozpoczęcia (`Arsenal vs Chelsea • Premier League • Sob 21:00`).
3. **Kolumna 3: POLISH EXECUTION ODDS (Kursy egzekucyjne)**:
   - Zestawienie kursów z polskich legalnych źródeł (Superbet, Betclic).
   - Wyraźne oznaczenie najlepszego kursu (`best`) z automatycznym wyliczeniem kursu efektywnego po potrąceniu podatku 12% (`eff: 2.16` dla Superbet vs `0% tax` dla Betclic).
   - Przycisk szybkiego kopiowania parametrów zakładu na hoverze.
4. **Kolumna 4: REFERENCE FAIR & CONSENSUS (Wycena rynkowa)**:
   - Konsensus zagraniczny (np. `1.92` z 4 źródeł).
   - Wartość Fair Odds i reference probability (`Fair: 52.1% (1.92)`).
5. **Kolumna 5: STATSHUB TREND & ACTION (Kontekst i inspekcja)**:
   - Pasek trafień w oknie próby (`8/10 • 80%`) z mikrowykresem.
   - Średnia sezonowa i forma: `Avg: 2.1 (L5: 2.4, L10: 2.1)`.
   - Dedykowany przycisk `Inspect ➔` otwierający Slide-over Drawer.

---

## 9. Global UI Direction (Harmonizacja pozostałych widoków)

Każdy z pozostałych widoków platformy zostanie doprowadzony do jednolitego standardu:

### 9.1. Dashboard (`#view-dashboard`)
- **Karty Hero KPI**: Ujednolicenie 6 kafelków (`Discovered`, `Selected`, `Matched`, `Markets`, `Surebets`, `Valuebets`) z użyciem stałopozycyjnego fontu `JetBrains Mono` i subtelnych obramowań `--border-subtle`.
- **Panel "Last Scan Execution"**: Zastąpienie surowych wewnętrznych kodów etapów (`SUPERBET_WORKER_STAGE_1`) przez eleganckie podsumowanie stanu potoku: gotowość bukmacherów, czas wykonania, liczba zmapowanych rynków i wykryte błędy.
- **Tabela "Recent Scan History"**: Dodanie lepkiego nagłówka, formatowania czasu relatywnego i filtracji udanych cykli.

### 9.2. Opportunity Explorer (`#view-opportunities`)
- Zunifikowany filtr z zakładkami: `🔥 All`, `🎯 Player Props`, `👥 Team Props`, `💰 Valuebets`, `♻️ Surebets`, `🎁 Boosters`.
- Zastąpienie pełnoekranowego przełączania podwidoku (`#opp-explorer-detail-view`) przez wspólny komponent Slide-over Drawer, co zachowuje pozycję przewijania listy głównej.
- Kalkulator stawek surebetów (`#detail-stake-calculator-card`): zwężenie paddingów, czytelniejszy podział kwot stawek i zysku gwarantowanego.

### 9.3. Event Browser (`#view-events`)
- Lista meczów po lewej stronie: dodanie wyrazistych badge'y dla meczów posiadających aktywne valuebety (`⚡ Value`) lub surebety (`♻️ Surebet`).
- Macierz kursowa po prawej stronie: dodanie szybkiej wyszukiwarki rynków (`1X2`, `Double Chance`, `Totals`, `Handicap`) i lepki nagłówek tabeli kursów.

### 9.4. Provider Monitor (`#view-providers`)
- Karty bukmacherów (Superbet, Betclic, Bet365, Unibet, Odds API): wskaźnik stanu zdrowia (Health Pulse), czas ostatniej odpowiedzi, wskaźnik limitu zapytań (Quota Bar) oraz przycisk manualnego wyzwolenia workera.

### 9.5. Historical Analytics (`#view-history`)
- Wykres Canvas 2D: dodanie czytelnych podziałek osi Y (kursy dziesiętne z krokiem 0.10), podziałek czasowych na osi X, interaktywnego celownika (crosshair) podążającego za kursorem oraz legendy serii.

### 9.6. Scan Profiler (`#view-profiler`)
- Zachowanie inżynierskiej telemetrii faz (Stage 46), dodanie górnego paska podsumowania w języku naturalnym (np. *„Wszystkie systemy optymalne • Średni cykl: 412 ms • Główna faza: Normalizacja rynków (42%)”*).

### 9.7. Settings (`#view-settings`)
- Kafelkowa konfiguracja stawek podatkowych bukmacherów (Stage 22B) i progów minimalnego Net EV z natychmiastowym feedbackiem zapisu (Toast notification).

---

## 10. Motion & Interaction Philosophy

### 10.1. Zasady Filozofii Ruchu
1. **Funkcjonalność ponad dekoracją**: Ruch ma wspierać orientację użytkownika w danych, a nie popisywać się efektami.
2. **Krótki czas trwania (Snappy & Responsive)**: Wszystkie przejścia mieszczą się w przedziale **120 ms – 220 ms**.
3. **Fizyka ruchu (Easing)**: Użycie naturalnej krzywej `cubic-bezier(0.16, 1, 0.3, 1)` (szybki start, płynne wyhamowanie).
4. **Wyciszenie (Reduced Motion)**: Pełne poszanowanie `@media (prefers-reduced-motion: reduce)` poprzez redukcję czasu trwania animacji do `0.01ms`.

### 10.2. Macierz Mikrointerakcji

| Interakcja | Element | Zastosowany efekt | Czas trwania & Easing | Cel UX |
|---|---|---|---|---|
| **Otwarcie panelu szczegółów** | Slide-over Drawer | `transform: translateX(0)` z przyciemnieniem tła | `220ms` • `cubic-bezier(0.16, 1, 0.3, 1)` | Utrzymanie kontekstu tabeli bez gwałtownego przeskoku. |
| **Rozwinięcie filtrów** | Filter Accordion | `grid-template-rows: 0fr -> 1fr`, `opacity` | `180ms` • `ease-out` | Płynne odsłonięcie zaawansowanych opcji bez layout shiftu. |
| **Hover wiersza tabeli** | `.prop-table-row` | `background-color: var(--surface-hover)` | `120ms` • `ease` | Sygnalizacja klikalności wiersza. |
| **Kopiowanie kursu** | Przycisk kopiowania | Zmiana ikony na `✓` i mikro-skalowanie `scale(1.15)` | `150ms` • `ease-in-out` | Natychmiastowe potwierdzenie zapisu do schowka. |
| **Aktualizacja danych na żywo** | Komórka kursu / EV | Subtelny błysk tła (Pulse flash) | `400ms` • `ease-out` | Zwrócenie uwagi na zmieniony kurs w czasie rzeczywistym. |
| **Przełączanie zakładek** | `.market-tab-btn` | Płynne przesunięcie akcentu tła | `150ms` • `ease` | Zrozumiała zmiana trybu pracy. |
| **Skeleton loader** | Pasek placeholder | Przesuwający się gradient (Shimmer) | `1.5s` • `infinite linear` | Zmniejszenie odczuwalnego czasu oczekiwania na API. |

---

## 11. Curated Reference Library

Poniższa biblioteka zawiera **konkretne, starannie dobrane wzorce wzornicze** przypisane do precyzyjnych wyzwań interfejsu Zielone Bety:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              CURATED REFERENCE LIBRARY                                 │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Reference 1: Multi-Dimensional Odds & Decision Data Table
- **Problem**: Prezentacja wielowymiarowych danych zakładu (zawodnik, rynek, kurs polski, kurs zagraniczny, Net EV, próba statystyczna) w jednym zwartym wierszu bez chaosu.
- **Reference**: *Koyfin Market Watch / Action Network Pro Prop Matrix*
- **URL**: `https://app.koyfin.com` / `https://www.actionnetwork.com/props`
- **What to study**: 
  - Hierarchia typograficzna w komórkach: główna liczba pogrubiona (`tabular-nums`), metadane w drugiej linii w kolorze szarym.
  - Zestawienie wartości własnej vs konsensusu rynkowego w stałej szerokości kolumn.
- **What to borrow**: 
  - Wzorzec dwuliniowej komórki: Nazwisko + mecz, kurs główny + kurs efektywny netto.
  - Oznaczenie lidera rynkowego (Best Bookmaker) z subtelnym obramowaniem.
- **What NOT to copy**: 
  - Przeładowane, jaskrawe banery bukmacherskie, logotypy affiliate zajmujące pół ekranu.

### Reference 2: Slide-Over Inspector Sheet / Detail Drawer
- **Problem**: Deformacja tabeli głównej po otwarciu panelu bocznego inspekcji na `/playerprops`.
- **Reference**: *Stripe Dashboard / Supabase Table Row Inspector*
- **URL**: `https://dashboard.stripe.com` / `https://supabase.com/docs/guides/database/tables`
- **What to study**: 
  - Drawer wysuwający się z prawej krawędzi (`position: fixed; right: 0; width: 520px`) z warstwą `backdrop-scrim`.
  - Możliwość zamykania klawiszem `Escape`, kliknięciem w tło lub przyciskiem `✕`.
  - Wewnętrzny podział na zakładki analityczne ze stałym nagłówkiem i stopką akcji.
- **What to borrow**: 
  - Bezkonfliktowy montaż panelu nad tabelą bez zmieniania szerokości kolumn tabeli w tle.
- **What NOT to copy**: 
  - Wielopoziomowe, zagnieżdżone modale wewnątrz draweru.

### Reference 3: Compact Multi-Dimensional Filter Toolbar
- **Problem**: 12 kontrolek filtrów zajmujących ponad 260px przestrzeni pionowej na `/playerprops`.
- **Reference**: *Linear Filter System & GitHub Issues Filter Bar*
- **URL**: `https://linear.app/features` / `https://github.com`
- **What to study**: 
  - Podział na filtry pierwszego rzędu (Search, główna statystyka, próg EV) oraz rozwijany panel „More Filters”.
  - Wyświetlanie aktywnej liczby nałożonych filtrów w postaci małego badge'a (`Filters (3)`).
  - Przycisk szybkiego resetowania wszystkich filtrów jednym kliknięciem.
- **What to borrow**: 
  - Kompaktowy, horyzontalny pasek narzędziowy o stałej wysokości `44px`.
- **What NOT to copy**: 
  - Pełnoekranowy modal filtrowania z aplikacji mobilnych.

### Reference 4: Probability, Margin & Sample Size Trend Visualization
- **Problem**: Czytelna prezentacja trafień w próbie L10 (np. 8/10, 80%) oraz średnich statystycznych bez zajmowania dużo miejsca.
- **Reference**: *Opta Analyst StatsHub / StatMuse Trend Visualizer*
- **URL**: `https://theanalyst.com` / `https://www.statmuse.com`
- **What to study**: 
  - Kompaktowy pasek postępu (Hit Rate Bar) o wysokości `6px` zintegrowany z ułamkiem liczbowym.
  - Wyraźne zestawienie średniej krótkoterminowej (L5) i długoterminowej (L10).
- **What to borrow**: 
  - Miniaturowy wskaźnik trafień ze statusem wypełnienia szmaragdem dla $\ge 70\%$.
- **What NOT to copy**: 
  - Wielkie kołowe wykresy typu pie-chart lub animacje 3D marnujące przestrzeń.

### Reference 5: Sticky Table Header & Tabular Number Discipline
- **Problem**: Utrata kontekstu kolumn podczas przewijania długich list oraz drżenie liczb przy auto-refreshu.
- **Reference**: *Vercel Analytics Data Grid / Raycast Store Data Tables*
- **URL**: `https://vercel.com/analytics` / `https://www.raycast.com`
- **What to study**: 
  - Idealnie stabilne nagłówki z `position: sticky; top: 0` z tłem nieprzezroczystym i 1px cieniem oddzielającym.
  - Własność `font-variant-numeric: tabular-nums` zapobiegająca przeskokom szerokości cyfr.
- **What to borrow**: 
  - Klasa pomocnicza `.tabular-nums` i semantyczny token tła nagłówka `--surface-table-header`.
- **What NOT to copy**: 
  - Ukrywanie nagłówków kolumn na korzyść widoku czysto kafelkowego na desktopie.

---

## 12. Design System Specification

### 12.1. Zmienne Globalne (CSS Custom Properties)

```css
/* Zielone Bety — Design Tokens Specification (v2.1) */

:root {
  /* Surfaces */
  --surface-canvas: #090D14;
  --surface-card: #111622;
  --surface-elevated: #161D2C;
  --surface-hover: #1C2538;
  --surface-active: #232E46;
  --surface-input: #0C101A;
  --surface-table-header: #0E131F;

  /* Borders */
  --border-subtle: rgba(255, 255, 255, 0.07);
  --border-strong: rgba(255, 255, 255, 0.14);
  --border-focus: #3B82F6;

  /* Typography */
  --font-sans: 'Outfit', -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
  --font-mono: 'JetBrains Mono', 'SF Mono', Menlo, monospace;
  --text-primary: #F8FAFC;
  --text-secondary: #94A3B8;
  --text-muted: #64748B;
  --text-inverse: #090D14;

  /* Brand */
  --brand-primary: #3B82F6;
  --brand-hover: #2563EB;
  --brand-subtle: rgba(59, 130, 246, 0.12);

  /* Semantic Values */
  --val-positive: #10B981;
  --val-positive-bg: rgba(16, 185, 129, 0.14);
  --val-positive-border: rgba(16, 185, 129, 0.35);
  --val-positive-high: #34D399;
  --val-positive-high-bg: rgba(16, 185, 129, 0.22);

  --val-warning: #F59E0B;
  --val-warning-bg: rgba(245, 158, 11, 0.12);
  --val-warning-border: rgba(245, 158, 11, 0.30);

  --val-negative: #EF4444;
  --val-negative-bg: rgba(239, 68, 68, 0.12);
  --val-negative-border: rgba(239, 68, 68, 0.30);

  --val-reference: #0EA5E9;
  --val-reference-bg: rgba(14, 165, 233, 0.12);
  --val-reference-border: rgba(14, 165, 233, 0.30);

  /* Spacing */
  --space-1: 0.25rem;
  --space-2: 0.50rem;
  --space-3: 0.75rem;
  --space-4: 1.00rem;
  --space-5: 1.25rem;
  --space-6: 1.50rem;
  --space-8: 2.00rem;

  /* Radii */
  --radius-xs: 3px;
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-full: 9999px;

  /* Shadows */
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.25);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.30);
  --shadow-lg: 0 12px 32px rgba(0, 0, 0, 0.45);
  --shadow-drawer: -8px 0 32px rgba(0, 0, 0, 0.55);

  /* Transitions */
  --transition-fast: 120ms ease;
  --transition-normal: 180ms ease-out;
  --transition-drawer: 220ms cubic-bezier(0.16, 1, 0.3, 1);
}
```

### 12.2. Specyfikacja Komponentów

1. **Komponent: `.net-ev-pill`**:
   - `display: inline-flex; align-items: center; gap: 0.35rem;`
   - `padding: 0.25rem 0.6rem; border-radius: var(--radius-sm);`
   - `font-family: var(--font-mono); font-weight: 700; font-size: 0.95rem;`
   - `background: var(--val-positive-bg); color: var(--val-positive); border: 1px solid var(--val-positive-border);`
   - Wariant High EV ($\ge 8\%$): `background: var(--val-positive-high-bg); color: var(--val-positive-high);`
   - Wariant Negative / Below: `background: var(--val-warning-bg); color: var(--val-warning); border-color: var(--val-warning-border);`

2. **Komponent: `.slide-over-drawer`**:
   - `position: fixed; top: 0; right: 0; bottom: 0; width: min(520px, 92vw);`
   - `background: var(--surface-elevated); border-left: 1px solid var(--border-strong);`
   - `box-shadow: var(--shadow-drawer); z-index: 200; display: flex; flex-direction: column;`
   - `transform: translateX(100%); transition: transform var(--transition-drawer);`
   - Klasa aktywna `.open`: `transform: translateX(0);`
   - Tło maskujące `.drawer-backdrop`: `position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5); backdrop-filter: blur(2px); z-index: 190;`

3. **Komponent: `.data-table`**:
   - `width: 100%; border-collapse: separate; border-spacing: 0;`
   - `th`: `position: sticky; top: 0; z-index: 10; background: var(--surface-table-header); padding: 0.65rem 0.85rem; font-size: 0.72rem; color: var(--text-muted); border-bottom: 1px solid var(--border-strong);`
   - `td`: `padding: 0.65rem 0.85rem; border-bottom: 1px solid var(--border-subtle); vertical-align: middle;`
   - `tbody tr:hover`: `background: var(--surface-hover); cursor: pointer;`

4. **Komponent: `.skeleton-row`**:
   - Zastępuje statyczny tekst ładowania. Paski o wysokości `14px`, `border-radius: 4px`, z animacją shimmer `linear-gradient(90deg, var(--surface-card) 25%, var(--surface-hover) 50%, var(--surface-card) 75%)`.

---

## 13. Incremental Implementation Roadmap

Wdrożenie zostanie przeprowadzone w **6 bezpiecznych, modułowych i odwracalnych etapach**. Każdy etap jest niezależny technicznie i może być natychmiast zweryfikowany w runtime.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   INKREMENTALNA STRATEGIA WDROŻENIA                         │
├─────────────────────────────────────────────────────────────────────────────┤
│ ETAP 1: Fundamenty CSS & Dual-Theme Tokens (styles.css)                     │
│         ↓                                                                   │
│ ETAP 2: Komponenty Współdzielone (Tabele, Badges, Przyciski, Skeleton)      │
│         ↓                                                                   │
│ ETAP 3: /playerprops Redesign (Hierarchia 5 kolumn, Drawer, Filtry)         │
│         ↓                                                                   │
│ ETAP 4: Opportunity Explorer, Event Browser & Dashboard Harmonization       │
│         ↓                                                                   │
│ ETAP 5: Provider Monitor, Profiler, History & Settings Polish               │
│         ↓                                                                   │
│ ETAP 6: Responsywność, Dostępność WCAG & Motion Gate                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Etap 1: Fundamenty CSS & Dual-Theme Tokens (Shared Foundation)
- **Zakres**: Plik `web/styles.css`.
- **Zadania**:
  1. Zdefiniowanie pełnego zestawu semantycznych tokenów CSS (`:root` i `[data-theme="light"]`).
  2. Wprowadzenie tokenów dla powierzchni (`--surface-*`), obramowań (`--border-*`) i kolorów wartości (`--val-*`).
  3. Konfiguracja `font-variant-numeric: tabular-nums` dla klas numerycznych.
- **Pliki**: `web/styles.css`.
- **Rezultat**: Brak błędów kontrastu w trybie jasnym, eliminacja zahardkodowanych kolorów w CSS.
- **Weryfikacja**: Inspekcja kontrastu WCAG $\ge 4.5:1$ w obu motywach.

### Etap 2: Komponenty Współdzielone (Shared UI Layer)
- **Zakres**: Pliki `web/styles.css`, `web/index.html`.
- **Zadania**:
  1. Dodanie `position: sticky; top: 0` do nagłówków wszystkich tabel (`.data-table th`).
  2. Ujednolicenie stylów przycisków (`.btn`, `.btn-primary`, `.btn-outline`, `.btn-sm`).
  3. Wdrożenie jednolitego zestawu klas dla etykiet i badge'y (`.badge`, `.net-ev-pill`).
  4. Dodanie CSS Skeleton Loaderów na czas ładowania danych.
- **Pliki**: `web/styles.css`, `web/index.html`.
- **Rezultat**: Płynne przewijanie tabel bez gubienia kontekstu, brak skoków układu podczas ładowania.
- **Weryfikacja**: Przewijanie listy 50 wierszy w przeglądarce, test ładowania.

### Etap 3: `/playerprops` UX Transformation (Core Decision Workspace)
- **Zakres**: Pliki `web/app.js` (`renderPropsTable`, `showPropDetail`), `web/index.html` (`#view-playerprops`), `web/styles.css`.
- **Zadania**:
  1. Przebudowa wiersza tabeli do 5 zintegrowanych bloków (Value & Net EV na pozycji nr 1).
  2. Implementacja komponentu **Slide-over Drawer (`#prop-detail-drawer`)** zastępującego flexowe ściskanie tabeli.
  3. Uporządkowanie zakładek widoku do 2 głównych trybów: `🏆 Qualified Value Bets` oraz `🔬 Pipeline Candidates & Diagnostics`.
  4. Zmniejszenie wysokości paska filtrów: linia podstawowa + rozwijany popover „Więcej filtrów”.
  5. Ujednolicenie komunikatów pustych i błędów w języku polskim z profesjonalną terminologią bukmacherską.
- **Pliki**: `web/app.js`, `web/index.html`, `web/styles.css`.
- **Rezultat**: Skrócenie czasu oceny okazji o > 60%, zero deformacji tabeli przy inspekcji, oszczędność 200px przestrzeni roboczej.
- **Weryfikacja**: Otwarcie szczegółów dowolnego propa, sprawdzenie geometrii tabeli, filtrowanie okazji.

### Etap 4: Opportunity Explorer, Events Browser & Dashboard Harmonization
- **Zakres**: Pliki `web/app.js`, `web/index.html`, `web/styles.css`.
- **Zadania**:
  1. Podpięcie Slide-over Drawera w Opportunity Explorerze (zamiast zastępowania całego ekranu).
  2. Wyróżnienie meczów z aktywnymi valuebetami w Event Browserze.
  3. Odświeżenie kafelków metryk i historii skanera na Dashboardzie.
- **Pliki**: `web/app.js`, `web/index.html`, `web/styles.css`.
- **Rezultat**: Spójne doświadczenie użytkownika (UX rhythm) pomiędzy wszystkimi głównymi widokami.
- **Weryfikacja**: Przejście przez Dashboard $\rightarrow$ Explorer $\rightarrow$ Events $\rightarrow$ Props.

### Etap 5: Provider Monitor, Profiler, History & Settings Alignment
- **Zakres**: Pliki `web/app.js`, `web/index.html`, `web/styles.css`.
- **Zadania**:
  1. Dodanie wskaźników health/quota do kart dostawców bukmacherskich.
  2. Dodanie osi Y i podpisów do wykresu Canvas 2D w History.
  3. Wdrożenie paska podsumowania w języku naturalnym w Profilerze.
  4. Synchronizacja przełącznika motywów w Settings z nagłówkiem głównym.
- **Pliki**: `web/app.js`, `web/index.html`, `web/styles.css`.
- **Rezultat**: Kompletny, profesjonalny stan wszystkich ekranów pomocniczych.
- **Weryfikacja**: Zmiana motywu w Settings, sprawdzenie wykresu historii.

### Etap 6: Responsywność, Dostępność WCAG & Motion Gate
- **Zakres**: Pliki `web/styles.css`, `web/index.html`, `web/app.js`.
- **Zadania**:
  1. Dostosowanie widoków tabelarycznych na laptopach (1024px–1366px), tabletach (768px–1023px) i mobile (<768px).
  2. Dodanie atrybutów `<label for="...">`, ról `role="tab"`, `aria-selected` oraz obsługi klawisza `Escape` dla drawera.
  3. Weryfikacja przejść animacyjnych $\le 220\text{ms}$ i reguły `prefers-reduced-motion`.
- **Pliki**: `web/styles.css`, `web/index.html`, `web/app.js`.
- **Rezultat**: Spełnienie standardu WCAG 2.1 AA, pełna responsywność bez poziomych pasków przewijania na całym oknie.
- **Weryfikacja**: Inspekcja w `agent-browser` na viewportach 1920px, 1366px, 1024px, 375px.

---

## 14. Targeted Validation Plan

Zgodnie z zasadą: **ZERO PUSTYCH TESTÓW — MAŁA, CELOWANA WALIDACJA W RUNTIME**:

### 14.1. Zasady walidacji wizualnej (Visual & Runtime Verification)
Dla modyfikacji stylów CSS, układów tabel i animacji **nie tworzymy sztucznych testów jednostkowych w Pythonie/JS**. Walidacja odbywa się bezpośrednio w przeglądarce za pośrednictwem `agent-browser`:

1. **Kontrast i Theming**:
   - Weryfikacja kontrastu tekstu do tła w trybie ciemnym (Dark) i jasnym (Light) $\ge 4.5:1$.
   - Brak niewidocznych elementów przy przełączeniu `[data-theme="light"]`.
2. **Geometria Tabeli i Drawer**:
   - Otwarcie panelu szczegółów propa (`Inspect ➔`) $\rightarrow$ tabela główna zachowuje 100% szerokości i nie zawija kolumn.
   - Naciśnięcie klawisza `Escape` lub kliknięcie w maskę $\rightarrow$ płynne zamknięcie panelu w $\le 220\text{ms}$.
3. **Viewport Matrix**:
   - Desktop 1920x1080 (Pełny widok tabeli, drawer 520px).
   - Laptop 1366x768 (Pełny widok, brak deformacji).
   - Tablet 1024x768 (Zwinięty sidebar do ikon, tabela z poziomym scrollem wewnątrz kontenera).
   - Mobile 375x812 (Kompaktowy widok kafelkowy, drawer pełnoekranowy).

### 14.2. Celowane testy zachowania (Targeted Behavioral Logic)
Dla kluczowych interakcji w `web/app.js` walidujemy deterministyczne zachowania:
1. **Przełączanie zakładek `/playerprops`**:
   - Kliknięcie `🏆 Value Bets` $\rightarrow$ wyświetlenie wyłącznie pozycji z `status === 'QUALIFIED'` lub `is_valuebet === true`.
   - Kliknięcie `🔬 Pipeline Candidates` $\rightarrow$ natychmiastowe wyświetlenie pełnej listy wraz z odrzuconymi pozycjami.
2. **Synchronizacja kalkulatora podatkowego**:
   - Zmiana stawki w `Settings` $\rightarrow$ natychmiastowe przeliczenie kursów efektywnych w tabeli propsów.

---

## 15. Backend Issues Detected

Podczas weryfikacji runtime i zapytań do API backendowego (`http://127.0.0.1:8000`) stwierdzono:

- **Stan ogólny backendu**: `HEALTHY`.
- **Wydajność API**: Wyjątkowo wysoka (< 1 ms dla endpointów REST).
- **Uwaga dot. routingu HTTP**:
  - Endpoint `/api/v1/props/global-scan` przyjmuje metodę `POST` z parametrami w Query Stringu (np. `?props_scope=ALL&time_horizon_days=7`). Wysłanie pustego `POST` bez parametrów zwraca `405 Method Not Allowed` lub `422`. Klient `web/app.js` obsługuje to poprawnie poprzez `new URLSearchParams(params).toString()`.
- **Brak blokad architektonicznych**: Backend nie wymaga żadnych modyfikacji, aby w pełni obsłużyć nowy interfejs i Slide-over Drawer.

---

## 16. Explicit Non-Goals (Twarde Granice Projektu)

Podczas przyszłej implementacji bezwzględnie obowiązują następujące zakazy:

1. ❌ **Brak przepisywania frontendu**: Zakaz migracji do React, Next.js, Vue, Svelte, Angular czy innych bibliotek SPA.
2. ❌ **Brak instalacji ciężkich frameworków CSS**: Zakaz wprowadzania Tailwind CLI, Bootstrapa, bulma itp. Całość opiera się na istniejącym, zoptymalizowanym `web/styles.css`.
3. ❌ **Brak zmian w backendzie**: Zero modyfikacji silników skanera, algorytmów Surebet/Valuebet, TaxEngine, StatsHub, bazy danych SQLite czy endpointów FastAPI.
4. ❌ **Brak usuwania logiki diagnostycznej**: Żadna funkcja analityczna, telemetria czy kod odrzucenia nie zostaną usunięte — ulegają jedynie eleganckiej separacji od widoku produkcyjnego.
5. ❌ **Brak pustych testów jednostkowych**: Brak tworzenia testów „na sztuczny coverage” testujących arkusze CSS; walidacja wyłącznie przez runtime i inspekcję wizualną.
6. ❌ **Brak kopiowania obcych stylów w całości**: Wdrażane są wyłącznie celowane, przemyślane wzorce (patrz punkt 11).

---

*Dokument przygotowany i zweryfikowany w oparciu o kod źródłowy repozytorium Zielone Bety.*

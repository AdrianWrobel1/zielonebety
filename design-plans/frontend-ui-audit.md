# AUDIT ONLY — NO IMPLEMENTATION PERFORMED

# Profesjonalny Audyt UI/UX Frontendu Platformy Zielone Bety
**Inkrementalne doskonalenie istniejącego interfejsu (bez przepisywania architektury)**

Data audytu: Wrzesień 2026  
Środowisko: Zielone Bety v2.0 (FastAPI REST API + Vanilla JS/HTML/CSS SPA)  
Główny obszar fokusowy: `/playerprops` (Global Props Scanner & Decision Workspace)  
Zakres audytu: Cały frontend produkcyjny (Dashboard, Explorer, Events, Providers, Props, History, Profiler, Notifications, Settings)

---

## 1. Executive Summary

Niniejszy audyt stanowi kompleksową, techniczną i wzorniczą ocenę obecnego stanu warstwy prezentacji (Frontend) platformy **Zielone Bety**. Zgodnie z wytycznymi, analiza została przeprowadzona z perspektywy **inkrementalnego ulepszania istniejącego UI** — bez wprowadzania równoległych frameworków, bez „big bang redesignu” i bez modyfikacji logiki backendowej czy modeli wyceny matematycznej.

### Kluczowe wnioski ogólne:
1. **Solidne fundamenty architektoniczne**: Aplikacja jest zaimplementowana jako szybka, lekka aplikacja SPA (`web/index.html`, `web/app.js`, `web/styles.css`) komunikująca się wyłącznie przez REST API z FastAPI. Czas renderowania i odpowiedzi API wynosi < 2 ms. Architektura ta jest w pełni wystarczająca i nie wymaga przepisywania.
2. **Przesunięcie w stronę panelu diagnostycznego**: Największym problemem całego frontendu jest zacieranie granicy między **informacjami produkcyjno-decyzyjnymi** (gdzie jest zysk, jaki jest Net EV, u którego bukmachera zagrać) a **telemetrią diagnostyczną/developerską** (kody odrzuceń filtrów, etapy parsowania, statusy pipeline'u).
3. **Naruszenie hierarchii skanowania na `/playerprops`**: Na kluczowym widoku `/playerprops` najważniejsze wskaźniki decyzyjne (`VALUE BET`, `Net EV %`, `Kursy Bukmacherów`) znajdują się dopiero w 7. i 9. kolumnie tabeli, a ekran jest przeładowany 12 kontrolkami filtrów i 11 polami statystyk lejka.
4. **Możliwość osiągnięcia poziomu profesjonalnego bez rewolucji**: Wszystkie zidentyfikowane problemy można rozwiązać w sposób **czysto inkrementalny**, modyfikując istniejące arkusze CSS i szablony komponentów w `app.js` i `index.html`.

---

## 2. Current Frontend Structure

Warstwa frontendu Zielone Bety znajduje się w katalogu `web/` i jest serwowana statycznie przez FastAPI (`api/fastapi_app.py`):

```
web/
├── index.html   (87.8 KB — struktura layoutu, szablony widoków SPA, modale)
├── styles.css   (36.4 KB — system tokenów CSS, motyw ciemny/jasny, style komponentów)
└── app.js       (207.6 KB — stan klienta, router hashy/ścieżek, klient REST API, renderery widoków)
```

### 2.1. Routes & Widoki SPA

Aplikacja wykorzystuje hybrydowy routing oparty na `window.location.hash` z obsługą bezpośrednich ścieżek URL fallbackowanych w FastAPI:

| Widok / Route | Identyfikator kontenera | Główny cel użytkownika |
|---|---|---|
| `#dashboard` / `/` | `#view-dashboard` | Monitorowanie stanu skanera, aktywności bukmacherów i ostatnich cykli |
| `#opportunities` | `#view-opportunities` | Przeglądanie i matematyczna inspekcja surebetów i valuebetów |
| `#providers` | `#view-providers` | Monitorowanie frameworku dostawców i ręczne wyzwalanie workerów |
| `#events` | `#view-events` | Eksploracja meczów kanonicznych i macierzy kursowej rynków |
| **`#playerprops`** | **`#view-playerprops`** | **Wyszukiwanie i ocena valuebetów na Player & Team Props (StatsHub)** |
| `#history` | `#view-history` | Wykresy czasowe kursów i historyczny replay |
| `#profiler` | `#view-profiler` | Szczegółowa telemetria wydajnościowa cyklu skanowania (Stage 46) |
| `#notifications` | `#view-notifications` | Log dostarczenia powiadomień i stan kanałów alertów |
| `#settings` | `#view-settings` | Preferencje UI, parametry skanera i progi podatkowe bukmacherów |

### 2.2. Stan i Zarządzanie Danymi w Kliencie

Stan w `web/app.js` jest scentralizowany w obiekcie `state`:
- Posiada odrębne gałęzie danych: `opportunities`, `providers`, `events`, `playerProps`, `latestScan`, `scanHistory`, `schedulerStatus`.
- Automatyczne odświeżanie (`startAutoRefresh`) działa w tle co 10 sekund dla widoków wymagających aktualizacji.
- Wszystkie zapytania kierowane są do zunifikowanych endpointów REST (`/api/v1/*`).

---

## 3. Current UI / Visual Language

### 3.1. Typografia
- **Font podstawowy**: `'Outfit', -apple-system, BlinkMacSystemFont, sans-serif` (Google Fonts). Czytelny font bezszeryfowy o nowoczesnym charakterze.
- **Font numeryczny / telemetryczny**: `'JetBrains Mono', monospace`. Używany dla kursów, wartości EV, identyfikatorów i dat.
- **Problem**: Brak konsekwentnego stosowania własności `font-variant-numeric: tabular-nums` w fontach bezszeryfowych, co powoduje skakanie kolumn przy odświeżaniu liczb. Zbyt małe nagłówki kolumn tabel (`0.72rem` z `letter-spacing: 0.05em`) utrudniają szybki rzut oka.

### 3.2. Paleta Kolorów i Tokeny CSS
- Główny akcent: `--accent-primary: #10B981` (Zielony "Zielone Bety").
- Tła ciemne: `--bg-main: #0B0E14`, `--bg-card: rgba(22, 28, 36, 0.75)`, `--bg-card-border: rgba(255, 255, 255, 0.08)`.
- Tła jasne: `--bg-main: #F3F4F6`, `--bg-card: rgba(255, 255, 255, 0.85)`.
- Kolory semantyczne:
  - Sukces / Value / Zysk: `#10B981` / `#34D399` / `#6EE7B7`
  - Informacja / Reference: `#3B82F6` / `#60A5FA`
  - Ostrzeżenie / Below Threshold: `#F59E0B` / `#FCD34D`
  - Błąd / Rejection / Ryzyko: `#EF4444` / `#FCA5A5`
- **Problem systemowy**: Wiele komponentów posiada zahardkodowane wartości `rgba(...)` bezpośrednio w szablonach JavaScript (`app.js`) oraz arkuszu `styles.css`. W trybie jasnym (`data-theme="light"`) jasne kolory tekstu (np. `#FCA5A5`, `#60A5FA`) tracą kontrast poniżej wymogu WCAG AA (4.5:1).

### 3.3. Promienie (Radii), Cienie i Głębia
- Promienie: `--radius-sm: 6px`, `--radius-md: 10px`, `--radius-lg: 16px`.
- Efekty szkła: `backdrop-filter: blur(12px)`.
- Cienie: Dyskretne, nowoczesne cienie warstwowe (`--shadow-md: 0 8px 24px rgba(0, 0, 0, 0.4)`).
- **Ocena**: Estetyka tła i kart („Dark Glassmorphism”) jest atrakcyjna i nowoczesna. Problemem nie jest styl tła, lecz nadmierna gęstość i brak światła pomiędzy blokami danych.

---

## 4. Shared Components & Patterns

Audyt wykazał obecność kilku kluczowych wzorców współdzielonych:

```
┌─────────────────────────────────────────────────────────────┐
│                      SHARED UI LAYER                        │
├─────────────────┬──────────────────┬────────────────────────┤
│  App Layout     │  Navigation      │  Data Displays         │
│  - Sidebar 260px│  - Nav Items     │  - .data-table         │
│  - Sticky Header│  - Badges        │  - .metric-card        │
│  - Content Area │  - Scope Switcher│  - Status Pills / Dots │
├─────────────────┼──────────────────┼────────────────────────┤
│  Forms & Inputs │  Feedback        │  Overlays              │
│  - .form-control│  - Toast Banner  │  - Slide / Modal       │
│  - .filter-card │  - Empty State   │  - Details Container   │
│  - Custom Select│  - Spinner       │  - Stake Calculator    │
└─────────────────┴──────────────────┴────────────────────────┘
```

### Zidentyfikowane problemy w komponentach współdzielonych:
1. **`.data-table`**:
   - Brak `sticky header` przy przewijaniu długich list (użytkownik traci kontekst nazw kolumn).
   - Wiersze nie mają wyraźnego stanu `focus-visible` przy nawigacji klawiaturą.
   - Komórki zawierają zbyt wiele zagnieżdżonych linii tekstu (`<div>`, `<small>`) bez stałego rytmu pionowego.
2. **`.filter-card`**:
   - Zajmuje zbyt dużo miejsca w pionie (brak podziału na filtry podstawowe i zaawansowane).
   - Brak przycisku „Wyczyść filtry” (Reset Filters) we wszystkich widokach.
3. **Pills & Badges**:
   - Niejednolita semantyka: niektóre statusy używają `.badge-accent` (niebieski), inne `.badge-success` (zielony), a niektóre niestandardowych klas `.net-ev-pill`, `.polish-quote-pill`.

---

## 5. `/playerprops` Detailed Review

Widok `/playerprops` jest sercem analitycznym platformy dla rynków statystycznych zawodników i drużyn.

### 5.1. Ocena względem Wymaganej Hierarchii Informacji

Wymagana hierarchia decyzyjna:
$$\text{VALUE BET} \rightarrow \text{Net EV} \rightarrow \text{Fair Odds} \rightarrow \text{Bookmaker Odds} \rightarrow \text{Reference Prob} \rightarrow \text{StatsHub Trend} \rightarrow \text{Sample} \rightarrow \text{Match} \rightarrow \text{Market/Line} \rightarrow \text{Diagnostics}$$

#### Rzeczywisty stan obecny w tabeli `/playerprops`:
1. Kolumna 1: `Type` (Badge: PLAYER / TEAM) — mało istotne, zajmuje całą kolumnę.
2. Kolumna 2: `Player / Team` (Zawodnik + Drużyna + Rywal).
3. Kolumna 3: `Match & Kickoff` (Nazwa meczu + Liga + Data).
4. Kolumna 4: `Market & Line` (Badge: OVER 0.5 SHOTS + FULL_TIME).
5. Kolumna 5: `Polish Bookmaker & Odds` (Superbet / Betclic pills).
6. Kolumna 6: `Foreign Reference & Fair` (Konsensus + Fair Odds).
7. Kolumna 7: `Net EV (%)` (Wskaźnik Net EV + Pewność).
8. Kolumna 8: `StatsHub Trend Context` (Pasek trafień + Średnie L5/L10).
9. Kolumna 9: `Action` (Badge statusu + Przycisk `Inspect →`).

#### Wnioski z hierarchii:
- **Kluczowa wartość (Net EV i kwalifikacja VALUE BET) znajduje się na samym końcu wiersza (kolumny 7 i 9)** zamiast przyciągać wzrok jako pierwszorzędny wskaźnik.
- Użytkownik musi przeskanować wzrokiem 6 kolumn metadanych, zanim dowie się, czy dany zakład w ogóle ma dodatnie Net EV.
- **Brak zwięzłego podsumowania kandydata**: Informacja o zawodniku, meczu i linii jest rozbita na 3 osobne szerokie kolumny.

### 5.2. Pasek Telemetrii i Panel Diagnostyczny (Funnel)
- 5 górnych kart metryk (`Trends Discovered`, `Polish Odds`, `Bettable Now`, `Reference Only`, `Match Uncertain`) jest przydatnych, ale dominuje nad samą tabelą.
- Rozwijany panel `🔬 SCAN & FUNNEL REJECTION DIAGNOSTICS` zawiera 11 boksów liczbowych. Po rozwinięciu zajmuje ponad 350 px wysokości, wypychając właściwe okazje poza pierwszy ekran.
- **Rekomendacja**: Utrzymać panel, ale domyślnie zwinąć go do kompaktowego paska podsumowania z możliwością podejrzenia szczegółów na żądanie.

### 5.3. Filtry i Zakładki Kategorii na `/playerprops`
- Obecnie występuje **12 kontrolek w formularzu** (`Horizon`, `Stat`, `Min EV`, `Tournaments`, `Min Odds`, `Position`, `Line Threshold`, `Bookmaker`, `Status`, `Sort`, `Limit`, `Search`) oraz **10 przycisków zakładek** (`Top Valuebets`, `Diagnostics`, `Player`, `Team`, `Superbet`, `Betclic`, `Below Threshold`, `Ref Gap`, `No Polish`).
- Zakładki mieszają wymiary: filtrują jednocześnie po typie podmiotu (`Player`/`Team`), bukmacherze (`Superbet`/`Betclic`) oraz statusie odrzucenia (`Below Threshold`/`Ref Gap`).
- **Rekomendacja**: Uporządkować filtry:
  - Wymiar 1 (Zakres): Istniejący `Scope Switcher` (PLAYER / TEAM / ALL).
  - Wymiar 2 (Widok): 2 główne zakładki: `🏆 Value Bets (Qualified)` oraz `🔬 All Candidates & Pipeline`.
  - Filtry bukmachera i ligi przenieść do kompaktowych dropdownów w toolbarze.

### 5.4. Panel Inspekcji Szczegółowej (`#prop-detail-container`)
- Po kliknięciu `Inspect →`, panel boczny pojawia się obok tabeli, ściskając tabelę z `flex: 2` do małej szerokości.
- W efekcie 9 kolumn tabeli ulega drastycznemu ściśnięciu, teksty zawijają się wielowierszowo, a czytelność drastycznie spada.
- **Rekomendacja**: Zastąpić ściskanie tabeli eleganckim bocznym drawerem (Slide-over panel / Sheet) lub dedykowanym rozwijanym wierszem (Accordion / Drawer), który nie niszczy układu tabeli głównej.

---

## 6. Other Important Views Review

### 6.1. Dashboard (`#view-dashboard`)
- **Mocne strony**: Bardzo czytelny zestaw metryk hero (`Discovered`, `Selected`, `Matched`, `Markets`, `Surebets`, `Valuebets`).
- **Problemy**:
  - Tabela "Last Scan Execution" prezentuje szczegółowe etapy przetwarzania dostawców zamiast skupić się na statusie gotowości i wykrytych okazjach.
  - Tabela "Recent Scan History" nie posiada paginacji ani możliwości filtrowania tylko udanych cykli.

### 6.2. Opportunity Explorer (`#view-opportunities`)
- **Mocne strony**: Zunifikowany explorer z podziałem na zakładki (`All`, `Player Props`, `Team Props`, `Valuebets`, `Surebets`, `Boosters`).
- **Problemy**:
  - Widok szczegółowy (`#opp-explorer-detail-view`) jest całkowicie odrębnym podekranem zastępującym listę, co wymaga klikania „Back to Opportunities” i gubi pozycję przewijania listy.
  - Kalkulator stawek surebetów (`#detail-stake-calculator-card`) jest funkcjonalny, ale ma zbyt duży margines wewnętrzny i niepotrzebnie powtarza nagłówki tabeli presetów.

### 6.3. Event Browser (`#view-events`)
- **Mocne strony**: Układ 2-kolumnowy (Lista meczów po lewej, macierz kursowa po prawej) doskonale sprawdza się przy porównywaniu kursów 1X2 i Double Chance.
- **Problemy**:
  - Lista meczów po lewej stronie nie pokazuje wyraźnie, które mecze mają aktywne surebety/valuebety, a które są tylko zmapowane.
  - Przy dużej liczbie rynków brak wyszukiwarki wewnątrz samej macierzy kursowej wybranego meczu.

### 6.4. Provider Monitor (`#view-providers`)
- **Mocne strony**: Przejrzyste karty kafelkowe dla każdego bukmachera z przyciskiem manualnego uruchomienia.
- **Problemy**:
  - Tabela "Provider Performance & Audit Log" zawiera puste myślniki (`—`) w kolumnach `Last Run Duration`, `Discovered`, `Parsed`, dopóki nie zostanie wykonany manualny run. Powinna pobierać ostatni stan z `provider_snapshots`.

### 6.5. Historical Analytics (`#view-history`)
- **Mocne strony**: Płynny Canvas 2D z osią czasu i możliwością odtwarzania historii kursów (`Replay`).
- **Problemy**:
  - Brak podpisów osi Y (poziomów kursowych) i legendy serii bezpośrednio na wykresie.

### 6.6. Scan Profiler (`#view-profiler`)
- **Mocne strony**: Bardzo głęboka telemetria (fazy, workery, stragglery, percentyle opóźnień).
- **Problemy**:
  - Wygląd czysto inżynierski. Brak krótkiego podsumowania w języku naturalnym (np. „Główny bottleneck: Rate-limit Superbet API — 68% czasu cyklu”).

### 6.7. Settings (`#view-settings`)
- **Mocne strony**: Czysty układ kafelkowy z konfiguracją podatków bukmacherów (Stage 22B) i progów skanera.
- **Problemy**:
  - Zmiana motywu w ustawieniach nie zawsze synchronizuje się z przełącznikiem motywu w nagłówku głównym.

---

## 7. Production UI vs Diagnostics

Audyt wykazał istotne zjawisko tzw. **„Developer UI Bleed”** — przenikania struktur wewnętrznych silnika do interfejsu użytkownika:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       PRODUCTION vs DIAGNOSTICS                         │
├────────────────────────────────────┬────────────────────────────────────┤
│  PRODUCTION-FACING INFORMATION     │  DIAGNOSTIC / TELEMETRY DATA       │
│  (Co interesuje typera/inwestora)  │  (Co interesuje inżyniera)         │
├────────────────────────────────────┼────────────────────────────────────┤
│  • Zawodnik / Mecz / Rynek         │  • Funnel Rejection Reason Codes   │
│  • Zysk Net EV (%) po podatku      │  • Traces & Request Percentiles    │
│  • Najlepszy bukmacher i kurs      │  • Multi-Fixture Scoped Counts     │
│  • Kurs odniesienia (Fair Odds)    │  • Normalization Provider Drops    │
│  • Trend historyczny i próba (L10) │  • Worker IDs & Rate-Limit Waits   │
│  • Status (Zagraj / Obserwuj)      │  • Raw gRPC / HTTP payload logs    │
└────────────────────────────────────┴────────────────────────────────────┘
```

### Rekomendacja podziału:
1. **Domyślny widok produkcyjny**: Eksponuje wyłącznie elementy z lewej kolumny. Tabela ma być maksymalnie czysta, czytelna i zorientowana na decyzję.
2. **Warstwa diagnostyczna**: Pozostaje w 100% dostępna, ale zostaje przeniesiona do:
   - Dedykowanej zakładki `🔬 Diagnostics & Pipeline`,
   - Rozwijanego paska telemetrycznego (collapsible accordion),
   - Dedykowanego widoku `Scan Profiler` (dla inżynierów).
3. **Translacja kodów technicznych**: Kody takie jak `BELOW_VALUE_THRESHOLD`, `INSUFFICIENT_REFERENCE_SOURCES`, `POLISH_ODDS_UNAVAILABLE` powinny być wyświetlane użytkownikowi jako czytelne polskie/angielskie etykiety (np. *„Brak wartości po podatku”*, *„Brak konsensusu zagranicznego”*, *„Brak kursu PL”*).

---

## 8. UX/UI Findings

Zgodnie z wymogami, wszystkie istotne problemy zostały sklasyfikowane w formacie:
**Problem $\rightarrow$ Evidence $\rightarrow$ Impact $\rightarrow$ Severity $\rightarrow$ Recommended Direction**

### Finding F-01: Odwrócona hierarchia skanowania na `/playerprops`
- **Evidence**: W `web/app.js` (linie 3580–3612) wiersz tabeli rozpoczyna się od `Type Badge`, `Player Name`, `Match Name`, a kolumny `Net EV (%)` i `VALUE BET Status` znajdują się dopiero na 7. i 9. pozycji.
- **Impact**: Użytkownik musi przesuwać wzrok w poprzek 9 kolumn, aby ocenić, czy okazja jest wartościowa.
- **Severity**: `P1` (Wysoki — Top 3)
- **Recommended Direction**: Przemodelować układ wiersza tabeli:
  1. `Okazja & Net EV` (wyrazisty badge `+X.X% Net EV` z oznaczeniem `VALUE BET`),
  2. `Zawodnik / Drużyna & Rynek` (połączone w spójny blok z linią i statystyką),
  3. `Kurs Polski vs Fair Odds` (bezpośrednie zestawienie Superbet/Betclic z kursem odniesienia),
  4. `StatsHub Trend` (trafienia w oknie próby i średnia),
  5. `Akcja` (przycisk inspekcji).

### Finding F-02: Ściskanie tabeli głównej przez panel boczny inspekcji
- **Evidence**: W `styles.css` (linia 1354) `.props-layout-grid` przełącza `#props-table-container` na `flex: 2` i `#prop-detail-container` na `flex: 1.2`.
- **Impact**: Otwarcie szczegółów dowolnego propa natychmiast deformuje 9-kolumnową tabelę, powodując ucinanie tekstu i drastyczny spadek ergonomii.
- **Severity**: `P1` (Wysoki — Top 3)
- **Recommended Direction**: Zmienić zachowanie panelu szczegółów na **wysuwany drawer (Slide-over Sheet)** nakładający się z prawej strony z tłem przyciemniającym lub płynnie rozwijany wiersz wewnętrzny (Sub-row accordion), nie zmieniający geometrii tabeli głównej.

### Finding F-03: Przeładowanie formularza filtrującego na `/playerprops`
- **Evidence**: W `web/index.html` (linie 1016–1149) znajduje się 12 kontrolek w siatce `.props-controls-grid`, zajmujących znaczną część ekranu roboczego.
- **Impact**: Użytkownik ma utrudniony dostęp do danych, filtry spychają tabelę poniżej linii zgięcia (fold).
- **Severity**: `P2` (Średni)
- **Recommended Direction**: Wprowadzić 2-poziomowy pasek filtrów:
  - Pasek główny: Wyszukiwarka, Statystyka, Minimalne EV, Horyzont czasowy.
  - Przycisk `Więcej filtrów ▾`: Rozwija pozycje, ligi, bukmacherów i limity.

### Finding F-04: Mieszanie języka polskiego i angielskiego w komunikatach stanu
- **Evidence**: W `web/app.js` (linia 3417): `"No player props match the current filters. Brak zakwalifikowanych propsów w wybranym zakresie. Przeanalizowano kandydatów (10 odrzuconych w kolejnych etapach lejka)..."`
- **Impact**: Wrażenie braku dopracowania i niespójności produktu.
- **Severity**: `P2` (Średni)
- **Recommended Direction**: Ujednolicić język interfejsu zgodnie z wybranym ustawieniem języka w `state.settings.language` lub zachować profesjonalny, jednolity standard.

### Finding F-05: Niska widoczność i kontrast elementów w trybie jasnym (Light Theme)
- **Evidence**: W `styles.css` klasy `.quality-flag-tag`, `.rejection-chip`, `.badge-accent`, `.polish-quote-pill` używają jasnych odcieni tekstu (np. `#FCA5A5`, `#60A5FA`, `#A5B4FC`), które na jasnym tle `#FFFFFF` / `#F3F4F6` mają kontrast poniżej 3:1.
- **Impact**: Elementy statusowe są nieczytelne dla użytkowników korzystających z jasnego motywu.
- **Severity**: `P2` (Średni)
- **Recommended Direction**: Wprowadzić semantyczne zmienne CSS dla kolorów tekstu i tła etykiet z osobnymi definicjami dla `:root` i `[data-theme="light"]`.

### Finding F-06: Brak lepkich nagłówków tabel (`sticky table headers`)
- **Evidence**: Wszystkie tabele w aplikacji (`.data-table`) przewijają się wraz z zawartością, przez co nagłówki znikają przy listach powyżej 10 elementów.
- **Impact**: Użytkownik traci orientację, co oznacza dana liczba w kolumnie podczas przewijania.
- **Severity**: `P2` (Średni)
- **Recommended Direction**: Dodać `position: sticky; top: 0; background: var(--bg-card); z-index: 10;` dla elementów `.data-table th`.

### Finding F-07: Przeładowanie zakładkami kategorii o mieszanej semantyce
- **Evidence**: W `index.html` (linie 1152–1201) znajduje się 10 przycisków zakładek filtrujących po wykluczających się lub nakładających kryteriach.
- **Impact**: Zdezorientowanie użytkownika — nie wiadomo, czy kliknięcie `Superbet` wyklucza `Player Props`, czy filtruje tylko po bukmacherze.
- **Severity**: `P2` (Średni)
- **Recommended Direction**: Zastąpić chaotyczne zakładki dwoma głównymi trybami pracy: `Wszystkie okazje Value` i `Diagnostyka odrzuceń`, a kryteria bukmachera i ligi kontrolować w toolbarze.

### Finding F-08: Brak etykiet powiązanych z formularzami (`<label for="...">`)
- **Evidence**: W wielu formularzach tagi `<label>` nie posiadają atrybutu `for`, a kontrolki nie są powiązane semantycznie.
- **Impact**: Utrudniona obsługa czytnikami ekranu oraz brak możliwości kliknięcia w etykietę w celu aktywacji pola.
- **Severity**: `P3` (Niski)
- **Recommended Direction**: Dodać unikalne `id` i powiązania `for="..."` we wszystkich grupach formularzy.

### Finding F-09: Brak wskaźnika ładowania szkieletowego (`Skeleton Loading`)
- **Evidence**: Podczas ładowania danych z API tabela wyświetla statyczny tekst `Loading...` lub czyści zawartość.
- **Impact**: Wrażenie migotania i skoków układu (layout shift).
- **Severity**: `P3` (Niski)
- **Recommended Direction**: Wprowadzić prosty CSS skeleton placeholder (delikatnie pulsujące szare paski) na czas trwania zapytania `fetch()`.

### Finding F-10: Brak bezpośrednich akcji kopiowania i linkowania do bukmachera
- **Evidence**: W komórkach kursów bukmacherów (`Polish Bookmaker & Odds`) wyświetlana jest tylko liczba bez możliwości szybkiego skopiowania szczegółów zakładu czy kliknięcia w deep-link.
- **Impact**: Spowolnienie procesu zawierania zakładu przez użytkownika.
- **Severity**: `P3` (Niski)
- **Recommended Direction**: Dodać małą ikonę kopiowania / linku zewnętrznego przy najlepszym kursie w wierszu lub w panelu inspekcji.

---

## 9. Responsive & Accessibility Findings

### 9.1. Responsywność (Breakpoints & Viewports)
- **Desktop szeroki (> 1440px)**: Działa poprawnie, choć panel boczny na `/playerprops` niepotrzebnie ściska tabelę główną.
- **Laptop standard (1024px – 1366px)**:
  - Siatka filtrów (12 kontrolek) łamie się na 3-4 nierówne wiersze.
  - Tabela `/playerprops` wymaga przewijania poziomego przy otwartym panelu inspekcji.
- **Tablet / Mały ekran (768px – 1023px)**:
  - Sidebar automatycznie zwija się do szerokości 70px (ikony), co jest poprawnym zachowaniem.
  - Górny pasek nagłówka (`top-header`) ulega stłoczeniu (wyszukiwarka + latency pill + przełączniki nakładają się na siebie).
- **Mobile (< 768px)**:
  - Tabele 9-kolumnowe stają się bardzo trudne w obsłudze na ekranach dotykowych bez dedykowanego widoku kafelkowego (Card View).

### 9.2. Dostępność (Accessibility / WCAG 2.1)
- **Kontrast tekstu**: W trybie ciemnym kontrast jest wzorowy (> 7:1 dla tekstu podstawowego). W trybie jasnym etykiety statusowe i ostrzeżenia mają zbyt niski kontrast.
- **Nawigacja klawiaturą**: Brak wyraźnego obrysu `focus-visible` na wierszach tabeli i kafelkach zdarzeń.
- **ARIA**: Zakładki (`.market-tab-btn`, `.scope-btn`) wymagają uzupełnienia o `role="tab"` i `aria-selected="true/false"`.

---

## 10. Empty / Loading / Error States

Platforma posiada zaimplementowane podstawowe stany, ale wymagają one uporządkowania:

| Stan systemu | Obecna prezentacja | Diagnoza i Rekomendacja |
|---|---|---|
| **`NOT_RUN` (Świeży start)** | Ikona lupy/playera + „No props scanned yet” | **Poprawne**: Jasny komunikat z zachętą do kliknięcia przycisku „Scan Props”. |
| **`QUALIFIED = 0` (Brak valuebetów, ale są kandydaci)** | Mieszany komunikat PL/EN informujący o odrzuceniach lejka | **Wymaga poprawy**: Zamiast ściany tekstu, zwięzły komunikat: *„Brak zakładów spełniających kryteria Net EV $\ge$ 3.0% w tym oknie. Przeanalizowano N kandydatów.”* + 1 przycisk: *„Pokaż wszystkich kandydatów (Diagnostyka)”*. |
| **`LOADING` (Trwa skanowanie)** | Tekst przycisku zmienia się na „Scanning...”, tabela pozostaje pusta | **Wymaga poprawy**: Dodać pasek postępu lub pulsujący szkielet tabeli (Skeleton). |
| **`NO_POLISH_ODDS`** | Szary badge „No Polish Odds” w komórce | **Poprawne**: Informuje o braku kursów u polskich bukmacherów bez blokowania widoku. |
| **`REFERENCE_GAP`** | Etykieta „Reference Gap / Fair: N/A” | **Poprawne**: Jasno wskazuje brak zagranicznego konsensusu. |
| **`API_ERROR` / Błąd sieci** | Czerwony alert banner z treścią błędu | **Poprawne**: Bezpieczny, sanityzowany komunikat bez wycieku tracebacków. |

---

## 11. Top 10 Problems

Poniższa lista przedstawia 10 najważniejszych problemów całego frontendu, uszeregowanych według wpływu na użytkownika i szybkości pracy:

1. **[P1] Suboptymalna hierarchia skanowania na `/playerprops`** (Wskaźnik Net EV i status Value Bet ukryte w 7. i 9. kolumnie zamiast na początku osi skanowania).
2. **[P1] Ściskanie i deformacja tabeli głównej przez panel boczny inspekcji** (Brak niezależnego draweru/sheetu dla detali).
3. **[P1] Zdominowanie widoków produkcyjnych przez surowe dane diagnostyczne** (Kody odrzuceń pipeline'u i tabele parsowania na pierwszym planie).
4. **[P2] Przeładowany i nieresponsywny formularz filtrów na `/playerprops`** (12 kontrolek w jednym bloku bez podziału na filtry podstawowe i zaawansowane).
5. **[P2] Niespójność kontrastu i tokenów kolorystycznych w trybie jasnym (Light Theme)** (Zahardkodowane kolory `rgba` w szablonach JS).
6. **[P2] Brak lepkich nagłówków (`sticky headers`) w tabelach danych** (Utrata kontekstu kolumn przy przewijaniu).
7. **[P2] Niejednolita nawigacja szczegółowa między widokami** (Sub-view w Explorerze vs Panel boczny w Props vs 2-kolumny w Events).
8. **[P2] Niespójne, dwujęzyczne komunikaty w stanach pustych i zerowych** (Mieszanie języka polskiego i angielskiego w jednym bloku).
9. **[P3] Brak wskaźników Skeleton Loading podczas odświeżania danych** (Migotanie tabeli przy przełączaniu filtrów).
10. **[P3] Braki w semantyce dostępności ARIA i obsłudze nawigacji klawiaturą** (Brak powiązań etykiet i ról zakładek).

---

## 12. Top 3 Priorities

Trzy najważniejsze obszary o najwyższym zwrocie z inwestycji (High Impact / Low Risk), od których należy rozpocząć prace w kolejnym etapie:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           TOP 3 PRIORITIES                              │
├─────────────────────────────────────────────────────────────────────────┤
│ 1. REORGANIZACJA HIERARCHII WIERSZA TABELI NA /playerprops              │
│    Wprowadzenie układu: [Value Bet + Net EV] → [Zawodnik/Rynek/Linia] → │
│    [Kurs PL vs Fair Odds] → [Trend L10] → [Akcja].                      │
├─────────────────────────────────────────────────────────────────────────┤
│ 2. BEZPIECZNY INSPECTOR SZCZEGÓŁÓW (SLIDE-OVER DRAWER)                  │
│    Zastąpienie ściskania tabeli wysuwanym panelem bocznym (Drawer),     │
│    który zachowuje 100% czytelności tabeli głównej.                     │
├─────────────────────────────────────────────────────────────────────────┤
│ 3. ROZDZIELENIE WIDOKU PRODUKCYJNEGO OD DIAGNOSTYKI                    │
│    Uproszczenie zakładek na /playerprops do 2 trybów:                   │
│    "Value Bets" i "Diagnostyka & Pipeline", z czystym Empty State.      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 13. Incremental Improvement Plan

Plan został podzielony na **4 małe, bezpieczne i odwracalne fazy**, które nie wymagają przebudowy architektury ani zmian w backendzie.

```
Phase 1: Quick Wins & Visual Foundations (Shared UI)
  │
  ▼
Phase 2: /playerprops Information Hierarchy & Opportunity Presentation
  │
  ▼
Phase 3: Inspector Drawer & Filter Refinement
  │
  ▼
Phase 4: Responsive, Accessibility & States Polish
```

### Faza 1: Szybkie usprawnienia wizualne i tokeny (Shared UI)
- **Cel**: Podniesienie czytelności wszystkich tabel i usunięcie błędów kontrastu w motywach.
- **Zakres zmian**:
  - `styles.css`: Dodanie `position: sticky` do `thead` wszystkich `.data-table`.
  - `styles.css`: Zdefiniowanie scentralizowanych tokenów kolorystycznych statusów dla motywu ciemnego i jasnego.
  - `styles.css`: Dodanie `font-variant-numeric: tabular-nums` do klas `.mono`, `.prop-odds-value`, `.net-ev-pill`.
- **Typ zmiany**: Współdzielona (Shared CSS).
- **Oczekiwany rezultat**: Wyraźniejszy tekst, brak skakania cyfr, pełna czytelność w trybie jasnym.
- **Priorytet**: `P1`.

### Faza 2: Poprawa prezentacji okazji i hierarchii na `/playerprops`
- **Cel**: Umożliwienie natychmiastowego znajdowania najlepszych valuebetów.
- **Zakres zmian**:
  - `web/app.js` (`renderPropsTable`): Przebudowa struktury kolumn tabeli w stronę hierarchii zorientowanej na decyzję.
  - `web/app.js`: Wyróżnienie wartości `Net EV (%)` i etykiety `VALUE BET` na początku wiersza.
  - `web/index.html`: Uporządkowanie zakładek kategorii do 2 głównych trybów (`Value Bets` vs `All Candidates / Diagnostics`).
- **Typ zmiany**: Lokalna (`/playerprops`).
- **Oczekiwany rezultat**: Czas oceny okazji przez użytkownika skrócony o > 60%.
- **Priorytet**: `P1` (Top 1).

### Faza 3: Usprawnienie panelu inspekcji i filtrów
- **Cel**: Wyeliminowanie ściskania tabeli i zmniejszenie wysokości formularza filtrów.
- **Zakres zmian**:
  - `styles.css` & `web/app.js`: Implementacja panelu szczegółów jako wysuwanego draweru (Slide-over overlay) zamiast podziału flexbox.
  - `web/index.html` & `web/app.js`: Zgrupowanie 12 filtrów w pasek główny + rozwijany panel „Więcej filtrów”.
  - `web/app.js`: Ujednolicenie komunikatów w stanie pustym (jasny, profesjonalny komunikat z przyciskiem przejścia do diagnostyki).
- **Typ zmiany**: Lokalna (`/playerprops`) z możliwością ponownego użycia w Explorerze.
- **Oczekiwany rezultat**: Pełna czytelność tabeli nawet przy otwartym panelu inspekcji, zyskanie 200 px przestrzeni pionowej.
- **Priorytet**: `P1` (Top 2).

### Faza 4: Dostępność, stany ładowania i szlify responsywne
- **Cel**: Perfekcyjne dopracowanie UX, obsługa małych ekranów i standardy dostępności.
- **Zakres zmian**:
  - `web/index.html` & `web/app.js`: Dodanie powiązań formularzy `<label for="...">`, ról `role="tab"` i obsługi klawiszy `Enter`/`Space`.
  - `styles.css`: Dodanie prostego skeleton loadera na czas trwania zapytań API.
  - `styles.css`: Dostosowanie nagłówka i kart na tabletach i małych ekranach.
- **Typ zmiany**: Współdzielona (Shared UI).
- **Oczekiwany rezultat**: Zgodność z wytycznymi WCAG 2.1 AA, płynne przejścia bez migotania.
- **Priorytet**: `P2`/`P3`.

---

## 14. Candidate Areas for External UI References

W kolejnym etapie (przed implementacją) warto będzie dobrać zewnętrzne referencje wzornicze wyłącznie dla następujących konkretnych obszarów problemowych:

1. **Obszar: Modern Sports/Financial Analytics Data Table**
   - *Problem*: Obecna tabela na `/playerprops` jest przeładowana i ma zaburzoną hierarchię.
   - *Cel referencji*: Zbadanie wzorców prezentacji wielowymiarowych danych (kursy, prawdopodobieństwo, zysk) w nowoczesnych terminalach analitycznych (np. Bloomberg Terminal, OddsJam, Action Network, Prop-Odds).

2. **Obszar: Slide-over Detail Drawer / Sheet Component**
   - *Problem*: Obecny panel boczny deformuje geometrię tabeli.
   - *Cel referencji*: Zbadanie ergonomii bezkonfliktowego wysuwanego panelu bocznego z zachowaniem kontekstu wiersza tabeli.

3. **Obszar: Compact Multi-Dimensional Filter Bar**
   - *Problem*: 12 kontrolek filtrów zajmuje zbyt dużo miejsca.
   - *Cel referencji*: Zbadanie wzorców zwijanych pasków filtrów z licznikami aktywnych filtrów (np. Linear, Stripe Dashboard, GitHub Issues).

---

## 15. Targeted Test / Validation Plan

Zgodnie z zasadą: **TARGETED TEST / VALIDATION $\rightarrow$ IMPLEMENTATION $\rightarrow$ TARGETED VALIDATION**, poniżej zdefiniowano precyzyjne kryteria weryfikacji przyszłych zmian:

### 15.1. Zmiany wizualne i układu (Visual & Runtime Inspection)
Dla zmian czysto wizualnych (CSS, kolory, układ tabeli, drawer) nie tworzymy sztucznych testów jednostkowych. Walidacja odbywa się poprzez inspekcję w przeglądarce:
- `No automated test required; runtime/visual validation is sufficient.`
- **Kryteria inspekcji**:
  1. Sprawdzenie widoku `/playerprops` w motywie ciemnym i jasnym (kontrast WCAG $\ge$ 4.5:1).
  2. Otwarcie panelu szczegółów i weryfikacja, czy tabela główna nie ulega zniekształceniu.
  3. Sprawdzenie zachowania na viewportach: 1920px (Desktop), 1366px (Laptop), 1024px (Tablet), 375px (Mobile).

### 15.2. Przypadki wymagające weryfikacji zachowania (Behavior Validation)
Dla interakcji logicznych w `app.js` walidujemy konkretne zachowania w runtime:
1. **Filtrowanie i przełączanie zakładek na `/playerprops`**:
   - Przełączenie na zakładkę `All Candidates / Diagnostics` musi natychmiast wyrenderować listę wszystkich kandydatów (w tym odrzuconych).
   - Przełączenie z powrotem na `Value Bets` musi filtrować listę tylko do pozycji z `is_valuebet === true` lub `status === 'QUALIFIED'`.
2. **Synchronizacja kalkulatora podatkowego**:
   - Zmiana stawki podatku w Settings (np. Betclic z 0% na 12%) musi natychmiast przeliczać efektywne kursy w tabeli.

---

## 16. Backend Issues Detected

Podczas audytu wykonano serię testów zapytań do działającego serwera FastAPI (`http://127.0.0.1:8000`).

### Wyniki weryfikacji backendu:
- **Status ogólny**: `HEALTHY`.
- **Wszystkie endpointy odpowiadają poprawnie**:
  - `/api/v1/health` $\rightarrow$ `200 OK` (czas: ~0.7 ms)
  - `/api/v1/props/global-results` $\rightarrow$ `200 OK` (czas: ~0.08 ms)
  - `/api/v1/opportunities/explorer` $\rightarrow$ `200 OK` (czas: ~0.15 ms)
  - `/api/v1/events` $\rightarrow$ `200 OK` (czas: ~0.20 ms)
  - `/api/v1/providers` $\rightarrow$ `200 OK` (czas: ~0.10 ms)
- **Uwaga środowiskowa (Non-breaking)**:
  - Przy uruchamianiu serwera z linii poleceń wymagane jest ustawienie zmiennej `PYTHONPATH` na katalog główny projektu, aby uniknąć błędu `ModuleNotFoundError: No module named 'api'`.

**Brak krytycznych problemów po stronie backendu.** Logika API i bazy danych działa w pełni prawidłowo.

---

## 17. Explicit Non-Goals

Podczas planowania i przyszłej realizacji usprawnień kategorycznie wyklucza się:
1. **Przebudowę architektury frontendu**: Pozostajemy przy wydajnym, istniejącym stacku HTML/CSS/Vanilla JS SPA.
2. **Instalację ciężkich bibliotek zewnętrznych**: Brak instalacji Tailwind CLI, React, Next.js, Bootstrap czy obcych bibliotek komponentów bez uzasadnienia.
3. **Modyfikację backendu i modeli matematycznych**: Silniki ScannerEngine, SurebetDetectorEngine, TaxEngine, kalkulacja EV, scoring oraz API pozostają w 100% nienaruszone.
4. **Usuwanie funkcji diagnostycznych**: Żadna istniejąca funkcja telemetryczna nie zostanie usunięta — ulegnie jedynie lepszemu zorganizowaniu wizualnemu.
5. **Tworzenie sztucznych zestawów testowych**: Nie tworzymy pustych testów jednostkowych dla kodu CSS; stosujemy precyzyjną inspekcję runtime.

---

**Koniec dokumentu audytu.**

# Autonomous CFO (Pizzeria Amalfi) — Specyfikacja Techniczna i Produktowa (SSoT)

**Wydarzenie:** Collabothon 2026 — Challenge: *Autonomous CFO (Red Hat & Commerzbank)*  
**Wersja dokumentu:** 1.0 (Baseline)  
**Zespół:** 4 osoby  

---

## 1. Cel projektu i zgodność z wymaganiami Challenge Card

### 1.1. Kontekst biznesowy i persona
* **Klient:** Właściciel małego/średniego przedsiębiorstwa (*Pizzeria Amalfi*), który samodzielnie zarządza firmą i nie zatrudnia dedykowanego dyrektora finansowego (CFO).
* **Problem:** Ręczna analiza wyciągów bankowych jest czasochłonna i grozi przeoczeniem spadków płynności lub zamrożeniem nadwyżek gotówki na nieoprocentowanym rachunku.
* **Motyw przewodni (*Innovations for all generations*):** Interfejs i komunikaty agenta muszą być zrozumiałe dla użytkownika o dowolnym poziomie biegłości technicznej i finansowej — zero niezrozumiałego żargonu bankowego, pełna przejrzystość wyliczeń i kontrola nad każdą decyzją.

### 1.2. Wybrany przypadek użycia (Deep Vertical Slice)
Zespół realizuje pełną ścieżkę End-to-End dla **Liquidity & Cash Allocation Agenta** (łączącego monitorowanie płynności oraz alokację nadwyżek z Challenge Card):
1. **Scenariusz niedoboru płynności (Liquidity Protection):** Wykrycie zbliżającego się spadku salda poniżej bezpiecznego bufora i rekomendacja uruchomienia krótkoterminowej linii kredytowej (*Credit Facility*).
2. **Scenariusz nadpłynności (Yield Optimization):** Wykrycie bezczynnej gotówki powyżej bufora operacyjnego i rekomendacja lokaty terminowej (*Fixed-Term Deposit*) lub funduszu rynku pieniężnego (*Money Market Fund*).

### 1.3. Macierz autonomii i kontroli (wymóg regulaminowy)
System wyraźnie rozgranicza cztery poziomy działania:
* **Analiza niezależna (System / Agent):** Codzienne wyliczenie deterministycznych wskaźników KPI na podstawie wyciągu bankowego oraz identyfikacja ryzyk/szans przez model AI.
* **Rekomendacja z wyjaśnieniem (Agent AI):** Przygotowanie konkretnej propozycji działania wraz z opisem logiki biznesowej w prostym języku (*Co wykryto → Dlaczego to ważne → Proponowany krok*).
* **Decyzja wymagająca zgody (Użytkownik / HITL):** Żadna operacja nie dochodzi do skutku bez jawnego kliknięcia **`APPROVED`** lub **`DECLINED`** przez właściciela firmy na UI.
* **Wykonanie symulowane (Baza danych):** Każda propozycja agenta oraz decyzja podjęta przez użytkownika (`PENDING` / `APPROVED` / `DECLINED`) jest trwale zapisywana w bazie PostgreSQL jako dziennik symulowanych operacji finansowych.

---

## 2. Architektura i przepływ danych (End-to-End Flow)

```text
[CSV Bank Statement] 
       │ (Jednorazowy / okresowy import)
       ▼
[PostgreSQL: bank_transactions]
       │
       ├──► (1) Daily Cron Job LUB (2) Przycisk "Run Daily Analysis" na UI (Demo)
       ▼
[Deterministic KPI Engine (Skrypty SQL/Python)]
       │ (Zapis migawki wskaźników)
       ▼
[PostgreSQL: kpi_snapshots] ──────────────────────────────► [Frontend: Dashboard KPI]
       │ (Przekazanie wyliczonych KPI jako kontekst)
       ▼
[AI Agent Layer (Open Weights LLM na Red Hat OpenShift AI)]
       │ (Ustrukturyzowana rekomendacja JSON)
       ▼
[PostgreSQL: agent_proposals (status: PENDING)] ─────────► [Frontend: Karty Decyzji]
                                                                  │
                                             (Użytkownik klika: APPROVED / DECLINED)
                                                                  ▼
                                               [PostgreSQL: agent_proposals (aktualizacja statusu)]
```

### Mechanizm wyzwalania (Harmonogram + Tryb Demo)
* **Tryb produkcyjny:** Zadanie uruchamiane raz dziennie przelicza KPI dla bieżącego dnia i wywołuje agenta AI.
* **Tryb prezentacji (Demo Trigger):** Endpoint `POST /api/jobs/run-daily-flow?as_of_date=YYYY-MM-DD` pozwala zasymulować przeliczenie na dowolny dzień z historycznego pliku CSV (np. czerwiec 2025 dla luki płynnościowej oraz grudzień 2025 dla nadpłynności).

---

## 3. Model danych (PostgreSQL)

### 3.1. Tabela `bank_transactions` (źródło: plik CSV)
Dostosowana bezpośrednio do formatu `pizzeria-amalfi-bank-statement-2025-v6.csv` (separator kolumn `;`, separator dziesiętny `,`, daty `DD.MM.YY`):

```sql
CREATE TABLE bank_transactions (
    id SERIAL PRIMARY KEY,
    account_number VARCHAR(34) NOT NULL,          -- np. DE89370400440532013000
    booking_date DATE NOT NULL,                   -- parsowane z DD.MM.YY
    value_date DATE NOT NULL,                     -- parsowane z DD.MM.YY
    transaction_type VARCHAR(100) NOT NULL,       -- np. CARD SETTLEMENT (DEBIT), SUPPLIER PAYMENT
    payment_reference TEXT,                       -- opis transakcji (zawiera m.in. kursy CHF)
    beneficiary_payer VARCHAR(255),
    counterparty_account VARCHAR(64),             -- NULLABLE (puste dla części transakcji Giro)
    bank_code VARCHAR(32),                        -- NULLABLE
    amount NUMERIC(12, 2) NOT NULL,               -- parsowane z przecinka dziesiętnego
    currency CHAR(3) NOT NULL,                    -- EUR lub CHF
    info VARCHAR(100),
    row_hash VARCHAR(64) UNIQUE NOT NULL,         -- SHA-256 wiersza zapobiegający duplikatom
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2. Tabela `kpi_snapshots` (wynik deterministycznych skryptów)
Przechowuje dzienny stan wszystkich wyliczonych KPI w formacie JSONB, aby zachować historię pod wykresy na dashboardzie oraz zapewnić powtarzalny wsad dla agenta AI.

```sql
CREATE TABLE kpi_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_date DATE UNIQUE NOT NULL,
    metrics JSONB NOT NULL,                       -- ustrukturyzowane wartości KPI (kontrakt w sekcji 4)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.3. Tabela `agent_proposals` (propozycje i symulowane wykonanie)
Centralna tabela przechowująca zarówno propozycje wygenerowane przez agenta, jak i wynik decyzji użytkownika (symulowane wykonanie).

```sql
CREATE TABLE agent_proposals (
    id SERIAL PRIMARY KEY,
    snapshot_id INTEGER REFERENCES kpi_snapshots(id) ON DELETE CASCADE,
    as_of_date DATE NOT NULL,
    agent_type VARCHAR(50) NOT NULL DEFAULT 'LIQUIDITY_AGENT',
    scenario_type VARCHAR(50) NOT NULL,           -- 'LIQUIDITY_SHORTAGE' | 'EXCESS_LIQUIDITY' | 'OPTIMAL'
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',-- 'PENDING' | 'APPROVED' | 'DECLINED'
    title VARCHAR(255) NOT NULL,
    plain_language_summary TEXT NOT NULL,         -- wyjaśnienie dla właściciela firmy
    reasoning_details JSONB NOT NULL,             -- lista użytych KPI i reguł decyzyjnych
    recommended_action JSONB NOT NULL,            -- typ instrumentu, kwota, waluta, okres, szacowany zysk/koszt
    user_note TEXT,                               -- opcjonalny komentarz przy zatwierdzeniu/odrzuceniu
    decided_at TIMESTAMP,                         -- czas podjęcia decyzji przez użytkownika
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. Deterministyczny Silnik KPI (Kontrakt Integracyjny)

> **Metodologia:** pełne definicje, wzory, założenia, progi statusów i obsługa braków danych dla 8 KPI są w pliku **[`KPI_SPEC.md`](KPI_SPEC.md)** (SSoT dla silnika KPI).
> **Implementacja:** `backend/kpi_engine/` — punkt wejścia `run_kpi_engine(as_of_date)` / `python -m kpi_engine --as-of-date YYYY-MM-DD` (instrukcja: `backend/kpi_engine/README.md`).

| # | KPI | Klucz(e) w `kpis` |
|---|---|---|
| 1 | Saldo bieżące | `current_cash_balance_eur` |
| 2 | Prognoza salda 7/14/30 dni | `forecast_balance_7d_eur`, `forecast_balance_14d_eur`, `forecast_balance_30d_eur`, `forecast_min_balance_30d_eur` |
| 3 | Minimalny bufor gotówki | `minimum_cash_buffer_eur` |
| 4 | Luka płynności | `liquidity_gap_30d_eur`, `projected_overdraft_30d_eur` |
| 5 | Nadwyżka do ulokowania | `deployable_cash_surplus_eur` |
| 6 | Przepływ netto 7/30 dni | `net_cash_flow_7d_eur`, `net_cash_flow_30d_eur` |
| 7 | Trend wpływów / wydatków | `cash_inflow_trend_pct`, `cash_outflow_trend_pct` |
| 8 | Pokrycie kosztów stałych | `fixed_cost_coverage_ratio` |

Dodatkowe tabele referencyjne (addytywne, `db/schema.sql` + `db/kpi_reference_data.sql`): `fixed_obligations` (harmonogram stałych zobowiązań z `Knowledge/`) oraz `account_balance_anchors` (udokumentowane salda — wyciąg nie zawiera salda).

### Kontrakt wyjściowy `metrics` (JSONB)
Skrypt przeliczający zapisuje (upsert po `snapshot_date`) wynik do `kpi_snapshots.metrics` w poniższej strukturze, z której korzystają Backend, Frontend i Agent AI (pełny opis: `KPI_SPEC.md` §3):

```json
{
  "schema_version": "1.0",
  "as_of_date": "2025-12-31",
  "currency": "EUR",
  "overall_status": "HEALTHY",
  "kpis": {
    "current_cash_balance_eur": 33266.35,
    "forecast_balance_7d_eur": 36217.57,
    "forecast_balance_14d_eur": 41438.79,
    "forecast_balance_30d_eur": 45620.15,
    "forecast_min_balance_30d_eur": 30430.81,
    "minimum_cash_buffer_eur": 22369.46,
    "liquidity_gap_30d_eur": 0.00,
    "projected_overdraft_30d_eur": 0.00,
    "deployable_cash_surplus_eur": 8061.35,
    "net_cash_flow_7d_eur": -4603.34,
    "net_cash_flow_30d_eur": 9386.83,
    "cash_inflow_trend_pct": -6.5,
    "cash_outflow_trend_pct": -6.9,
    "fixed_cost_coverage_ratio": 1.95
  },
  "kpi_metadata": [
    {
      "id": "current_cash_balance_eur",
      "kpi_number": 1,
      "label": "Saldo bieżące (EUR)",
      "label_en": "Current cash balance",
      "value": 33266.35,
      "unit": "EUR",
      "status": "HEALTHY", // "HEALTHY" | "WARNING" | "CRITICAL"
      "is_estimate": false,
      "details": { }
    }
  ],
  "forecast": { "daily": [ ], "sensitivity": { } },
  "signals": { "scenario_hint": "OPTIMAL", "liquidity_shortage": { }, "excess_liquidity": { } },
  "data_quality": { "status": "OK", "flags": [ ] },
  "engine": { "name": "amalfi-kpi-engine", "version": "1.0.0" }
}
```

---

## 5. Specyfikacja Agenta AI (Liquidity & Cash Allocation Agent)

### 5.1. Zasady nadrzędne (Guardrails)
1. **Zakaz halucynacji matematycznych:** Agent AI **nie otrzymuje** surowego pliku CSV i nie wykonuje samodzielnych sumowań na transakcjach. Pracuje wyłącznie na deterministycznych liczbach z `kpi_snapshots`.
2. **Wyjaśnialność:** Każda rekomendacja musi wskazywać konkretne wartości KPI, które uruchomiły alert, oraz tłumaczyć konsekwencje finansowe prostym językiem.
3. **Structured Output:** Agent musi zwracać odpowiedź zgodną ze schematem JSON poniżej (wymuszone przez prompt / tool calling / parser po stronie backendu).

### 5.2. Logika decyzyjna agenta
* **Warunek A — Ryzyko utraty płynności (`LIQUIDITY_SHORTAGE`):**
  * *Trigger:* Prognozowane saldo 30-dniowe spada poniżej bezpiecznego bufora (`liquidity_gap_30d_eur > 0`, `signals.liquidity_shortage.detected`) lub `fixed_cost_coverage_ratio < 1.0` (saldo poniżej miesięcznej sumy zobowiązań stałych — jak w danych z czerwca 2025).
  * *Akcja:* Propozycja uruchomienia krótkoterminowej linii kredytowej (*Commerzbank Business Credit Line*) na kwotę pokrywającą deficyt + bufor bezpieczeństwa.
* **Warunek B — Nadpłynność (`EXCESS_LIQUIDITY`):**
  * *Trigger:* Wolne środki powyżej bufora bezpieczeństwa (`deployable_cash_surplus_eur > 10 000 EUR`, `signals.excess_liquidity.detected`). Uwaga: na danych 2025 nadwyżka na 2025-12-31 wynosi 8 061,35 EUR (maks. w roku 9 443,26 EUR) — szczegóły i konsekwencje dla demo w `KPI_SPEC.md` §5.2.
  * *Akcja:* Propozycja alokacji 70% nadwyżki na 90-dniową lokatę terminową (*Fixed-Term Deposit*) lub fundusz rynku pieniężnego (*Money Market Fund*) z wyliczeniem szacowanego zysku odsetkowego.
* **Warunek C — Brak konieczności działania (`OPTIMAL`):**
  * Gdy wskaźniki mieszczą się w bezpiecznych widełkach, agent nie tworzy zbędnych alertów (lub raportuje status stabilny).

### 5.3. Kontrakt wyjściowy Agenta (JSON Schema)
```json
{
  "should_create_proposal": true,
  "scenario_type": "EXCESS_LIQUIDITY",
  "title": "Ulokuj 25 000 EUR nadwyżki na 90-dniowej lokacie terminowej",
  "plain_language_summary": "Twoje saldo operacyjne wynosi 82 945,55 EUR, co pokrywa ponad 4 miesiące stałych kosztów pizzerii. Przeniesienie części wolnych środków na 3-miesięczną lokatę pozwoli wypracować ok. 218 EUR bezpiecznego zysku bez naruszania poduszki finansowej.",
  "reasoning_details": {
    "triggered_kpis": [
      { "name": "current_cash_balance_eur", "value": 82945.55, "threshold": "> 45000.00" },
      { "name": "fixed_cost_coverage_ratio", "value": 4.6, "threshold": "> 2.0" }
    ],
    "safety_buffer_kept_eur": 57945.55
  },
  "recommended_action": {
    "instrument": "FIXED_TERM_DEPOSIT_90D",
    "provider": "Commerzbank",
    "amount": 25000.00,
    "currency": "EUR",
    "duration_days": 90,
    "estimated_annual_yield_pct": 3.5,
    "estimated_return_eur": 218.75,
    "requires_user_approval": true,
    "execution_mode": "SIMULATED_DB_RECORD"
  }
}
```

---

## 6. Kontrakt REST API (Backend ↔ Frontend)

| Metoda | Endpoint | Opis | Payload / Response |
|---|---|---|---|
| `GET` | `/api/dashboard` | Pobiera najnowsze (lub dla `?date=`) KPI, historię salda oraz listę propozycji agenta | `{ snapshot, history, pending_proposals, decision_history }` |
| `POST` | `/api/proposals/{id}/decision` | Zapisuje decyzję użytkownika w bazie (`APPROVED` lub `DECLINED`) | Request: `{ "decision": "APPROVED" \| "DECLINED", "user_note": "..." }` |
| `POST` | `/api/jobs/run-daily-flow` | Ręcznie uruchamia dzienny flow (KPI + Agent AI) dla podanej daty (do demo) | Query: `?as_of_date=2025-06-30` → `{ snapshot_id, proposal_created }` |
| `POST` | `/api/transactions/import` | Importuje plik CSV wyciągu bankowego do PostgreSQL | `multipart/form-data` (`file`) → `{ imported_rows, skipped_duplicates }` |

---

## 7. Wymagania Frontend (UI/UX)

1. **Nagłówek z kontekstem symulacji:** Wyświetla aktualną datę analizy (np. `2025-12-31`) oraz przycisk **`Symuluj kolejny dzień / Przelicz KPI`** (wywołujący `POST /api/jobs/run-daily-flow`), umożliwiający płynną prezentację przed jury.
2. **Sekcja 1 — Dashboard KPI:** Czytelne kafelki ze wszystkimi wyliczonymi KPI (wartość, jednostka, status kolorystyczny) oraz wykres trendu salda/przepływów w czasie.
3. **Sekcja 2 — Aktywne Rekomendacje CFO (`status = PENDING`):**
   * Wyraźna karta decyzji z tytułem, proponowaną kwotą i instrumentem.
   * Rozwijana lub czytelnie wydzielona sekcja **„Dlaczego agent to proponuje?”** (wyświetlająca `plain_language_summary` oraz `reasoning_details`).
   * Dwa wyraźne przyciski akcji: **`Zatwierdź (Approve)`** oraz **`Odrzuć (Decline)`**.
4. **Sekcja 3 — Dziennik Decyzji i Symulowanych Akcji (`APPROVED` / `DECLINED`):**
   * Tabela/lista wszystkich historycznych propozycji agenta z widocznym statusem podjętym przez użytkownika, kwotą oraz znacznikiem czasu decyzji.

---

## 8. Infrastruktura i wymagania Red Hat OpenShift AI

### 8.1. Wymagania obowiązkowe (Must-Have)
* **Hosting na Red Hat OpenShift AI:** Cała aplikacja (Frontend, Backend, baza PostgreSQL, serwis Agentów oraz serwer modelu LLM) musi być wdrożona i uruchomiona na przydzielonym klastrze OpenShift AI.
* **Model Open Weights:** Wykorzystanie otwartego modelu (np. **Granite**, **Mistral** lub **Qwen**) serwowanego lokalnie na klastrze GPU OpenShift AI.
* **Repozytorium Git:** Kod źródłowy wraz z niniejszym plikiem `SPEC.md` i instrukcją uruchomienia udostępniony organizatorom.

### 8.2. Elementy opcjonalne (Bonus Points z Challenge Card)
* Serwowanie modelu przez **vLLM** oraz dostęp przez **OpenShift AI Model-as-a-Service (MaaS)**.
* Śledzenie i ewaluacja wywołań agenta w **MLFlow**.
* Zabezpieczenie odpowiedzi agenta za pomocą **NeMo Guardrails** lub **OpenShell**.

---

## 9. Podział ról w zespole i Definition of Done (DoD)

* **Osoba 1 (Data & KPI Engine):** Importer CSV → PostgreSQL + implementacja deterministycznych reguł KPI zapisujących wynik do `kpi_snapshots`.
* **Osoba 2 (AI & OpenShift Engineer):** Uruchomienie modelu Open Weights na OpenShift AI + implementacja serwisu Agenta AI (prompt, guardrails, walidacja JSON).
* **Osoba 3 (Backend & Orchestration):** API REST (FastAPI / Node / Quarkus), obsługa flow dziennego oraz zapis decyzji `APPROVED` / `DECLINED` w `agent_proposals`.
* **Osoba 4 (Frontend & UX):** Dashboard KPI, karty rekomendacji z wyjaśnieniem biznesowym, obsługa przycisków `Approve`/`Decline` i historia decyzji.

### Definition of Done (MVP na prezentację):
- [ ] Dane z `pizzeria-amalfi-bank-statement-2025-v6.csv` załadowane do PostgreSQL.
- [ ] Przeliczenie KPI i wywołanie agenta dla daty **2025-06-30** generuje propozycję linii kredytowej (`PENDING`).
- [ ] Przeliczenie KPI i wywołanie agenta dla daty **2025-12-31** generuje propozycję lokaty/inwestycji (`PENDING`).
- [ ] Kliknięcie **Approve** lub **Decline** na UI aktualizuje rekord w bazie i przenosi go do historii decyzji.
- [ ] Całość działa stabilnie na klastrze Red Hat OpenShift AI.
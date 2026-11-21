# Autonomous CFO – Pizzeria Amalfi

### Agent płynności finansowej i doradcy CFO (podatki, CHF, lokaty) · Collabothon 2026 · Red Hat / Commerzbank Challenge

---

## 0. TL;DR (przeczytaj najpierw)

Budujemy **asystenta finansowego dla właściciela małej firmy** (pizzeria).
Agent:

1. czyta wyciąg z konta z bazy (tabela `bank_transactions`) **do wybranego dnia „dziś"**,
2. przyjmuje **plany właściciela opisane w czacie językiem naturalnym** (urlop, zakup pieca, …), a AI rozpoznaje typ zdarzenia i jego parametry,
3. **liczy prognozę salda na 60 dni** (zwykły kod, bez AI),
4. **wykrywa ryzyko** (saldo spadnie pod zero lub określony limit),
5. **symuluje opcje ratunku** (np. linia kredytowa, przesunięcie przelewu do Zurychu itp.),
6. **LLM tłumaczy** to prostym językiem i wskazuje najlepszą opcję,
7. właściciel **zatwierdza lub odrzuca**, wykonanie jest **symulowane**, decyzja jest zapisana w bazie danych,
8. prognoza przelicza się, a wykres pokazuje efekt (czerwona linia → zielona),
9. gdy płynność jest bezpieczna, **trzej doradcy (podatkowy, FX, inwestycyjny)** proponują kolejne kroki (rezerwa na podatek, zabezpieczenie rat w CHF, lokata), a **agent nadrzędny** pokazuje tylko takie propozycje, które można zatwierdzić w dowolnej kombinacji (sekcja 18).

**Zasada nr 1:** liczby liczy kod (deterministycznie). LLM rozpoznaje intencję użytkownika, tłumaczy i rekomenduje spośród opcji i propozycji, które policzył kod.

---

## 1. Problem i scenariusz demo

- Właściciel: Vincenzo Amalfi, pizzeria we Frankfurcie, sam zarządza finansami.
- Dane: wyciąg z konta za 2025 (1 125 transakcji).
- Ukryty problem w danych:
  - stałe koszty ok. **21 000 €/mies.** (czynsz, pensje, składki, podatki, media, przelew do brata w Zurychu),
  - **zamknięcie na 3 tygodnie w czerwcu**, gdy przychód = 0, a koszty idą dalej,
  - saldo spada do **−22 097,60 € (3 lipca)**,
  - do końca roku odbija do **+33 266,35 €**.

**Demo:** ustawiamy „dziś" = **10 maja 2025** (saldo 3 205,69 €), właściciel pisze w czacie
„w czerwcu zamykamy na urlop od 1 do 21", a agent ostrzega przed dołkiem i proponuje zabezpieczenie, zanim do niego dojdzie.

> Dlaczego 10 maja: przed dołkiem wypadają wtedy jeszcze **dwa** przelewy do Zurychu
> (15.05 i 15.06), więc opcja „wstrzymaj przelewy" jest wyraźniejsza.

---

## 2. Mini słowniczek


| Pojęcie                             | Znaczenie                                                                                                                   |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Płynność                            | Czy masz pieniądze na rachunki **w danym dniu**. Firma może zarabiać i mimo to nie mieć gotówki.                            |
| Prognoza salda (cash flow forecast) | Przewidywany stan konta dzień po dniu.                                                                                      |
| Koszt stały                         | Płacony zawsze, niezależnie od sprzedaży (czynsz, pensje).                                                                  |
| Koszt zmienny                       | Zależy od sprzedaży (zakupy u dostawców).                                                                                   |
| Linia kredytowa                     | Umówiony z bankiem limit „na minusie", z którego korzysta się tylko w razie potrzeby. Odsetki tylko od wykorzystanej kwoty. |
| Bufor bezpieczeństwa                | Minimalne saldo, którego nie chcemy przekraczać (domyślnie 3 000 €).                                                        |
| Zdarzenie (plan)                    | Przyszły wydatek, wpływ lub zamknięcie lokalu opisane przez właściciela w czacie.                                           |
| Nadwyżka                            | Gotówka niepotrzebna w horyzoncie prognozy ani na płatności, ani na bufor (zaokrąglona w dół do 1 000 €).                   |
| Lokata terminowa                    | Pieniądze odłożone w banku na ustalony okres; wracają na konto z odsetkami w dniu zapadalności.                             |
| Kontrakt terminowy (forward)        | Umowa z bankiem, która już dziś ustala kurs wymiany dla przyszłych przelewów w obcej walucie.                               |
| OGX                                 | Serwer agentowy (dawniej Llama Stack) przed modelem na vLLM; obsługuje rozmowę i wywołania narzędzi MCP.                    |
| MCP                                 | Protokół, przez który model wywołuje „narzędzia" (np. pobierz KPI, zapisz zdarzenie) zamiast dostawać surowe dane.          |


---

## 3. Architektura

```
┌──────────────────────────────── OpenShift AI (namespace autonomous-cfo) ────────────────────────────────┐
│                                                                                                         │
│  ┌──────────────────────┐  /api/*  ┌───────────────────────────┐   SQL   ┌──────────────┐               │
│  │ POD: frontend        │ ───────▶ │ POD: backend (Python)     │ ──────▶ │  PostgreSQL  │◀──────┐       │
│  │ React + Vite (nginx) │          │ - REST API (FastAPI)      │         │  (istnieje)  │       │       │
│  │ - wykres, karty opcji│          │ - kategoryzacja           │         └──────────────┘       │ SQL   │
│  │ - czat z asystentem  │          │ - silnik KPI, prognoza    │                                │       │
│  │ - historia decyzji   │          │ - ryzyko, opcje, symulacje│  Responses API                 │       │
│  └──────────────────────┘          │ - orkiestracja czatu      │ ─────────────▶ ┌─────────────┐ │       │
│                                    └───────────────────────────┘                │ OGX server  │ │       │
│                                                                                 └─────────────┘ │       │
│                                                   ┌──────────────────────┐  MCP     │    │       │       │
│                                                   │ POD: serwer MCP      │◀─────────┘    │       │       │
│                                                   │ narzędzia KPI/danych │ ──────────────┼───────┘       │
│                                                   │ + zapis zdarzeń      │               ▼               │
│                                                   └──────────────────────┘     ┌──────────────────────┐  │
│                                                                                │ vLLM (model cfo-llm) │  │
│                                                                                └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Frontend (osobny pod):** React + Vite, zbudowany statycznie i serwowany przez nginx. Ruch `/api/`* nginx przekazuje do backendu.
- **Backend (osobny pod):** Python. Cała logika deterministyczna (kategoryzacja, KPI, prognoza, ryzyko, opcje) oraz orkiestracja rozmowy z modelem.
- **PostgreSQL:** już istnieje; transakcje są w `bank_transactions` (sekcja 5).
- **OGX:** jedyny punkt dostępu do modelu. Backend nie woła vLLM bezpośrednio; wysyła rozmowę do OGX (API Responses zgodne z OpenAI), a OGX przekazuje ją do vLLM i obsługuje wywołania narzędzi MCP.
- **Serwer MCP:** daje modelowi metody zwracające KPI i dane (szczegóły narzędzi zostaną dopisane później) oraz narzędzie do zapisu potwierdzonego zdarzenia z czatu (Moduł 3).
- **vLLM:** serwuje otwarty model (`cfo-llm`).

Obok środowiska `autonomous-cfo` istnieje osobny namespace deweloperski `autonomous-cfo-dev` z pełną kopią stosu (własny PostgreSQL, serwer MCP, OGX, backend i frontend); współdzielony jest tylko model (sekcja 16).

Konfiguracja backendu przez zmienne środowiskowe: połączenie z bazą (`PG*`), adres OGX, identyfikator modelu w OGX, adres serwera MCP.

### 3.1. Frontend i backend?

Rekomendacja (zgodna z tym, co jest już w repo):


| Warstwa  | Technologia                                                      | Uzasadnienie                                                                                  |
| -------- | ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Frontend | **React + Vite** (już jest w `frontend/`, obraz nginx)           | statyczny obraz, prosty czat, interaktywny wykres, pełna kontrola nad wyglądem kart decyzji   |
| Backend  | **Python + FastAPI** (obecny `backend/app.py` trzeba rozbudować) | silnik KPI jest już w Pythonie, FastAPI daje walidację JSON (Pydantic) i łatwy endpoint czatu |


Biblioteki dla frontendu: wykres np. `recharts` lub `chart.js`; czat jako zwykły komponent React
(lista wiadomości + pole tekstowe), bez dodatkowych frameworków.

---

## 4. Dane wejściowe

Wyciąg **jest już załadowany** do tabeli `bank_transactions`
(źródło: `Challenge/pizzeria-amalfi-bank-statement-2025-v6.csv`, import: `db/import_transactions.sql`).
Aplikacja **nie importuje CSV** – tylko czyta z bazy.


| Cecha  | Wartość                                                |
| ------ | ------------------------------------------------------ |
| Data   | używamy `booking_date`                                 |
| Kwota  | `amount`, minus = wydatek                              |
| Waluta | prawie wszystko `EUR`, **przelewy do Zurychu w** `CHF` |
| Saldo  | **brak kolumny salda**                                 |


**Dwie ważne pułapki:**

1. **CHF → EUR.** Kurs jest w opisie przelewu (`payment_reference`), np.
  `Silent Partnership Capital Tranche 12/12 (4166.67 CHF @ 1.047 EUR/CHF)`.
   Wyciągamy go regexem `@ ([\d.]+) EUR/CHF` i mnożymy: `kwota_eur = kwota_chf * kurs`.
2. **Saldo początkowe** na 01.01.2025 = **2 500,00 €** (jest już w bazie jako punkt odniesienia salda).
  Saldo w dniu X = 2 500 + suma wszystkich transakcji (w EUR) do dnia X włącznie.

---

## 5. Model danych (wymagania logiczne)

Baza i tabele już istnieją, a ich nazwy i struktura mogą się różnić od tego opisu.
Poniżej jest lista danych, które aplikacja **musi** móc odczytać lub zapisać, a nie gotowy DDL.

**1. Transakcje (odczyt)** – tabela `bank_transactions`, używane pola:
`booking_date`, `transaction_type`, `payment_reference`, `beneficiary_payer`, `amount`, `currency`.
Kategorii **nie zapisujemy** w tej tabeli; liczymy ją w kodzie przy odczycie (Moduł 1).

**2. Dane referencyjne (odczyt)** – już istnieją w bazie: saldo początkowe / znane salda
oraz harmonogram stałych zobowiązań.

**3. Zdarzenia / plany (odczyt i zapis)** – zapisywane po potwierdzeniu w czacie (Moduł 3)
oraz po zatwierdzeniu przez właściciela opcji ratunku (Moduł 5) albo propozycji doradców CFO (sekcja 18):


| Dane                          | Opis                                                                    |
| ----------------------------- | ----------------------------------------------------------------------- |
| identyfikator                 | unikalny                                                                |
| typ zdarzenia                 | jedna z wartości z Modułu 3 (zamknięta lista)                           |
| data od / data do             | dla jednodniowych data do = data od                                     |
| kwota (EUR)                   | puste dla zamknięcia; lokata: kapitał; zakup CHF: zapłacona kwota w EUR |
| kategoria                     | wstrzymanie: kategoria płatności (np. `investment_outflow`); lokata: `investment` albo `tax_reserve`; poza tym puste |
| opis                          | krótki opis rozpoznany przez AI                                         |
| oryginalna wypowiedź          | tekst użytkownika z czatu, z którego powstało zdarzenie (ślad audytowy) |
| źródło                        | `czat` (użytkownik) albo `agent` (zatwierdzona opcja lub propozycja doradcy) |
| data „dziś" w momencie zapisu | `as_of_date` symulacji                                                  |
| czas utworzenia               |                                                                         |


**4. Decyzje właściciela (zapis, ślad audytowy):**
„dziś" symulacji, identyfikator i opis opcji ratunku (`A`–`E`) albo propozycji doradcy (`T1`, `T2`, `F1`, `F2`, `I1`),
status (`zatwierdzona` / `odrzucona`), wykonanie (`SYMULOWANE`), wyjaśnienie od LLM, migawka prognozy w momencie decyzji,
czas decyzji. Tabela decyzji jest jedna (`owner_decisions`); zatwierdzenie kilku propozycji naraz zapisuje po jednym
wierszu na propozycję w jednej transakcji.

**Czego NIE trzymamy w bazie:** prognozy, średnich i kategorii transakcji. Liczymy je na bieżąco (milisekundy) przy każdym odświeżeniu.

---

## 6. Moduł 1 – Kategoryzacja (`categories.py`)

**Zasada:** kategorię wyznacza przede wszystkim `transaction_type`. Dodatkowe dopasowanie
(po fragmencie tekstu w `beneficiary_payer` lub `payment_reference`, bez rozróżniania wielkości liter)
stosujemy **tylko** dla dwóch typów, które mieszają różne koszty: `SALARY PAYMENT` i `DIRECT DEBIT`.

Klucze kategorii są takie same jak w istniejących danych referencyjnych (harmonogram stałych zobowiązań),
żeby kod, serwer MCP i baza używały tych samych nazw.

### 6.1. Pełna lista kategorii (na podstawie analizy CSV 2025)


| `transaction_type`            | Dodatkowe dopasowanie                         | Kategoria             | Etykieta (UI)                                 | Rodzaj      | Cykl / termin               | Przesuwalne? | Liczba / suma 2025  |
| ----------------------------- | --------------------------------------------- | --------------------- | --------------------------------------------- | ----------- | --------------------------- | ------------ | ------------------- |
| `CARD SETTLEMENT (DEBIT)`     | –                                             | `card_revenue_debit`  | Przychód – karty debetowe                     | zmienny (+) | prawie codziennie           | –            | 335 / +109 105,08 € |
| `CREDIT CARD PAYOUT`          | –                                             | `card_revenue_credit` | Przychód – karty kredytowe                    | zmienny (+) | kilka razy w tygodniu       | –            | 143 / +435 319,19 € |
| `SUPPLIER PAYMENT`            | –                                             | `supplier_invoices`   | Dostawcy (Asimex, Fattoria La Vino)           | zmienny (−) | wg faktur                   | –            | 239 / −180 193,06 € |
| `GIRO CARD PAYMENT`           | –                                             | `fresh_produce`       | Zakupy na targu (Central Wholesale)           | zmienny (−) | prawie codziennie           | –            | 292 / −76 825,66 €  |
| `RENT PAYMENT`                | –                                             | `rent`                | Czynsz                                        | stały       | miesięczny, 1. dnia         | nie          | 12 / −45 600,00 €   |
| `TAX OFFICE TRANSFER`         | –                                             | `payroll_tax`         | Podatek od wynagrodzeń                        | stały       | miesięczny, 10. dnia        | nie          | 12 / −10 560,00 €   |
| `INTERNATIONAL WIRE TRANSFER` | –                                             | `investment_outflow`  | Przelew do Zurychu (Luigi)                    | stały       | miesięczny, 15. dnia, w CHF | **tak**      | 12 / −50 000,04 CHF |
| `SALARY PAYMENT`              | `Owner Salary Withdrawal` / `Vincenzo Amalfi` | `owner_withdrawal`    | Wypłata właściciela                           | stały       | miesięczny, 28. dnia        | nie          | 12 / −38 400,00 €   |
| `SALARY PAYMENT`              | (pozostałe)                                   | `net_payroll`         | Pensje pracowników                            | stały       | miesięczny, 28. dnia        | nie          | 36 / −64 800,00 €   |
| `DIRECT DEBIT`                | `Social Security Contributions`               | `social_security`     | Składki (Techniker Krankenkasse)              | stały       | miesięczny, 27. dnia        | nie          | 12 / −31 800,00 €   |
| `DIRECT DEBIT`                | `Energy & Gas`                                | `utilities`           | Energia i gaz                                 | stały       | miesięczny, 10. dnia        | nie          | 12 / −7 800,00 €    |
| `DIRECT DEBIT`                | `Income Tax Prepayment`                       | `income_tax_advance`  | Zaliczka na podatek dochodowy                 | stały       | kwartalny, 10.03/06/09/12   | nie          | 4 / −4 600,00 €     |
| `DIRECT DEBIT`                | `Trade Tax Prepayment`                        | `trade_tax_advance`   | Zaliczka na podatek od działalności (Gewerbe) | stały       | kwartalny, 15.02/05/08/11   | nie          | 4 / −900,00 €       |
| `DIRECT DEBIT`                | (nic nie pasuje)                              | `other_outflow`       | Inne wydatki                                  | –           | –                           | –            | 0 w 2025            |
| (nieznany typ), kwota ≥ 0     | –                                             | `other_inflow`        | Inne wpływy                                   | –           | –                           | –            | 0 w 2025            |
| (nieznany typ), kwota < 0     | –                                             | `other_outflow`       | Inne wydatki                                  | –           | –                           | –            | 0 w 2025            |


Kontrola: suma liczności = 1 125 (wszystkie transakcje 2025 mają kategorię inną niż `other_*`).

> Pensje pracowników to 3 osobne przelewy (Yusuf Akcit, Rosa Pavese, Vanessa Paradi), a wypłata właściciela to czwarty.
> W prognozie każdy traktujemy jako osobną pozycję cykliczną (klucz = kategoria + kontrahent).

### 6.2. Grupy do prognozy


| Grupa                  | Kategorie                                                                                                                                                 |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Przychody zmienne      | `card_revenue_debit`, `card_revenue_credit`                                                                                                               |
| Koszty zmienne         | `supplier_invoices`, `fresh_produce`                                                                                                                      |
| Koszty stałe cykliczne | `rent`, `payroll_tax`, `investment_outflow`, `owner_withdrawal`, `net_payroll`, `social_security`, `utilities`, `income_tax_advance`, `trade_tax_advance` |
| Inne                   | `other_inflow`, `other_outflow` – pokazywane w historii, nie prognozowane                                                                                 |


Grupy są zgodne z klasami przepływów używanymi już w silniku KPI i serwerze MCP
(`OPERATING_INFLOW`, `VARIABLE_OUTFLOW`, `FIXED_OUTFLOW`, `OTHER_*`).

---

## 7. Moduł 2 – Prognoza (`forecast.py`)

**Wejście:** transakcje do dnia „dziś", lista zdarzeń (Moduł 3), horyzont (domyślnie 60 dni).
**Wyjście:** lista 60 dni; każdy dzień ma `data`, `saldo`, `wplywy`, `wydatki` i `pozycje`
(z czego się składa).

### Krok A – Płatności stałe (cykliczne)

Dla każdej pozycji stałej (kategoria + kontrahent):

1. weź **ostatnie wystąpienie** przed „dziś" (kwota, data),
2. następna data = ostatnia data + 1 miesiąc (albo + 3 miesiące dla kwartalnych),
3. powtarzaj, aż wyjdziesz poza horyzont.

Przykład: czynsz był 01.05 → prognoza: 01.06, 01.07.
Zaliczka na podatek dochodowy była 10.03 → prognoza: 10.06.

### Krok B – Przychody i koszty zmienne (średnie)

- weź **ostatnie 28 dni** przed „dziś",
- `sredni_przychod_dzienny` = suma przychodów zmiennych / liczba dni **otwartych**
(dni z przychodem > 0; wtedy zamknięcia nie psują średniej),
- `srednie_zakupy_dzienne` = analogicznie dla kosztów zmiennych,
- każdy przyszły dzień dostaje te dwie średnie,
- jeśli w oknie 28 dni jest mniej niż **14 dni otwartych** (np. tuż po długim zamknięciu), okno wydłuża się
wstecz aż do 14. ostatniego dnia otwartego; API zwraca faktyczną długość okna (`lookback_days`).

Uproszczenie: ignorujemy sezonowość. To podejście ostrożne (lato/jesień były lepsze).

### Krok C – Nałożenie zdarzeń (Moduł 3)

Wszystkie aktywne zdarzenia (sekcja 8.2) modyfikują plan dni przed policzeniem salda. Zakup CHF (`fx_prebuy`)
jest nakładany jako ostatni, bo usuwa raty, które inne zdarzenia (przesunięcia) dopiero dopisały do planu.

### Krok D – Saldo dzień po dniu

```
saldo = saldo_dzis
for dzien in horyzont:
    saldo = saldo + wplywy(dzien) - wydatki(dzien)
    zapisz(dzien, saldo)
```

**Deterministyczne:** te same dane i te same zdarzenia dają zawsze ten sam wynik.

---

## 8. Moduł 3 – Zdarzenia z czatu (`events.py` + AI)

Właściciel **nie wypełnia formularza**. Opisuje plan w czacie z asystentem AI językiem naturalnym,
np. *„w czerwcu zamykamy na urlop od 1 do 21"* albo *„w lipcu kupuję piec za 8 tys."*.
AI rozpoznaje **typ zdarzenia** i jego parametry, a kod nakłada je na prognozę.

### 8.1. Przebieg rozmowy

1. Użytkownik pisze scenariusz w czacie.
2. LLM (przez OGX) przypisuje wypowiedź do **jednego typu z zamkniętej listy** (8.2) i wyciąga parametry: daty, kwotę, opis.
3. Jeśli czegoś brakuje albo jest niejednoznaczne (np. brak kwoty, „latem"), LLM **dopytuje**, zamiast zgadywać.
4. LLM pokazuje w czacie podsumowanie do potwierdzenia, np.
  *„Rozumiem: zamknięcie lokalu 01.06–21.06.2025. Czy mam to uwzględnić w prognozie?"*
5. **Dopiero po potwierdzeniu** przez użytkownika LLM wywołuje narzędzie MCP zapisujące zdarzenie.
6. Serwer MCP waliduje dane i zapisuje zdarzenie (źródło `czat`, z oryginalną wypowiedzią).
7. Backend przelicza prognozę, ryzyko i opcje; UI odświeża wykres, a asystent komentuje zmianę.

Jedna wypowiedź może zawierać kilka zdarzeń; każde jest potwierdzane osobno lub w jednym zbiorczym podsumowaniu.
Zdarzenia można usunąć z listy „Moje plany" w UI (przycisk „usuń").
Plan dodany przez czat można też usunąć przez czat (np. *„usuń zakup pieca"*), w tym samym schemacie:
propozycja → potwierdzenie użytkownika → wykonanie (asystent pokazuje plan do usunięcia, a usuwa go dopiero po „tak").
Wpisów utworzonych przez agenta nie usuwa się w czacie – wycofuje się je razem z zatwierdzoną opcją albo rekomendacją
(przycisk „Withdraw option” / „Withdraw recommendation” w „Moich planach”).

**LLM nie liczy wpływu zdarzenia na saldo.** Zamienia tekst na ustrukturyzowany wpis; efekt liczy kod (8.2).

### 8.2. Typy zdarzeń i ich wpływ na prognozę

Każdy typ = jedna mała funkcja, która modyfikuje dni prognozy **przed** liczeniem salda.


| Typ               | Kto tworzy                     | Co robi z prognozą                                                                                                             |
| ----------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| `zamkniecie`      | AI z czatu (po potwierdzeniu)  | w dniach `od..do`: przychód zmienny = 0, koszty zmienne = 0 (koszty stałe zostają)                                             |
| `wydatek`         | AI z czatu (po potwierdzeniu)  | w `od`: wydatki += `kwota`                                                                                                     |
| `wplyw`           | AI z czatu (po potwierdzeniu)  | w `od`: wpływy += `kwota`                                                                                                      |
| `wstrzymanie`     | agent (po zatwierdzeniu opcji) | usuwa płatność cykliczną `kategoria` w `od..do` i dopisuje ją jako `wydatek` 2 miesiące później (przesunięcie, nie anulowanie) |
| `linia_kredytowa` | agent (po zatwierdzeniu opcji) | **nie zmienia salda**; ustawia „bezpieczne dno" = −`kwota` od `od`                                                             |
| `term_deposit`    | agent (po zatwierdzeniu propozycji doradcy, sekcja 18) | lokata: w `od` (otwarcie) wydatek = `kwota`; w `do` (zapadalność, jeśli w horyzoncie) wpływ = `kwota` + odsetki; `kategoria` = `investment` albo `tax_reserve` |
| `fx_prebuy`       | agent (po zatwierdzeniu propozycji doradcy, sekcja 18) | zakup CHF z góry: w `od` wydatek = `kwota` (EUR); usuwa z prognozy wszystkie pozycje `investment_outflow` (raty zwykłe i przesunięte) do `do` = dzień ostatniej pokrytej raty |


Z czatu AI może tworzyć tylko typy `zamkniecie`, `wydatek`, `wplyw`. Typy `wstrzymanie` i `linia_kredytowa`
powstają wyłącznie z zatwierdzonej opcji ratunku (Moduł 5), a `term_deposit` i `fx_prebuy` wyłącznie z zatwierdzonej
propozycji doradców CFO (sekcja 18). W kodzie i w bazie typy mają nazwy angielskie: `closure`, `expense`, `inflow`,
`payment_deferral`, `credit_line`, `term_deposit`, `fx_prebuy` (lista w `EventType` i w ograniczeniu CHECK tabeli
`planned_events`).

`wstrzymanie` jest zapisywane jako **jeden wiersz na każdą przesuniętą płatność**: `od` = pierwotny termin,
`do` = nowy termin, z `kwota` i `kategoria`. Dzięki temu przesunięte zobowiązanie pozostaje w prognozie także
wtedy, gdy „dziś" minie pierwotny termin. Usunięcie dowolnego wpisu agenta wycofuje całą decyzję, z której powstał
(zatwierdzoną opcję albo propozycję doradcy).

Lokata i zakup CHF są symulowane, więc zaksięgowane saldo nigdy ich nie pokazuje. Dlatego gdy „dziś" minie dzień
otwarcia lokaty (a zapadalność jest jeszcze przed nami), kapitał jest zdejmowany w **pierwszym dniu prognozy** –
pieniądze pozostają zablokowane aż do zapadalności. Analogicznie po dniu zakupu CHF w pierwszym dniu prognozy
zdejmowana jest suma rat, które zakup usunął z planu (część ceny, której saldo jeszcze nie pokazuje).
Odsetki lokaty liczy jedna funkcja (`events.term_deposit_interest`) wyłącznie z danych zdarzenia:
`kwota × TERM_DEPOSIT_INTEREST_RATE × dni / 365`, gdzie `dni` = zapadalność − otwarcie.

### 8.3. Zabezpieczenia zapisu przez MCP

- Narzędzie zapisu przyjmuje tylko trzy typy czatu (`zamkniecie`, `wydatek`, `wplyw`; enum), daty po „dziś” symulacji (wydatek i wpływ: najwcześniej jutro; zamknięcie: koniec najwcześniej jutro – prognoza zaczyna się od jutra, a dzisiejsze operacje są już w saldzie) i kwotę > 0 (tam, gdzie wymagana).
- Data „dziś" jest przekazywana do serwera MCP nagłówkiem `X-As-Of-Date` (tak jak dla narzędzi odczytu).
- Użytkownik bazy dla serwera MCP dostaje prawo zapisu **tylko** do danych zdarzeń; transakcje, KPI i decyzje pozostają dla niego tylko do odczytu.
- Wpisów agenta (`wstrzymanie`, `linia_kredytowa`, `term_deposit`, `fx_prebuy`) serwer MCP nie może ani utworzyć, ani usunąć. Narzędzie `list_planned_events` pokazuje lokaty i zakup CHF z krótkim opisem słownym (`summary`); skutku w prognozie serwer MCP nie liczy.
- Potwierdzenie użytkownika jest wymagane przed zapisem (patrz otwarte kwestie, sekcja 20 – sposób wymuszenia).

---

## 9. Moduł 4 – Wykrywanie ryzyka (`risk.py`)

```
dno = -limit_kredytowy (jeśli jest) albo 0
min_saldo, dzien_min = minimum z prognozy

ZIELONY  jeśli min_saldo >= dno + bufor (3 000 €)
ŻÓŁTY    jeśli dno <= min_saldo < dno + bufor
CZERWONY jeśli min_saldo < dno
```

Dodatkowo liczymy **okno ryzyka**: od pierwszego dnia pod buforem do `dzien_min`.

---

## 10. Moduł 5 – Opcje ratunku (`options.py`)

**Opcje generuje kod, nie LLM.** Każda opcja = lista dodatkowych zdarzeń (Moduł 3).
Dla każdej kod **liczy prognozę od nowa** i zapisuje wynik.


| ID  | Opcja                             | Jak liczona                                                        | Koszt                                                  |
| --- | --------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------ |
| A   | Linia kredytowa                   | `limit` = (−min_saldo + bufor) zaokrąglone w górę do 5 000 €       | odsetki: Σ dni max(0, −saldo) × 9% / 365 (*założenie*) |
| B   | Przesunięcie przelewów do Zurychu | `wstrzymanie` kategorii `investment_outflow` w oknie ryzyka        | 0 € (wymaga rozmowy z bratem)                          |
| C   | Krótszy urlop                     | zmiana `zamkniecie` na 7 dni                                       | niefinansowy: „2 tygodnie mniej urlopu"                |
| D   | Przesunięcie planowanego wydatku  | tylko jeśli jest `wydatek` w oknie ryzyka → przesuń za `dzien_min` | 0 €                                                    |
| E   | Kombinacja B + mniejsza linia     | najpierw B, potem A na tym, co zostało                             | odsetki (mniejsze niż w A)                             |


Każda opcja w wyniku:

```json
{"id": "E", "opis": "...", "nowe_min_saldo": 1800, "nowy_status": "ZIELONY",
 "koszt_eur": 190, "koszt_niefinansowy": "rozmowa z bratem", "wymaga_zgody": true}
```

**Ranking kodu (fallback):** najpierw opcje dające ZIELONY, potem najniższy koszt w €,
a przy remisie mniej „bolesna" opcja niefinansowa.

> Oprocentowanie 9% to **przykładowe założenie** (stała w konfiguracji).
> Pokazujemy dla porównania, że nieuzgodniony debet jest dużo droższy (np. 13%).

---

## 11. Moduł 6 – LLM (`llm.py` w backendzie + OGX + MCP)

### Jak backend rozmawia z modelem

- Backend wysyła rozmowę do **OGX** (API Responses zgodne z OpenAI), nigdy bezpośrednio do vLLM.
- W wywołaniu przekazuje serwer MCP jako narzędzie oraz nagłówek `X-As-Of-Date` = „dziś" symulacji,
więc model nie widzi danych z przyszłości.
- Historię rozmowy przechowuje OGX; backend kontynuuje ją, odwołując się do poprzedniej odpowiedzi.
- Model korzysta z narzędzi MCP, które zwracają KPI i dane o transakcjach (lista narzędzi – do uzupełnienia),
oraz z narzędzia zapisu zdarzeń (Moduł 3).

### Rola LLM

1. **Asystent scenariuszy (czat, Moduł 3):** rozumie opis planu w języku naturalnym, przypisuje go do jednego
  typu zdarzenia z zamkniętej listy, wyciąga daty i kwotę, dopytuje o brakujące dane, pokazuje podsumowanie
   i po potwierdzeniu zapisuje zdarzenie przez MCP.
2. **Analiza sytuacji:** pobiera KPI i dane przez narzędzia MCP (tylko do odczytu) zamiast liczyć je samodzielnie.
3. **Wyjaśnienie prostym językiem:** co się stanie, kiedy i dlaczego (które wydatki, który okres),
  bo właściciel nie jest finansistą.
4. **Rekomendacja:** wybiera **jedną z opcji policzonych przez kod** (Moduł 5) i uzasadnia wybór.
5. **Odpowiedzi na pytania** typu „dlaczego?", „co jeśli…?" w tym samym czacie. Pytanie „co jeśli…"
  z nowym planem traktuje jak scenariusz z punktu 1 (zdarzenie → potwierdzenie → przeliczenie przez kod).

LLM **nie** liczy salda ani prognozy, **nie** wymyśla nowych opcji ratunku i **nie** podejmuje decyzji
finansowych – zatwierdza zawsze właściciel (Moduł 7, karty opcji).

Te same zasady obowiązują doradców CFO (podatki, CHF, lokaty). Ich warstwa LLM – osobne, równoległe wywołania
bez narzędzi MCP, z własnym fallbackiem dla każdego agenta – jest opisana w sekcji 18.7.

### Wejście do rekomendacji („teczka", budowana przez backend)

KPI model pobiera sam przez MCP. Backend dokłada to, czego MCP nie liczy: prognozę z uwzględnieniem zdarzeń i opcje.

```json
{
  "dzis": "2025-05-10",
  "saldo_dzis": 3205.69,
  "horyzont_dni": 60,
  "status": "CZERWONY",
  "min_saldo": -22100.00,
  "dzien_min": "2025-07-03",
  "zdarzenia": [{"typ": "zamkniecie", "od": "2025-06-01", "do": "2025-06-21", "opis": "Urlop"}],
  "srednio_dziennie": {"przychod": 1350.00, "zakupy": 720.00},
  "koszty_stale_w_oknie_ryzyka": [
    {"kategoria": "net_payroll",        "suma": 5400.00, "przesuwalne": false},
    {"kategoria": "investment_outflow", "suma": 8740.00, "przesuwalne": true},
    {"kategoria": "rent",               "suma": 3800.00, "przesuwalne": false}
  ],
  "saldo_tygodniowo": [3205, 2500, -1200, -9800, -15400, -20100, -22100, -18000, -12000],
  "opcje": [ /* lista z Modułu 5 */ ],
  "rekomendacja_kodu": "E"
}
```

*(liczby poza `saldo_dzis` są przykładowe – prawdziwe wylicza kod)*

### Prompt systemowy (szkic)

```
Jesteś asystentem finansowym (CFO) małej pizzerii. Rozmawiasz z właścicielem,
który nie zna się na finansach. Pisz krótko, prosto, po polsku, bez żargonu.

Dane i KPI pobieraj narzędziami MCP. Używaj WYŁĄCZNIE liczb z narzędzi i z przekazanych
danych – nie licz salda samodzielnie i nie wymyślaj liczb.

Gdy właściciel opisuje przyszły plan (urlop, zakup, wpływ):
- przypisz go do jednego typu: zamkniecie | wydatek | wplyw,
- ustal daty i kwotę; jeśli czegoś brakuje, dopytaj,
- pokaż krótkie podsumowanie i poproś o potwierdzenie,
- dopiero po wyraźnym potwierdzeniu wywołaj narzędzie zapisu zdarzenia.

Gdy prosisz o rekomendację: wybierz jedną opcję z listy "opcje". Nie twórz nowych opcji.
Nie podejmuj decyzji za właściciela – rekomendujesz, on zatwierdza.

Rekomendację zwróć jako JSON:
{
  "naglowek": "1 zdanie – co się stanie",
  "wyjasnienie": "3-5 zdań – dlaczego (które wydatki, kiedy)",
  "rekomendowana_opcja": "<id z listy>",
  "uzasadnienie": "2-3 zdania",
  "opcje_komentarz": {"<id>": "1 zdanie plus/minus"}
}
```

### Zabezpieczenia

- `temperature = 0` (powtarzalność),
- typ zdarzenia i parametry waliduje serwer MCP (enum, daty, kwoty) – zły wpis nie trafi do bazy,
- backend sprawdza, czy `rekomendowana_opcja` istnieje na liście; jeśli nie, bierze `rekomendacja_kodu`,
- jeśli LLM nie odpowie albo zwróci zły JSON, UI pokazuje opcje z gotowymi opisami z kodu,
- model nigdy nie wysyła SQL; korzysta tylko z narzędzi MCP o stałych zapytaniach,
- nagłówek `X-As-Of-Date` odcina dane z przyszłości.

---

## 12. Moduł 7 – UI (React, `frontend/`)

**Pasek górny:** data „dziś" (domyślnie 2025-05-10), horyzont (30/60/90), bufor (€).

**Ekran główny, od góry:**

1. **Światło + nagłówek** od LLM, np.
  🔴 *„Za ok. 7 tygodni zabraknie Ci ok. 22 000 € na koncie."*
2. **Wykres:**
  - historia (ciągła niebieska),
  - prognoza bez działania (czerwona przerywana),
  - prognoza z wybraną opcją (zielona przerywana),
  - linia zera i ewentualnie linia limitu kredytowego,
  - zaznaczone zdarzenia (np. szary pas „urlop").
3. **Wyjaśnienie** od LLM + przycisk **„Dlaczego?"** (pokazuje tabelę pozycji z okna ryzyka).
4. **Karty opcji** (A–E): opis, nowe min. saldo, koszt, ⭐ przy rekomendowanej,
  przyciski **Zatwierdź** / **Odrzuć**. Kliknięcie karty = podgląd zielonej linii.
5. **Rekomendacje CFO** („CFO recommendations", pod kartami opcji): podsumowanie agenta nadrzędnego i karty
  propozycji doradców pogrupowane w „Tax" / „Currency (CHF transfers)" / „Investments", z polami wyboru
  i przyciskami **„Approve selected"** / **„Reject selected"** (szczegóły w sekcji 18.10).
6. **Czat z asystentem** (panel boczny lub dolny): opisywanie planów, potwierdzanie rozpoznanych zdarzeń
  (przyciski „Tak, uwzględnij" / „Popraw"), pytania „dlaczego?".
7. **Moje plany:** lista zdarzeń (typ, daty, kwota, opis, źródło) z przyciskiem „usuń"; lokaty i zakup CHF
  mają etykiety „Term deposit" / „CHF purchase" i przycisk „Withdraw recommendation".
8. **Historia decyzji:** tabela decyzji (status, „SYMULOWANE"); decyzje o propozycjach doradców mają prefiks
  „Recommendation" (np. „Recommendation T1: …").

---

## 13. Przepływ E2E (krok po kroku)

1. **Start:** transakcje są już w `bank_transactions` – brak importu.
2. Użytkownik ustawia **„dziś" = 10.05.2025**.
3. Użytkownik pisze w czacie: *„W czerwcu zamykamy na urlop od 1 do 21."*
4. LLM rozpoznaje `zamkniecie 01.06–21.06`, pokazuje podsumowanie; użytkownik potwierdza → zapis zdarzenia przez MCP.
5. Kod: transakcje ≤ „dziś" → **saldo dziś** (3 205,69 €).
6. Kod: **prognoza 60 dni** (stałe + średnie + zdarzenia).
7. Kod: **ryzyko** → CZERWONY, min ok. −22 tys. € 3 lipca.
8. Kod: **opcje A–E**, każda przeliczona.
9. Backend buduje **teczkę JSON**, a **LLM** (z KPI z MCP) zwraca wyjaśnienie i rekomendację.
10. UI pokazuje światło, wykres, wyjaśnienie, karty opcji i odpowiedź w czacie.
11. Właściciel klika **„Zatwierdź E"**:
  - zapis decyzji (status `zatwierdzona`, wykonanie `SYMULOWANE`, migawka prognozy),
    - opcja zamienia się na zdarzenia (źródło `agent`),
    - komunikat: *„Wniosek o linię kredytową 15 000 € wysłany do Commerzbanku (symulacja). Przelew do Luigiego przesunięty na sierpień (symulacja)."*
12. Prognoza przelicza się automatycznie, a światło zmienia się na 🟢.
13. Równolegle z dashboardem UI pobiera **rekomendacje CFO** (`GET /advice`, potem teksty LLM z
  `POST /advice/recommendation`). Przy statusie innym niż ZIELONY doradcy nie proponują niczego, co blokuje gotówkę;
  gdy płynność jest bezpieczna (np. „dziś" = 31.12.2025), właściciel zaznacza propozycje i zatwierdza je jednym
  przyciskiem (sekcja 18).

---

## 14. Kto co robi (wymóg z karty challenge'u)


| Poziom                  | Co                                                                                            |
| ----------------------- | --------------------------------------------------------------------------------------------- |
| **Agent analizuje sam** | kategoryzacja, KPI, prognoza, wykrycie ryzyka, symulacja opcji; fakty i propozycje doradców CFO (podatek, CHF, lokaty) oraz rozstrzyganie konfliktów między nimi |
| **Agent rekomenduje**   | rozpoznanie zdarzenia z czatu, wybór najlepszej opcji + wyjaśnienie prostym językiem; sformułowanie propozycji doradców, niewybieralne wskazówki („Tip") i podsumowanie agenta nadrzędnego |
| **Wymaga zgody**        | zapis zdarzenia z czatu do prognozy; każda akcja z pieniędzmi (kredyt, przesunięcie przelewu, lokata, zakup CHF, blokada kursu) |
| **Wykonanie**           | wyłącznie **symulowane**; zapis decyzji w bazie                                               |


---

## 15. Scenariusz demo (3 minuty)

1. „Vincenzo prowadzi pizzerię i nie ma czasu na finanse." Pokazujemy zielone światło na 10 maja **bez** planów.
2. Vincenzo pisze w czacie: „w czerwcu zamykamy na urlop od 1 do 21". Asystent pyta o potwierdzenie, Vincenzo potwierdza. Światło robi się **czerwone**, a wykres spada do −22 tys. €.
3. Agent wyjaśnia: *„Przez 3 tygodnie nie będzie przychodów, a pensje, czynsz i przelewy do Luigiego trzeba płacić dalej."*
4. Pokazujemy opcje i pytamy w czacie „Dlaczego E?".
5. **Zatwierdź**: zielona linia, światło 🟢, wpis w historii decyzji.
6. *(bonus)* Przesuwamy „dziś" na 31 grudnia. Agent: *„Masz 33 tys. € na koncie, rozważ lokatę."* W sekcji
  „CFO recommendations" są trzy propozycje: lokata-rezerwa podatkowa 14 849,65 €, porada o podwyższeniu zaliczek
  i lokata inwestycyjna 12 000 € na 6 miesięcy. Zaznaczamy wszystkie i zatwierdzamy jednym przyciskiem – status
  pozostaje 🟢 (minimum 3 581,16 € przy buforze 3 000 €).

---

## 16. Wdrożenie na OpenShift AI

1. **Model:** OpenShift AI → Model Serving → runtime **vLLM**, model `cfo-llm` (już wdrożony, `deploy/`).
2. **OGX:** `deploy/ogx-base-config.yaml` + `deploy/ogx.yaml` – serwer OGX przed vLLM.
3. **Serwer MCP:** `deploy/mcp-server.yaml` – narzędzia odczytu oraz jedno narzędzie zapisu (`save_planned_event`); rola bazy `amalfi_mcp` (`db/setup_mcp_user.sh`) może wyłącznie dopisywać zdarzenia z czatu.
4. **Postgres:** już jest, dane załadowane.
5. **Backend:** osobny pod, obraz Python (UBI, `deploy/backend.yaml`), Service; zmienne `PG`*, adres OGX, model, adres MCP.
6. **Frontend:** osobny pod, obraz nginx z buildem React (`frontend/Dockerfile`, `deploy/frontend.yaml`), Route; nginx przekazuje `/api/`* do backendu. Backend nie ma własnego Route.
7. **Bonusy:** vLLM ✅, OGX + MCP ✅, Jupyter Notebook do prototypowania prognozy, MLflow do logowania wywołań LLM (jeśli zostanie czas).
8. **Migracja bazy dla nowych typów zdarzeń:** `db/schema.sql` jest idempotentny – na istniejącej bazie zastępuje
  ograniczenie CHECK kolumny `planned_events.event_type` nazwanym ograniczeniem `planned_events_event_type`
  z listą rozszerzoną o `term_deposit` i `fx_prebuy`. Trzeba go zastosować przed wdrożeniem nowego obrazu backendu.
9. **Środowisko deweloperskie:** `deploy/deploy-dev.sh` wdraża bieżące drzewo robocze (nie gałąź `main`) do osobnego
  namespace `autonomous-cfo-dev`: przepisuje manifesty z `deploy/` na ten namespace i buduje trzy obrazy z lokalnych
  katalogów (binary build). Stos deweloperski jest kompletny i odrębny; współdzielony (tylko do odczytu) jest wyłącznie model.

---

## 17. Struktura repozytorium

```
/frontend                React 18 + TypeScript + Vite, obraz nginx
  src/App.tsx            ekran główny (składa komponenty)
  src/api/               klient REST (/api)
  src/components/        wykres, karty opcji, rekomendacje CFO (AdviceSection, ProposalCard,
                         AdviceApprovalDialog), czat, plany, historia decyzji, dialogi
  src/hooks/             stan czatu, przepływ decyzji (opcje i rekomendacje CFO), podgląd na wykresie,
                         ładowanie danych
  src/lib/               formatowanie kwot i dat, model wykresu, karty i łączny podgląd rekomendacji (advice.ts)
  src/types/             typy kontraktu REST
  e2e/                   testy Playwright: mocked (bez backendu) i live (wdrożona aplikacja)
  nginx-default-cfg/     proxy /api → backend
/backend                 Python (FastAPI)
  app.py                 utworzenie aplikacji FastAPI
  routes.py              REST: health, dashboard, zdarzenia, decyzje, reset demo
  llm_routes.py          REST: rekomendacja i czat
  advice_routes.py       REST: rekomendacje CFO (GET /advice, POST /advice/recommendation, POST /advice/decisions)
  schemas.py             modele Pydantic kontraktu REST
  advice_schemas.py      modele Pydantic kontraktu rekomendacji CFO
  service.py             składanie dashboardu, teczki dla LLM i decyzji
  domain.py              obiekty domenowe (bez I/O)
  repository.py          dostęp do bazy (psycopg 3)
  config.py, deps.py     konfiguracja i zależności FastAPI
  errors.py              błędy i jednolite body błędu
  texts.py               deterministyczne teksty (nagłówek, wnioski, rekomendacja awaryjna)
  ledger.py              transakcje jako księga w EUR, saldo
  kpi_engine/            deterministyczny silnik KPI (istnieje)
  categories.py          kategoryzacja (Moduł 1)
  forecast.py            prognoza (Moduł 2)
  events.py              nakładanie zdarzeń (Moduł 3)
  risk.py                status ryzyka (Moduł 4)
  options.py             opcje A–E + symulacje (Moduł 5)
  llm.py                 klient OGX (Moduł 6)
  llm_prompts.py         prompty i teczka JSON
  llm_recommendation.py  rekomendacja: wywołanie modelu, walidacja, wersja awaryjna
  llm_chat.py            czat: narzędzia MCP, zapis planów, komentarz po zmianie
  advisors/              doradcy CFO (sekcja 18)
    model.py             wspólne typy: Agent, ActionType, Proposal, SuppressedProposal, Advice
    tax.py, fx.py, investment.py   agenci domenowi: fakty i kandydaci
    proposals.py         wspólne klocki: scenariusz przez simulate, odciski, propozycja lokaty
    coordinator.py       agent nadrzędny: tabela reguł R1–R6
    engine.py            punkt wejścia build_advice
    prompts.py, llm_service.py     dossier, prompty, wywołania LLM z walidacją i fallbackiem
    grounding.py         kontrola dat, procentów i kwot w tekstach modelu względem dossier
    decisions.py         zapis decyzji o kilku propozycjach naraz
/mcp_server              narzędzia MCP (KPI i dane – odczyt; zdarzenia – zapis)
/db                      schemat, import CSV, dane referencyjne, rola bazy dla MCP
/deploy                  manifesty OpenShift (frontend, backend, MCP, OGX, Postgres, CI poller)
/Challenge               CSV z wyciągiem
```

---

## 18. Doradcy CFO – agent podatkowy, FX, inwestycyjny i agent nadrzędny (`backend/advisors/`)

Agent płynności odpowiada na pytanie „czy wystarczy pieniędzy". Trzej doradcy domenowi odpowiadają na pytanie
„co zrobić z pieniędzmi, gdy ich wystarcza": odłożyć rezerwę na podatek, zabezpieczyć raty w CHF, ulokować nadwyżkę.
**Agent nadrzędny (koordynator)** zbiera ich kandydatów, usuwa konflikty stałą tabelą reguł w kodzie i zwraca
wyłącznie propozycje, które właściciel może zatwierdzić **w dowolnej kombinacji**.

### 18.1. Zasady wspólne

- **Liczby liczy kod.** Kwoty, odsetki, daty i skutek w prognozie każdej propozycji pochodzą z kodu; skutek jest
  liczony tą samą funkcją `simulate`, co opcje ratunku (Moduł 5). LLM tylko formułuje i wyjaśnia.
- **Brak wycieku przyszłości.** Agenci dostają `Workspace` (`service.build_workspace`): dashboard, księgę transakcji
  tylko do „dziś" włącznie, wszystkie jeszcze niezakończone plany (także te zaczynające się po horyzoncie) i funkcję
  `simulate`.
- **Wąski zakres.** Podatek: wyłącznie podatek dochodowy. FX: wyłącznie raty w CHF do Zurychu. Inwestycje: wyłącznie
  lokaty terminowe w banku.
- **Założenia spoza danych są nazwanymi stałymi** w `backend/config.py` i są pokazywane właścicielowi na karcie
  propozycji (pole `assumptions`).
- **Agent płynności bez zmian.** Opcje A–E, ryzyko, prompty `/recommendation` i `/chat` działają jak dotąd. Wspólna jest
  tylko definicja nadwyżki (`texts.surplus_eur`), wydzielona z istniejącego wniosku o nadwyżce bez zmiany jego zachowania.
- **Wykonanie zawsze symulowane:** zatwierdzenie = zapis decyzji + ewentualne zdarzenie ze źródłem `agent` + komunikat
  zakończony „(simulated).".

Każdy agent ma dwie warstwy: kod (fakty + kandydaci) i jedno wywołanie LLM (18.7). Identyfikatory propozycji są stałe
i krótkie, bo trafiają do `owner_decisions.option_id` (`VARCHAR(5)`) obok opcji A–E:

| ID   | Agent        | `action_type`  | Co to jest                                                        | Zdarzenie po zatwierdzeniu                    |
| ---- | ------------ | -------------- | ----------------------------------------------------------------- | --------------------------------------------- |
| `T1` | `tax`        | `tax_reserve`  | rezerwa na niedopłatę podatku, odłożona na lokacie                | `term_deposit`, `kategoria = tax_reserve`     |
| `T2` | `tax`        | `advice`       | porada (tekst z kodu), bez skutku w prognozie                     | brak (tylko decyzja)                          |
| `F1` | `fx`         | `fx_prebuy`    | zakup CHF już teraz na wszystkie pozostałe raty                   | `fx_prebuy`                                   |
| `F2` | `fx`         | `fx_forward`   | kontrakt terminowy blokujący kurs pozostałych rat                 | brak (tylko decyzja)                          |
| `I1` | `investment` | `term_deposit` | lokata inwestycyjna z nadwyżki                                    | `term_deposit`, `kategoria = investment`      |

Akcje blokujące gotówkę (`CASH_BLOCKING_ACTIONS`): `tax_reserve`, `fx_prebuy`, `term_deposit`. Każda akcja zaczyna się
**jutro** (`dziś + 1 dzień`), bo dzisiejszy dzień jest już w saldzie.

### 18.2. Agent podatkowy (`advisors/tax.py`)

**Kontekst z danych:** kwartalne zaliczki na podatek dochodowy 1 150 € (10.03, 10.06, 10.09, 10.12) ustalono na podstawie
zysku z poprzedniego roku ok. 30 000 € (zobowiązanie TAX002 w harmonogramie stałych płatności). W 2025 zysk rośnie,
więc zaliczki są za niskie i grozi niedopłata.

**Metoda** (rok podatkowy = rok daty „dziś"):

```
efektywna_stawka   = ANNUAL_INCOME_TAX_PREPAYMENTS_EUR / PRIOR_YEAR_PROFIT_BASE_EUR   = 4 600 / 30 000 ≈ 15,33%
zysk_kasowy        = suma przepływów EUR od 01.01 do „dziś"
                     z pominięciem kategorii: owner_withdrawal, investment_outflow,
                     income_tax_advance, trade_tax_advance
baza_proporcjonalna = PRIOR_YEAR_PROFIT_BASE_EUR × dni / 365      (dni liczone włącznie: 31.12 → 365)
szacowany_podatek  = zysk_kasowy × efektywna_stawka               (do centa)
rezerwa            = max(0, szacowany_podatek − zaliczki na podatek dochodowy zapłacone od 01.01)
już_odłożone       = kapitał aktywnych lokat `term_deposit` z kategorią `tax_reserve`
do_odłożenia       = max(0, rezerwa − już_odłożone)
```

- Stawka jest **wyprowadzona z danych** (zaliczki roczne / zysk poprzedniego roku) i nigdy nie jest wpisana na sztywno;
  żadna stawka ustawowa nie występuje w kodzie ani w tekstach.
- Wypłaty właściciela, raty do Zurychu i same zaliczki nie są kosztem, dlatego zysk kasowy jest wysoki już od wiosny.
- Agent jest **aktywny**, gdy `zysk_kasowy > baza_proporcjonalna` i `rezerwa ≥ TAX_RESERVE_MIN_EUR` (1 000 €).
- Termin rozliczenia niedopłaty **nie jest znany z danych**, więc żaden tekst go nie podaje.

**Kandydaci:**

- `T1` – lokata-rezerwa na `TAX_RESERVE_DEPOSIT_TERM_MONTHS` = 1 miesiąc; kandydat tylko, gdy `do_odłożenia ≥ 1 000 €`.
  Kwotę ustala koordynator (R2): całe `do_odłożenia` albo – gdy budżet jest mniejszy – budżet (rezerwa częściowa;
  opis mówi wtedy, jaką część potrzebnej kwoty pokrywa).
- `T2` – porada pisana przez kod, w dwóch wariantach: zwykły („Ask for higher income tax prepayments" – poproś doradcę
  podatkowego lub urząd o podwyższenie zaliczek kwartalnych) oraz wariant na czas kłopotów z płynnością („Plan for an
  extra income tax bill" – zacznij odkładać, gdy saldo się odbuduje, z kwotą rezerwy).

**Punkty kontrolne na realnych danych** (testy sprawdzają je co do centa):

| „Dziś"     | Zysk kasowy  | Baza proporcjonalna | Zaliczki zapłacone | Rezerwa     |
| ---------- | ------------ | ------------------- | ------------------ | ----------- |
| 10.05.2025 | ok. 32 235 € | ok. 10 685 €        | 1 150 €            | ok. 3 793 € |
| 15.10.2025 | 86 432,04 €  | ok. 23 671 €        | 3 450,00 €         | 9 802,91 €  |
| 31.12.2025 | 126 845,55 € | 30 000 €            | 4 600,00 €         | 14 849,65 € |

### 18.3. Agent FX (`advisors/fx.py`)

**Kontekst z danych:** 12 rat po 4 166,67 CHF (cicha spółka, łącznie 50 000 CHF), 15. dnia miesiąca; kurs i kwota w CHF są
w `payment_reference` (`(4166.67 CHF @ 1.046 EUR/CHF)`), numer raty w tekście `Tranche 05/12`. Ostatnia rata: 15.12.2025.

**Fakty** (liczone tylko z przelewów zaksięgowanych do „dziś"):

- **pozostałe raty** – te, które silnik prognozy jeszcze zapłaci: zwykłe (ta sama projekcja `forecast.project_recurring`,
  z odległą datą końcową, żeby policzyć także raty poza horyzontem) oraz raty przesunięte (`wstrzymanie` tej kategorii),
  o ile ich pierwotny termin jest późniejszy niż ostatni zaksięgowany przelew (wyciąg jest historyczny i nie zna
  symulowanego przesunięcia: po pierwotnym terminie pokazuje ratę jako zapłaconą),
- liczba, daty i kwota w CHF pozostałych rat (`pozostałe_CHF` = liczba × kwota ostatniej raty),
- ostatni, najniższy, najwyższy i średni zaobserwowany kurs,
- **ekspozycja** = `pozostałe_CHF × ostatni_kurs` (koszt pozostałych rat w EUR po ostatnim kursie),
- **wrażliwość** = `pozostałe_CHF × (kurs_max − kurs_min)` – o tyle zmieniłby się koszt w EUR, gdyby kurs wahał się
  tak jak dotąd,
- `wrażliwość < FX_SMALL_SENSITIVITY_EUR` (500 €) → teksty mówią wprost, że ryzyko jest małe,
- gdy zaksięgowany jest dopiero **jeden** przelew, nie ma jeszcze zakresu kursów: „ryzyko, które znika" jest puste,
  a teksty mówią, że brakuje historii kursów do oceny wahań.

**Dwie akcje (i tylko te):**

- `F1` (`fx_prebuy`) – zakup wszystkich pozostałych franków jutro po ostatnim zaobserwowanym kursie: z konta wychodzi
  kwota ekspozycji, a przyszłe raty znikają z prognozy. Blokuje gotówkę, więc podlega budżetowi nadwyżki (R2);
  nie da się jej zmniejszyć.
- `F2` (`fx_forward`) – kontrakt terminowy w Commerzbanku na wszystkie pozostałe raty. **Nie zmienia prognozy salda** –
  korzyścią jest pewna kwota w EUR; wrażliwość jest pokazana jako „ryzyko, które znika". Warunek: co najmniej
  `FX_FORWARD_MIN_TRANCHES` = 2 pozostałe raty.

Założenie obu akcji (pokazane na karcie): kurs = **ostatni zaobserwowany kurs** („assumed rate, not a bank quote"), bo
w danych nie ma kwotowań, punktów terminowych, opłat ani spreadów.

**Agent milczy** (brak faktów i kandydatów), gdy: nie ma już rat do zapłaty, istnieje aktywny zakup CHF (`fx_prebuy`)
albo właściciel zatwierdził już blokadę kursu `F2` dla tych samych rat.

### 18.4. Agent inwestycyjny (`advisors/investment.py`)

**Nadwyżka** – jedna wspólna funkcja `texts.surplus_eur`, używana też przez istniejący wniosek o nadwyżce na dashboardzie:

```
nadwyżka = zaokrąglenie w dół do 1 000 € ( min(saldo dziś, minimum prognozy) − bufor )
           tylko gdy status = ZIELONY (inaczej 0); nigdy ujemna;
           liczona od realnego salda, nie od limitu linii kredytowej
```

**Kandydaci:** lokata terminowa w Commerzbanku na każdy okres z `TERM_DEPOSIT_TERMS_MONTHS` = (1, 3, 6) miesięcy.
Okres jest kandydatem tylko wtedy, gdy prognoza przeliczona z tą lokatą (`simulate`) pozostaje ZIELONA i realne saldo
nie spada poniżej bufora. Test sięga tylko do końca horyzontu prognozy; gdy zapadalność wypada później, założenie na
karcie mówi wprost, że dalszy okres nie jest sprawdzony. Kwotę (resztę budżetu po rezerwie podatkowej i zakupie CHF) i wybór jednego okresu ustala
koordynator (R2, R5).

**Odsetki** = `kapitał × TERM_DEPOSIT_INTEREST_RATE × dni / 365` (do centa), `dni` = zapadalność − otwarcie,
zapadalność = dzień otwarcia + liczba miesięcy. Ta sama funkcja liczy odsetki w prognozie i w tekstach propozycji.

### 18.5. Założenia w `backend/config.py`

Wartości z danych (TAX002) i **założenia ilustracyjne** – tak jak 9% dla linii kredytowej, nie są to oferty banku ani
przepisy:

| Stała                               | Wartość     | Charakter                                                                 |
| ----------------------------------- | ----------- | ------------------------------------------------------------------------- |
| `PRIOR_YEAR_PROFIT_BASE_EUR`        | 30 000 €    | z danych (TAX002: zaliczki oparte o zysk poprzedniego roku)               |
| `INCOME_TAX_PREPAYMENT_EUR`         | 1 150 €     | z danych (TAX002); test porównuje z plikiem stałych płatności             |
| `INCOME_TAX_PREPAYMENTS_PER_YEAR`   | 4           | z danych (TAX002)                                                         |
| `ANNUAL_INCOME_TAX_PREPAYMENTS_EUR` | 4 600 €     | iloczyn dwóch powyższych; stawka efektywna jest z niego wyprowadzana      |
| `TAX_PROFIT_EXCLUDED_CATEGORIES`    | 4 kategorie | przepływy, które nie są kosztem przy liczeniu zysku kasowego              |
| `TAX_RESERVE_MIN_EUR`               | 1 000 €     | **założenie ilustracyjne**: mniejszej niedopłaty agent nie zgłasza        |
| `TAX_RESERVE_DEPOSIT_TERM_MONTHS`   | 1           | **założenie ilustracyjne**: termin rozliczenia nieznany, więc krótko      |
| `TERM_DEPOSIT_INTEREST_RATE`        | 2% rocznie  | **założenie ilustracyjne**, nie oferta banku                              |
| `TERM_DEPOSIT_TERMS_MONTHS`         | (1, 3, 6)   | **założenie ilustracyjne**                                                |
| `FX_FORWARD_MIN_TRANCHES`           | 2           | **założenie ilustracyjne**: blokada kursu ma sens od dwóch rat            |
| `FX_SMALL_SENSITIVITY_EUR`          | 500 €       | **założenie ilustracyjne**: poniżej tej kwoty ryzyko nazywamy małym       |
| `SURPLUS_INSIGHT_MIN_EUR`           | 10 000 €    | istniejący próg wniosku o nadwyżce; bramkuje **tylko** lokatę inwestycyjną |
| `SURPLUS_ROUNDING_EUR`              | 1 000 €     | krok zaokrąglenia nadwyżki                                                |

Kurs forward i kurs zakupu CHF nie mają stałej: z założenia równają się ostatniemu zaobserwowanemu kursowi.

### 18.6. Agent nadrzędny – tabela reguł (`advisors/coordinator.py`)

Wejście: `Workspace` oraz odciski (fingerprints) propozycji, o których właściciel już zdecydował. Wyjście: `Advice` –
propozycje w stałej kolejności `T1`, `T2`, `F1`|`F2`, `I1` (każda z odciskiem `fingerprint`), lista kandydatów odrzuconych przez reguły (`suppressed`,
z kodem powodu; tylko do debugowania, UI jej nie pokazuje), fakty każdego agenta dla LLM i prognoza „wszystko naraz"
(`combined`). Konflikty rozstrzyga **kod, nie LLM**.

`liquidity_first` (otwarty problem z płynnością) = status bazowy ≠ ZIELONY **albo** (istnieje zdarzenie `linia_kredytowa`
**i** któryś dzień prognozy ma saldo < 0).

| Reguła | Treść                                                                                                                                                                                                                                                                                                                                                          | Kod powodu w `suppressed`                                         |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| **R1** | **Płynność ma pierwszeństwo.** Przy `liquidity_first` znikają wszystkie propozycje blokujące gotówkę (`T1`, `F1`, `I1`); kredyt i nowa lokata nigdy nie są proponowane razem. Agent podatkowy pokazuje wtedy tylko `T2` w wariancie „odkładaj, gdy saldo się odbuduje". Status ≠ ZIELONY wyłącza też `F2` (kolidowałby z przesunięciem przelewów do Zurychu, opcje B/E); przy statusie ZIELONYM z używaną linią `F2` zostaje. | `liquidity_first`                                                 |
| **R2** | **Wspólny budżet.** Budżet = nadwyżka (0 przy `liquidity_first`), **bez progu 10 000 €**. Kolejność przydziału: `T1` → `F1` → `I1`. `T1` = `do_odłożenia`, a gdy nie mieści się w budżecie – cały budżet (rezerwa częściowa); poniżej 1 000 € znika. `F1` jest niepodzielne: pokazane tylko, gdy cała kwota mieści się w pozostałym budżecie. `I1` = reszta budżetu zaokrąglona w dół do 1 000 €, pokazana tylko, gdy i nadwyżka, i ta kwota ≥ 10 000 €. Na końcu wszystkie pokazane propozycje są symulowane **razem**: wynik musi być ZIELONY z saldem ≥ bufor; inaczej propozycje są wycofywane od końca priorytetu (`I1`, potem `F1`, potem `T1`). | `over_budget`, `below_threshold`, `below_buffer`, `combined_below_buffer` |
| **R3** | **Lokata może być rezerwą podatkową.** `T1` i `I1` to dwie lokaty z różnych pieniędzy: `T1` z kategorią `tax_reserve` na 1 miesiąc, `I1` z kategorią `investment` z tego, co zostało po R2 – bez dublowania kwot.                                                                                                                                               | –                                                                 |
| **R4** | **Jedna propozycja na te same raty CHF.** `F1` i `F2` nigdy nie występują razem: `F1`, gdy mieści się w budżecie; w przeciwnym razie `F2` (jeśli zostały ≥ 2 raty).                                                                                                                                                                                             | `exclusive_fx`                                                    |
| **R5** | **Jedna lokata inwestycyjna.** Z okresów 1/3/6 miesięcy pokazywany jest najdłuższy, który przechodzi test bufora; pozostałe są odnotowane jako odrzucone.                                                                                                                                                                                                       | `single_deposit`                                                  |
| **R6** | **Nic nie jest proponowane dwa razy.** Zatwierdzone zdarzenia są częścią prognozy bazowej: aktywny `fx_prebuy` albo zatwierdzone `F2` → agent FX milczy; rezerwa podatkowa jest pomniejszana o aktywne lokaty `tax_reserve`. Propozycja o odcisku już zatwierdzonym lub odrzuconym nie jest pokazywana ponownie.                                                | `already_decided`, `rejected`                                     |

**Decyzje właściciela (product ownera) wbudowane w reguły:**

1. Próg 10 000 € (`SURPLUS_INSIGHT_MIN_EUR`) bramkuje **tylko lokatę inwestycyjną** (i istniejący wniosek na dashboardzie).
   Rezerwa podatkowa i zakup CHF korzystają z nadwyżki bez tego progu.
2. Rezerwa podatkowa większa niż budżet jest **przycinana do budżetu** (rezerwa częściowa), a nie ukrywana.
3. Lokata-rezerwa ma najkrótszy okres: **1 miesiąc** (założenie pokazane na karcie).
4. Porady do wyboru (`T2`) **pisze kod** – można je zatwierdzić lub odrzucić i trafiają do historii decyzji. LLM może
   dodać własne wskazówki, ale tylko jako **niewybieralne „Tip"** bez skutku w prognozie.

`T2` jest w ofercie zawsze, gdy agent podatkowy jest aktywny i ten wariant porady nie został jeszcze zatwierdzony ani
odrzucony w danym roku podatkowym.

**Odciski propozycji:** propozycje ze zdarzeniem (`T1`, `F1`, `I1`) mają ten sam odcisk co opcje ratunku
(`options.option_fingerprint` z identyfikatora i zdarzeń); `F2` = sha256 z identyfikatora i pierwotnego terminu ostatniej pozostałej raty (przesunięcie raty nie tworzy więc
nowej propozycji);
`T2` = sha256 z identyfikatora, roku podatkowego i wariantu tekstu.

### 18.7. Warstwa LLM (`advisors/prompts.py`, `advisors/llm_service.py`)

- **Wywołania równoległe:** jedno na każdego agenta, który ma fakty, plus jedno dla agenta nadrzędnego (tylko gdy są
  propozycje) – wszystkie naraz, przez OGX, **bez narzędzi MCP** (jak `/recommendation`).
- **Małe dossier:** agent dostaje wyłącznie swoje fakty, swoje propozycje i informację, czy problem z płynnością jest
  otwarty; koordynator dostaje status, nadwyżkę i skrót wszystkich propozycji. Fakty agenta inwestycyjnego opisują
  wynik po koordynacji (ile wolnej gotówki zajęły rezerwa podatkowa i zakup CHF, ile zostało na lokatę i ewentualnie
  dlaczego lokaty nie ma), żeby żaden tekst nie przedstawił całej nadwyżki jako kwoty do ulokowania. Każda kwota w dossier jest gotowym
  napisem (np. `"€9,803"`, `"1.046 EUR/CHF"`), żeby walidacja akceptowała dokładnie liczby policzone przez kod.
- **Odpowiedź agenta (JSON):** `recommendations` – dla propozycji z dossier: `proposal_id`, `recommended` (model może
  polecić kilka propozycji albo żadnej), własny tytuł i wyjaśnienie (1–3 zdania); `tips` – najwyżej 2 dodatkowe
  wskazówki z domeny agenta, bez liczenia. Schemat JSON (guided decoding) pozwala wskazać wyłącznie identyfikatory
  propozycji, które naprawdę są w ofercie.
- **Odpowiedź agenta nadrzędnego (JSON):** `headline` i `text` (2–4 zdania „co i dlaczego teraz").
- **Model nie decyduje o ofercie:** lista propozycji, kwoty i daty zawsze pochodzą z silnika; model nie może dodać
  propozycji ani zmienić jej parametrów.
- **Walidacja każdej odpowiedzi osobno:** odpowiedź ucięta, zły JSON, nieznana lub powtórzona propozycja, liczba spoza
  dossier (`llm_ungrounded_numbers`), ujemne saldo podane bez znaku. Kontrola liczb (`advisors/grounding.py`) używa
  mechanizmu `ungrounded_amounts` z rekomendacji płynności i zaostrza go dla doradców: każda **data** i każdy
  **procent** w tekście modelu muszą występować w dossier, a kwoty poniżej 10 (kursy, opłaty) muszą zgadzać się
  dokładnie – wymyślony kurs, stawka podatku, oprocentowanie czy termin rozliczenia nie przejdą.
- **Zabezpieczenia w kodzie po strojeniu promptów na żywym modelu** (mały model nie zawsze słucha promptu):
  wskazówka (`tip`), która nie mówi wprost o domenie agenta (słowa kluczowe: podatek/zaliczki; CHF/Zurych/kurs/przelew;
  lokata/odsetki), jest po cichu pomijana; podsumowanie agenta nadrzędnego, które przy więcej niż jednej propozycji
  przedstawia je jako alternatywy („either", „or", „alternatively", „choose between"), jest odrzucane
  (`llm_contradicts_engine`) i zastępowane tekstem z kodu; agent, któremu przy otwartym problemie z płynnością
  wstrzymano wszystkich kandydatów (np. FX przy statusie RED), w ogóle nie jest pytany.
- **Fallback na agenta:** gdy wywołanie jednego agenta zawiedzie (błąd, timeout, nieprawidłowa odpowiedź), tylko ten
  agent pokazuje teksty z kodu (tytuł i opis propozycji, `recommended = true`, bez wskazówek) z kodem powodu
  w `fallback_reason`; pozostali agenci zachowują swoje teksty z modelu. Tak samo podsumowanie ma własny fallback z kodu.
  Propozycja pominięta w poprawnej odpowiedzi modelu dostaje tekst z kodu z `recommended = false`.
- **Bez modelu, gdy nie ma o czym mówić:** brak faktów i propozycji → żadnego wywołania, podsumowanie z kodu
  („No further recommendations right now.").
- **Cache:** odpowiedzi modelu są pamiętane w pamięci procesu (LRU, `ADVICE_CACHE_SIZE` = 256) pod skrótem dossier
  danego wywołania; odpowiedzi awaryjne nie są cache'owane.
- Limity wyjścia: `LLM_ADVICE_MAX_OUTPUT_TOKENS` = 700 (agent), `LLM_ADVICE_SUMMARY_MAX_OUTPUT_TOKENS` = 350 (podsumowanie).

### 18.8. REST (`backend/advice_routes.py`, `backend/advice_schemas.py`)

| Metoda i ścieżka                                 | Co robi                                                                                                                                                                                                                         |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /advice?as_of_date&horizon_days&buffer_eur` | Deterministyczna lista propozycji po koordynacji (bez LLM): status bazowy, `liquidity_first`, `surplus_eur`, `proposals` (teksty z kodu, kwota, daty, odsetki, „ryzyko, które znika", skutek, założenia, symulowane akcje, nowe minimum i status, prognoza dzień po dniu) oraz `suppressed` (debug). |
| `POST /advice/recommendation`                    | Teksty LLM: `summary` (agent nadrzędny), `agents` (źródło `llm`/`fallback`, powód fallbacku, wskazówki) i `proposals` (tytuł, wyjaśnienie, `recommended` dla każdej pokazanej propozycji).                                       |
| `POST /advice/decisions`                         | Zatwierdzenie albo odrzucenie **listy** propozycji naraz: `{as_of_date, horizon_days, buffer_eur, proposal_ids, status, explanations?, fingerprints?}` → 201 z decyzjami i komunikatami wykonania.                                             |

- Walidacja (422): `proposal_ids` – od 1 do 5 unikalnych identyfikatorów w formacie `^[TFI][0-9]$`; klucze `explanations`
  i `fingerprints` muszą należeć do `proposal_ids`; dodatkowe pola są odrzucane.
- `fingerprints` (identyfikator → odcisk propozycji, którą właściciel widział, pole `fingerprint` z `GET /advice`):
  identyfikator oznacza tylko „miejsce" (`T1`, `I1`, …), a odcisk – konkretną propozycję z jej kwotą i datami.
- **409 `proposal_not_available`**, gdy którakolwiek z wybranych propozycji nie jest aktualnie w ofercie (także odrzucona
  przez reguły, już zatwierdzona lub już odrzucona) albo – przy podanym odcisku – zmieniła się od chwili pokazania;
  wtedy nic nie jest zapisywane.
- `POST /demo/reset` usuwa także zdarzenia i decyzje doradców. `DELETE /events/{id}` na lokacie lub zakupie CHF wycofuje
  całą decyzję, z której zdarzenie powstało.

### 18.9. Zapis decyzji (`advisors/decisions.py`)

1. Pod blokadą decyzji (`repository.decision_lock()`) backend **liczy wszystko od nowa** (workspace + koordynator) –
   z klienta przychodzą tylko identyfikatory, nigdy liczby.
2. Jeśli któregoś identyfikatora nie ma w ofercie albo podany odcisk nie zgadza się z aktualną propozycją → 409, bez zapisu.
3. W **jednej transakcji** powstaje po jednym wierszu `owner_decisions` na propozycję (w kolejności wyświetlania
   `T1`, `T2`, `F1`|`F2`, `I1`): `option_id` = identyfikator propozycji, tytuł i opis z kodu, `llm_explanation` = tekst
   pokazany właścicielowi, komunikaty wykonania („… (simulated).") tylko dla zatwierdzeń.
4. **Migawka prognozy** ma ten sam format co dla opcji A–E (`service._forecast_snapshot`) plus odcisk propozycji:
   „przed" = prognoza bazowa; „po" = scenariusz **wszystkich zatwierdzonych razem** (przy odrzuceniu: scenariusz samej
   propozycji); koszt 0 €.
5. Zdarzenia (`term_deposit`, `fx_prebuy`, źródło `agent`, powiązane z decyzją) powstają tylko dla zatwierdzonych
   propozycji. `F2` i `T2` nie tworzą zdarzenia.
6. **Wycofanie:** usunięcie lokaty lub zakupu CHF w „Moich planach" wycofuje decyzję; propozycja wraca wtedy do oferty.
   Zatwierdzonych `T2` i `F2` nie da się wycofać, bo nie mają zdarzenia do usunięcia.

### 18.10. Frontend – sekcja „CFO recommendations" (`frontend/src`)

- Sekcja `AdviceSection` jest **pod kartami opcji A–E** (`OptionsSection` bez zmian). Na górze podsumowanie agenta
  nadrzędnego z oznaczeniem „AI summary" albo „Standard summary (AI unavailable)".
- Karty (`ProposalCard`) są pogrupowane według agenta: **Tax / Currency (CHF transfers) / Investments**. Pokazywane są
  tylko propozycje do wyboru; lista `suppressed` nie jest wyświetlana.
- Karta: pole wyboru, tytuł i wyjaśnienie (najpierw teksty z kodu, po odpowiedzi modelu – jego sformułowanie),
  znaczek „Recommended", kwota, daty, skutek, odsetki albo „Risk removed", nowe minimum salda i status (dla propozycji
  zmieniających prognozę) oraz lista założeń („Assumptions").
- Wskazówki modelu są wypisane pod grupą agenta z etykietą „Tip" i **nie mają pola wyboru**.
- Można zaznaczyć **wiele** propozycji. „Approve selected" otwiera okno potwierdzenia (`AdviceApprovalDialog`: lista
  propozycji z kwotami, łączne nowe minimum salda, symulowane akcje, przycisk „Approve (simulated)"); „Reject selected"
  zapisuje odrzucenie bez dodatkowego okna.
- **Podgląd na wykresie:** „Preview on chart" rysuje jedną propozycję; zaznaczone propozycje są rysowane łącznie
  (prognoza bazowa + suma różnic poszczególnych propozycji). Opcja ratunku i rekomendacje nigdy nie są rysowane razem –
  wygrywa ostatni wybór właściciela.
- Brak propozycji → komunikat „No further recommendations right now.". Stany ładowania i błędów jak w pozostałych
  sekcjach; błąd podsumowania nie blokuje kart.
- Po decyzji odświeżane są dashboard, rekomendacje, plany i historia decyzji; po zatwierdzeniu baner pokazuje komunikaty
  wykonania. Odpowiedź 409 `proposal_not_available` jest zgłaszana właścicielowi i odświeża rekomendacje.
- „Moje plany" i „Historia decyzji" pokazują nowe wpisy (sekcja 12, punkty 7 i 8).

### 18.11. Testy

- **Backend (`backend/tests/`, pytest na realnym wyciągu 2025, fake'i LLM):** `test_advisors_tax.py`,
  `test_advisors_fx.py`, `test_advisors_investment.py` (agenci, punkty kontrolne), `test_advisors_events.py` (nowe typy
  zdarzeń w prognozie), `test_advisors_coordinator.py` (każda reguła osobno), `test_advisors_real_data.py` (daty demo,
  „dowolny podzbiór pokazanych propozycji nie łamie bufora" dla wielu dat, horyzontów i buforów, determinizm, brak
  wycieku przyszłości), `test_advisors_llm.py` (walidacja, fallback na agenta, cache), `test_advisors_api.py`,
  `test_advisors_decisions.py`, `test_advisors_integration.py` (endpointy, zapis wielu decyzji, wycofanie, reset demo,
  współpraca z opcjami A–E), `test_advisors_chat_views.py` (nowe zdarzenia w teczce i w czacie).
- **Testy z bazą (opcjonalne, `KPI_TEST_DATABASE_URL`):** `test_integration_advice_db.py`,
  `test_integration_advisor_events_db.py` – m.in. zapis „wszystko albo nic", dwa równoczesne zatwierdzenia tych samych
  propozycji (201 i 409) oraz migracja ograniczenia `event_type`.
- **Serwer MCP:** `mcp_server/tests/test_new_event_types.py` (listowanie nowych typów, zakaz ich tworzenia i usuwania z czatu).
- **Frontend:** `frontend/e2e/mocked/cfo-recommendations.spec.ts` (Playwright: wyświetlanie, wybór wielu propozycji,
  podgląd na wykresie, zatwierdzenie i odrzucenie, błędy, klawiatura, dostępność, widok telefonu).
- Wywołań prawdziwego modelu przez OGX testy automatyczne backendu nie wykonują – używają fake'ów klienta LLM.

### 18.12. Wyniki na realnych danych

Wyniki `GET /advice` na wyciągu 2025 (bufor 3 000 €, horyzont 60 dni, bez wcześniejszych decyzji):

| „Dziś"     | Status   | `liquidity_first` | Nadwyżka | Pokazane propozycje                                                                                                                                                                                                                                                         | Odrzucone przez reguły (`suppressed`)                             |
| ---------- | -------- | ----------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| 10.05.2025 | CZERWONY | tak               | 0 €      | `T2` – porada „Plan for an extra income tax bill" (rezerwa ok. 3 793 €, odkładać, gdy saldo się odbuduje); prognoza bez zmian (min. −325,55 € 01.07)                                                                                                                        | `T1`, `F1`, `F2`: `liquidity_first`                               |
| 15.10.2025 | ZIELONY  | nie               | 9 000 €  | `T1` – lokata-rezerwa **9 000,00 €** z potrzebnych 9 802,91 € (16.10–16.11, odsetki 15,29 €, nowe min. 4 534,46 € 16.10); `T2` – porada o podwyższeniu zaliczek; `F2` – blokada kursu dla 2 rat (8 333,34 CHF, łącznie ok. 8 717 €, znika ryzyko 100 €)                      | `F1`: `over_budget`; `I1`: `below_threshold`                      |
| 20.10.2025 | ZIELONY  | nie               | 12 000 € | `T1` – pełna rezerwa **10 806,92 €** (21.10–21.11, odsetki 18,36 €, nowe min. 5 038,16 € 28.10); `T2`; `F2` – blokada kursu (ryzyko 100 €)                                                                                                                                  | `F1`: `over_budget`; `I1`: `below_threshold`                      |
| 20.11.2025 | ZIELONY  | nie               | 18 000 € | `T1` – **12 969,35 €** (21.11–21.12, odsetki 21,32 €, nowe min. 8 091,08 € 01.12); `T2`; `F1` – zakup 4 166,67 CHF za **4 350,00 €** (jedna rata, znika ryzyko 50 €, nowe min. 16 710,43 € 01.12). Wszystkie trzy razem: min. 3 741,08 € 01.12, ZIELONY                      | `I1`: `below_threshold`                                           |
| 31.12.2025 | ZIELONY  | nie               | 27 000 € | `T1` – **14 849,65 €** (01.01–01.02.2026, odsetki 25,22 €, nowe min. 15 581,16 € 01.01); `T2`; `I1` – lokata inwestycyjna **12 000,00 €** na 6 miesięcy (01.01–01.07.2026, odsetki 119,01 €, nowe min. 18 430,81 € 01.01). Brak agenta FX (nie ma już rat)                   | `I1` na 3 miesiące i na 1 miesiąc: `single_deposit`               |

Dla 31.12.2025 horyzonty 30 i 90 dni dają te same propozycje, kwoty, daty i odsetki co horyzont 60 dni.

**Zatwierdzenie wszystkich trzech propozycji 31.12.2025** (`POST /advice/decisions`, 201, trzy decyzje): status pozostaje
ZIELONY, minimum prognozy zmienia się z 30 430,81 € na 3 581,16 € (01.01.2026), w planach pojawiają się dwie lokaty
(rezerwa 14 849,65 € do 01.02.2026 i inwestycyjna 12 000,00 € do 01.07.2026), wniosek o nadwyżce znika. Kolejne
`GET /advice` nie zwraca już żadnej propozycji.

**Porównanie z pierwotnymi oczekiwaniami** (metodologia nie była naginana do oczekiwań):

- **10.05.2025 – zgodnie z oczekiwaniem:** status CZERWONY, tylko ostrzeżenie podatkowe, żadnej lokaty ani propozycji FX.
- **Połowa października – trzy różnice względem oczekiwania „rezerwa podatkowa, propozycja FX dla 2 rat, ewentualnie lokata":**
  1. **15.10 rezerwa jest częściowa:** 9 000 € z 9 802,91 €, bo wspólny budżet (nadwyżka) wynosi tylko 9 000 €
     (decyzja właściciela: rezerwa przycinana do budżetu). 20.10 nadwyżka wynosi 12 000 € i rezerwa jest pełna (10 806,92 €).
  2. **Propozycją FX dla dwóch pozostałych rat jest blokada kursu (`F2`), a nie zakup CHF (`F1`):** zakup kosztuje
     8 716,67 €, a po rezerwie podatkowej w budżecie zostaje 0 € (15.10) albo 1 193,08 € (20.10) – reguły R2 i R4.
     Zakup mieści się dopiero 20.11.2025, dla jednej pozostałej raty.
  3. **Nie ma lokaty inwestycyjnej:** nadwyżka po rezerwie jest poniżej progu 10 000 €. Oczekiwanie mówiło
     „ewentualnie", więc to mieści się w oczekiwaniu.
- **31.12.2025 – zgodnie z oczekiwaniem:** brak agenta FX, rezerwa podatkowa 14 849,65 € i lokata inwestycyjna 12 000 €
  na 6 miesięcy; punkt kontrolny podatku zgadza się co do centa.
- **Ryzyko kursowe jest małe w całym okresie:** możliwe wahanie to 100 € w połowie października i 50 € 20.11.2025;
  każdy tekst agenta FX mówi to wprost.

**Zachowanie silnika, o którym warto wiedzieć (niezmieniane):**

- **Rezerwa jest uzupełniana w kolejnych dniach.** Po zatwierdzeniu `T1` rezerwa dalej rośnie z zyskiem, więc `T1` wraca
  na różnicę: 1 806,92 € 20.10 po zatwierdzeniu 9 000 € 15.10; 1 161,00 € 25.11 po zatwierdzeniu 20.11.
- **Odrzucenie `T1` oddaje jego budżet lokacie inwestycyjnej:** 31.12.2025 `I1` rośnie wtedy do 27 000 € (min. 3 430,81 €).
- **Linia kredytowa i lokata mogą współistnieć po fakcie.** Koordynator nigdy nie proponuje lokaty przy otwartym problemie
  z płynnością, ale jeśli po zatwierdzeniu lokaty pojawi się nowy duży wydatek, agent płynności zaproponuje linię
  kredytową, gdy lokata jest jeszcze zablokowana. Wyjściem właściciela jest wycofanie lokaty w „Moich planach";
  żadna reguła tego nie podpowiada.
- **Zatwierdzenie wszystkiego zostawia mały zapas:** 3 581,16 € (31.12) i 3 741,08 € (20.11) przy buforze 3 000 €.
- **Zatwierdzonych `T2` i `F2` nie da się wycofać** – nie tworzą zdarzenia; `T2` pozostaje „już zdecydowane" dla danego
  roku podatkowego i wariantu tekstu.

---

## 19. Czego świadomie NIE robimy

- Uczenia maszynowego do prognozy (średnie wystarczą i są wyjaśnialne).
- Liczenia czegokolwiek przez LLM (salda, prognozy, kosztu opcji).
- Sezonowości, dni świątecznych, prawdziwych integracji z bankiem.
- Prawdziwego wykonywania przelewów (wszystko jest symulowane).
- Doradcy podatkowy, FX i inwestycyjny istnieją (sekcja 18), ale świadomie w wąskim zakresie. **Nie** robimy: innych
  podatków niż dochodowy, stawek ustawowych ani terminu rozliczenia niedopłaty; innych walut niż CHF, opłat, spreadów,
  punktów terminowych ani prognoz kursu; funduszy, akcji, obligacji ani kredytów inwestycyjnych; prawdziwych ofert banku
  (oprocentowanie i okresy lokat to założenia ilustracyjne).

---

## 20. Otwarte kwestie (do ustalenia w zespole)

1. **Horyzont prognozy** – ten dokument zakłada 60 dni, a istniejący silnik KPI liczy prognozę na 30 dni. Czy Moduł 2 ma rozszerzyć silnik KPI, czy być osobnym kodem? Odpowiedź: Moduł 2 jest osobnym kodem w backendzie (`forecast.py`); prognoza na wybrany horyzont jest liczona na żądanie i nigdzie niezapisywana. Silnik KPI (30 dni) pozostaje bez zmian i zasila tylko narzędzia analityczne MCP, których aplikacja nie udostępnia modelowi.
2. **Lista narzędzi MCP z KPI** – jest opisana w mcp_server
3. **Wymuszenie potwierdzenia przed zapisem zdarzenia** – samą instrukcją w prompcie czy mechanizmem zatwierdzania wywołań narzędzi MCP w OGX (do sprawdzenia, czy wersja OGX na klastrze to wspiera). Odpowiedź: intrukcja w prompcie


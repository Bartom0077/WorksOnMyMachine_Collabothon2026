# Autonomous CFO – Pizzeria Amalfi

### Agent płynności finansowej · Collabothon 2026 · Red Hat / Commerzbank Challenge

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
8. prognoza przelicza się, a wykres pokazuje efekt (czerwona linia → zielona).

**Zasada nr 1:** liczby liczy kod (deterministycznie). LLM rozpoznaje intencję użytkownika, tłumaczy i rekomenduje spośród opcji, które policzył kod.

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
oraz po zatwierdzeniu opcji przez właściciela (Moduł 5):


| Dane                          | Opis                                                                    |
| ----------------------------- | ----------------------------------------------------------------------- |
| identyfikator                 | unikalny                                                                |
| typ zdarzenia                 | jedna z wartości z Modułu 3 (zamknięta lista)                           |
| data od / data do             | dla jednodniowych data do = data od                                     |
| kwota (EUR)                   | puste dla zamknięcia i wstrzymania                                      |
| kategoria                     | tylko dla wstrzymania (np. `investment_outflow`)                        |
| opis                          | krótki opis rozpoznany przez AI                                         |
| oryginalna wypowiedź          | tekst użytkownika z czatu, z którego powstało zdarzenie (ślad audytowy) |
| źródło                        | `czat` (użytkownik) albo `agent` (zatwierdzona opcja)                   |
| data „dziś" w momencie zapisu | `as_of_date` symulacji                                                  |
| czas utworzenia               |                                                                         |


**4. Decyzje właściciela (zapis, ślad audytowy):**
„dziś" symulacji, identyfikator i opis opcji, status (`zatwierdzona` / `odrzucona`),
wykonanie (`SYMULOWANE`), wyjaśnienie od LLM, migawka prognozy w momencie decyzji, czas decyzji.

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
Wpisów utworzonych przez agenta nie usuwa się w czacie – wycofuje się je razem z zatwierdzoną opcją („Wycofaj opcję” w „Moich planach”).

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


Z czatu AI może tworzyć tylko typy `zamkniecie`, `wydatek`, `wplyw`. Typy `wstrzymanie` i `linia_kredytowa`
powstają wyłącznie z zatwierdzonej opcji ratunku (Moduł 5).

`wstrzymanie` jest zapisywane jako **jeden wiersz na każdą przesuniętą płatność**: `od` = pierwotny termin,
`do` = nowy termin, z `kwota` i `kategoria`. Dzięki temu przesunięte zobowiązanie pozostaje w prognozie także
wtedy, gdy „dziś" minie pierwotny termin. Usunięcie dowolnego wpisu agenta wycofuje całą zatwierdzoną opcję.

### 8.3. Zabezpieczenia zapisu przez MCP

- Narzędzie zapisu przyjmuje tylko typy z listy powyżej (enum), daty po „dziś” symulacji (wydatek i wpływ: najwcześniej jutro; zamknięcie: koniec najwcześniej jutro – prognoza zaczyna się od jutra, a dzisiejsze operacje są już w saldzie) i kwotę > 0 (tam, gdzie wymagana).
- Data „dziś" jest przekazywana do serwera MCP nagłówkiem `X-As-Of-Date` (tak jak dla narzędzi odczytu).
- Użytkownik bazy dla serwera MCP dostaje prawo zapisu **tylko** do danych zdarzeń; transakcje, KPI i decyzje pozostają dla niego tylko do odczytu.
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
5. **Czat z asystentem** (panel boczny lub dolny): opisywanie planów, potwierdzanie rozpoznanych zdarzeń
  (przyciski „Tak, uwzględnij" / „Popraw"), pytania „dlaczego?".
6. **Moje plany:** lista zdarzeń (typ, daty, kwota, opis, źródło) z przyciskiem „usuń".
7. **Historia decyzji:** tabela decyzji (status, „SYMULOWANE").

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

---

## 14. Kto co robi (wymóg z karty challenge'u)


| Poziom                  | Co                                                                                            |
| ----------------------- | --------------------------------------------------------------------------------------------- |
| **Agent analizuje sam** | kategoryzacja, KPI, prognoza, wykrycie ryzyka, symulacja opcji                                |
| **Agent rekomenduje**   | rozpoznanie zdarzenia z czatu, wybór najlepszej opcji + wyjaśnienie prostym językiem          |
| **Wymaga zgody**        | zapis zdarzenia z czatu do prognozy; każda akcja z pieniędzmi (kredyt, przesunięcie przelewu) |
| **Wykonanie**           | wyłącznie **symulowane**; zapis decyzji w bazie                                               |


---

## 15. Scenariusz demo (3 minuty)

1. „Vincenzo prowadzi pizzerię i nie ma czasu na finanse." Pokazujemy zielone światło na 10 maja **bez** planów.
2. Vincenzo pisze w czacie: „w czerwcu zamykamy na urlop od 1 do 21". Asystent pyta o potwierdzenie, Vincenzo potwierdza. Światło robi się **czerwone**, a wykres spada do −22 tys. €.
3. Agent wyjaśnia: *„Przez 3 tygodnie nie będzie przychodów, a pensje, czynsz i przelewy do Luigiego trzeba płacić dalej."*
4. Pokazujemy opcje i pytamy w czacie „Dlaczego E?".
5. **Zatwierdź**: zielona linia, światło 🟢, wpis w historii decyzji.
6. *(bonus)* Przesuwamy „dziś" na grudzień. Agent: *„Masz 33 tys. € na koncie, rozważ lokatę."*

---

## 16. Wdrożenie na OpenShift AI

1. **Model:** OpenShift AI → Model Serving → runtime **vLLM**, model `cfo-llm` (już wdrożony, `deploy/`).
2. **OGX:** `deploy/ogx-base-config.yaml` + `deploy/ogx.yaml` – serwer OGX przed vLLM.
3. **Serwer MCP:** `deploy/mcp-server.yaml` – narzędzia odczytu oraz jedno narzędzie zapisu (`save_planned_event`); rola bazy `amalfi_mcp` (`db/setup_mcp_user.sh`) może wyłącznie dopisywać zdarzenia z czatu.
4. **Postgres:** już jest, dane załadowane.
5. **Backend:** osobny pod, obraz Python (UBI, `deploy/backend.yaml`), Service; zmienne `PG`*, adres OGX, model, adres MCP.
6. **Frontend:** osobny pod, obraz nginx z buildem React (`frontend/Dockerfile`, `deploy/frontend.yaml`), Route; nginx przekazuje `/api/`* do backendu. Backend nie ma własnego Route.
7. **Bonusy:** vLLM ✅, OGX + MCP ✅, Jupyter Notebook do prototypowania prognozy, MLflow do logowania wywołań LLM (jeśli zostanie czas).

---

## 17. Struktura repozytorium

```
/frontend                React 18 + TypeScript + Vite, obraz nginx
  src/App.tsx            ekran główny (składa komponenty)
  src/api/               klient REST (/api)
  src/components/        wykres, karty opcji, czat, plany, historia decyzji, dialogi
  src/hooks/             stan czatu, przepływ decyzji, ładowanie danych
  src/lib/               formatowanie kwot i dat, model wykresu
  src/types/             typy kontraktu REST
  e2e/                   testy Playwright: mocked (bez backendu) i live (wdrożona aplikacja)
  nginx-default-cfg/     proxy /api → backend
/backend                 Python (FastAPI)
  app.py                 utworzenie aplikacji FastAPI
  routes.py              REST: health, dashboard, zdarzenia, decyzje, reset demo
  llm_routes.py          REST: rekomendacja i czat
  schemas.py             modele Pydantic kontraktu REST
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
/mcp_server              narzędzia MCP (KPI i dane – odczyt; zdarzenia – zapis)
/db                      schemat, import CSV, dane referencyjne, rola bazy dla MCP
/deploy                  manifesty OpenShift (frontend, backend, MCP, OGX, Postgres, CI poller)
/Challenge               CSV z wyciągiem
```

---

---

## 19. Czego świadomie NIE robimy

- Uczenia maszynowego do prognozy (średnie wystarczą i są wyjaśnialne).
- Liczenia czegokolwiek przez LLM (salda, prognozy, kosztu opcji).
- Sezonowości, dni świątecznych, prawdziwych integracji z bankiem.
- Prawdziwego wykonywania przelewów (wszystko jest symulowane).
- Pozostałych agentów (FX, podatki, inwestycje), co najwyżej jako jednozdaniowy bonus.

---

## 20. Otwarte kwestie (do ustalenia w zespole)

1. **Horyzont prognozy** – ten dokument zakłada 60 dni, a istniejący silnik KPI liczy prognozę na 30 dni. Czy Moduł 2 ma rozszerzyć silnik KPI, czy być osobnym kodem? Odpowiedź: Moduł 2 jest osobnym kodem w backendzie (`forecast.py`); prognoza na wybrany horyzont jest liczona na żądanie i nigdzie niezapisywana. Silnik KPI (30 dni) pozostaje bez zmian i zasila tylko narzędzia analityczne MCP, których aplikacja nie udostępnia modelowi.
2. **Lista narzędzi MCP z KPI** – jest opisana w mcp_server
3. **Wymuszenie potwierdzenia przed zapisem zdarzenia** – samą instrukcją w prompcie czy mechanizmem zatwierdzania wywołań narzędzi MCP w OGX (do sprawdzenia, czy wersja OGX na klastrze to wspiera). Odpowiedź: intrukcja w prompcie


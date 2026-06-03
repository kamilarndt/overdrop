# Research 3: Task lifecycle i DAG

**1. Pełny lifecycle zadania: od utworzenia do zamknięcia**

Cykl życia zadania w systemach agentowych zależy od implementacji, ale najlepiej obrazuje go maszyna stanów protokołu DropSite, oparta na systemie plików [1, 2]. Pełny cykl obejmuje następujące przejścia:
*   **DROP / INBOX**: Nowe zadanie trafia do folderu `inbox`, czekając na przypisanie lub podjęcie przez agenta [1, 2].
*   **CLAIMED**: Agent rezerwuje zadanie (w DropSite poprzez atomową operację `os.rename()`, co zapobiega konfliktom współbieżności) [2].
*   **ACTIVE**: Zadanie jest w trakcie wykonywania [2]. Jeśli agent zawiesi się w tym stanie, mechanizm "Stale Task Reaper" po określonym czasie timeoutu przenosi zadanie z powrotem do `inbox` [3].
*   **DONE / FAILED**: Po zakończeniu zadanie ląduje w `done` (sukces) lub `failed` (wyczerpane limity ponowień lub uszkodzone pliki) [1, 2].
*   **FEEDBACK / BLOCKED**: Zadanie może zostać wstrzymane (np. w oczekiwaniu na zewnętrzne zależności – `blocked`) lub skierowane do folderu `feedback` w celu weryfikacji przez człowieka w ramach human-in-the-loop [1, 2, 4]. Odblokowane zadanie lub zadanie z zatwierdzonym feedbackiem wraca do `inbox` [2].

Dodatkowo, narzędzie **pi-crew** wprowadza terminalny stan **`needs_attention`**. Otrzymują go zadania, które zakończyły się bez wywołania odpowiedniej funkcji zgłoszenia wyniku (`submit_result`). Stan ten pozwala na ponowienie zadania (retry) bez blokowania kolejnych faz downstream [5]. Z kolei Overstory wykorzystuje powiadomienia typu `worker_done` i zamyka całe grupy zadań (Task Groups) po zakończeniu podzadań [6].

**2. DAG zależności: implementacja grafu zadań**

*   **pi-multiagent** implementuje grafy jako **statyczne DAG-i (Directed Acyclic Graphs)**. Każdy krok w grafie jest przypisany do konkretnego agenta [7]. Zależności definiuje się za pomocą dwóch kluczowych mechanizmów:
    *   **`needs`**: definiuje zależność warunkowaną sukcesem – dany krok uruchomi się tylko, jeśli krok poprzedzający zakończył się powodzeniem [7].
    *   **`after`**: definiuje zależność czasową/sekwencyjną, pozwalając krokowi na skonsumowanie końcowych dowodów z wcześniejszych linii (lanes) nawet wtedy, gdy poprzedzające kroki uległy awarii lub zostały zablokowane [7]. Ostateczne wyniki (finals) są definiowane przez tzw. "sink steps" (węzły ujścia), a nie przez kolejność w tablicy zadań [7].
*   **Overstory** podchodzi do problemu bardziej hierarchicznie i dynamicznie. Zamiast statycznego pliku JSON, korzysta z **grup zadań (Task Groups)** do śledzenia pracy wsadowej [6]. Koordynator (Coordinator) działa jako ciągły dekompozytor celów – deleguje poszczególne kroki do agentów podrzędnych (np. Supervisor -> Scout, Builder, Reviewer) i śledzi ich postępy, automatycznie zamykając grupę, gdy wszystkie powiązane ze sobą issue zostaną zrealizowane [6, 8].

**3. Statusy operacyjne zadań**

Na podstawie zebranych źródeł można wyróżnić następującą standaryzację statusów:
*   **inbox / claimed / active / done / failed**: Podstawowe foldery i stany przepływu pracy określające gotowość, wykonywanie i finał zadania [1, 2].
*   **blocked**: Zadanie oczekujące na zewnętrzną zależność [1].
*   **feedback / needs_decision**: Stan, w którym agent prosi o weryfikację. W DropSite odpowiada za to folder `feedback` [1, 4]. W systemach takich jak pi-intercom czy pi-subagents, zadanie może zostać zablokowane z flagą `need_decision`, wymuszając odpowiedź od nadrzędnego agenta (supervisora) lub człowieka, z określonym timeoutem [9, 10].
*   **needs_review**: Zadania wymagające weryfikacji przez innych agentów (np. kod). W pi-subagents wykorzystuje się bramki akceptacji (Acceptance Gates), które weryfikują dowody za pomocą tagów (np. `reviewed`, `verified`, `rejected`), nakazując agentom poprawki aż do skutku [11, 12].
*   **needs_attention**: Terminalny status błędu wykonawczego w pi-crew dla zadań nieoddanych poprawnie [5].

**4. Timeout i retry: crash recovery w Overstory**

Overstory posiada zaawansowany system odzyskiwania sprawności (crash recovery) realizowany na kilku poziomach:
*   Wykorzystuje warstwowy **system "watchdog"**: Tier 0 to mechaniczny demon sprawdzający żywotność procesów (liveness), Tier 1 to diagnostyka awarii wspierana przez AI, a Tier 2 to stały agent monitorujący zdrowie "floty" (monitor agent) [6, 13].
*   Cykl życia sesji wspiera tzw. **checkpoint save/restore** w celu odzyskania stanu po kompresji kontekstu oraz mechanizmy orkiestracji przekazywania zadań (handoff) chroniące przed skutkami awarii [6].
*   Jeżeli główny agent (np. Lead) zawiesi się lub umrze w trakcie zadania bez wysłania komunikatu `merge_ready`, administrator może wznowić proces za pomocą flagi **`--recover`** (np. `ov sling <task-id> --capability lead --recover --name <fresh-name>`). Omija to blokadę "workable-status" zadania zamkniętego i pozwala nowemu agentowi podjąć pracę w miejscu, gdzie zmarły agent ją przerwał [14, 15]. 

W przypadku prostszego systemu DropSite, zadania, w których agent zawiódł, wpadają do automatycznej pętli retry (do limitu `max_retries`), a przerwane w połowie zadania (stuck) są wymiatane przez `reap_stale` z powrotem do inboxa po upływie zadanego czasu [3, 16].

**5. Merge queue: scalanie zmian z worktree w Overstory**

Zamiast modyfikować te same pliki i ryzykować konflikty, Overstory wymusza pełną izolację – **każdy agent działa we własnym, odizolowanym środowisku git worktree** [6, 13]. Kiedy praca dobiega końca:
*   Agenci w worktree wysyłają typ wiadomości protokołowej `merge_ready` poprzez wbudowany system mailowy oparty na bazie SQLite [6, 14].
*   Następnie Overstory korzysta z kolejki łączenia **FIFO (SQLite-backed FIFO merge queue)**, która wykorzystuje do synchronizacji pliki blokujące (sentinel-file lock) [6, 17].
*   Proces ten wdraża predykcję typu "dry-run" (uruchomienie próbne przed scaleniem) oraz **4-poziomowy system rozwiązywania konfliktów (4-tier conflict resolution)**, który bezpiecznie scala gałęzie z poszczególnych worktree z powrotem do gałęzi kanonicznej (canonical branch) [6, 17]. Wywołanie ręczne odbywa się za pomocą polecenia `ov merge` [18].

**6. File reservations w pi-messenger**

`pi-messenger` operuje bez serwera i demona, współdzieląc stan bezpośrednio na systemie plików (w katalogu `.pi/messenger/`) [19]. Aby uniknąć wyścigów (race conditions) podczas współbieżnego dostępu do plików przez różnych agentów w roju (swarm), wykorzystuje wbudowany **system rezerwacji plików (file reservation system)**, który służy jako mechanizm unikania konfliktów i blokowania zasobów na czas ich odczytu/zapisu [19].

---

**7. Propozycja kompletnego modelu "Task Lifecycle" dla OverDrop (Hybryda Overstory + DropSite)**

Projektując idealny system łączący elegancję i bezstanowość DropSite [2] z zaawansowaną izolacją i obsługą drzewa zależności Overstory/pi-multiagent [6, 7], cykl życia "OverDrop" wyglądałby następująco:

1.  **SPAWN / INBOX (Oparty na plikach + DAG):** 
    Zadanie trafia do globalnego folderu `/inbox`. Definicje zadań posiadają tagi zależności `needs: [task_id]` oraz `after: [task_id]`. Dopóki zależności nie zostaną osiągnięte, zadanie jest w stanie wstrzymania (PENDING_DEPENDENCIES).
2.  **CLAIMED (Atomowa rezerwacja):** 
    Agent odnajduje zadanie i używa atomowego `os.rename()` aby przenieść plik zadania do katalogu `/active/<agent_id>`. Dodatkowo system używa rezerwacji plików a'la pi-messenger na plikach źródłowych związanych z zadaniem.
3.  **ACTIVE / ISOLATED WORKTREE:** 
    Agent nie pracuje na głównym drzewie plików. Podobnie jak w Overstory, system automatycznie klonuje dedykowane *git worktree* dla agenta.
4.  **INTERRUPTED (needs_decision / blocked):** 
    Agent natrafia na niejednoznaczność lub zależność zewnętrzną. Plik zadania ląduje w folderze `/blocked` ze zdefiniowanym webhookiem, komunikatem z prośbą o feedback (human-in-the-loop) lub czeka na subagenta (np. wywołanie `contact_supervisor` z flagą `need_decision`).
5.  **MERGE_QUEUE (Gdy praca kodu jest gotowa):**
    Po zakończeniu pracy w worktree, agent nie scala samodzielnie! Zamiast tego zmienia status zadania na `MERGE_READY` (przenosi do `/merge_queue`). Watchdog/Coordinator (a'la Overstory) przejmuje to z kolejki FIFO, włącza predykcję "dry-run" i przy użyciu 4-poziomowego rozwiązywania konfliktów scala kod do głównej gałęzi.
6.  **REVIEW / ACCEPTANCE GATES:**
    Przed ostatecznym zamknięciem uruchamiani są agenci Recenzenci (`reviewer`). Zadanie figuruje jako `NEEDS_REVIEW`. Błędy wracają na biurko agenta w `active`. Sukces nadaje atest `verified` / `reviewed`.
7.  **DONE / NEEDS_ATTENTION / FAILED:**
    *   *Done*: Zadanie scaliło się i przeszło testy, ląduje w `/done`. Grupa zadań (Task Group) aktualizuje DAG i zwalnia kolejne zadania.
    *   *Needs_Attention*: Agent zakończył proces technicznie, ale bez ważnego rezultatu lub zerwał komunikację bez poddania się kolejce merge – wymusza wezwanie nowego agenta przy pomocy funkcji `--recover`.
    *   *Failed*: Po wyczerpaniu z góry zdefiniowanego limitu `max_retries` i niemożliwości rozwikłania konfliktów (nawet przez AI). Praca w worktree jest niszczona (cleanup).

Taki system zachowuje pełną czytelność przez zwykłe `ls` i `cat` z DropSite, jednocześnie będąc całkowicie bezpiecznym środowiskiem git dla dużych wieloagentowych rojów programistycznych (swarms) z Overstory.
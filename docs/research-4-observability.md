# Research 4: Observability i Wake-up

Oto szczegółowa analiza systemów observability i mechanizmów wybudzania (wake-up) na podstawie dostarczonych źródeł, z uwzględnieniem koncepcji dla systemu OverDrop.

### 1. Jak Overstory implementuje web UI (`ov serve`) i dashboard TUI?

**Web UI (`ov serve`)** w Overstory służy jako główny interfejs (primary operator surface) do monitorowania całego "roju" agentów [1]. System pod spodem:
* Uruchamia serwer **HTTP oraz WebSockets** [2, 3].
* Agenci uruchamiani w trybie domyślnym (headless) działają jako procesy podrzędne (subprocesses), które przesyłają swoje zdarzenia do `stdout` w formacie `stream-json` (NDJSON) [4, 5].
* Interfejs pozwala na obserwację poszczególnych osi czasu agentów (timelines), przeglądanie "poczty" między agentami (mail bus) oraz odczytywanie w pełni ustrukturyzowanych zdarzeń [1, 4].

**Dashboard TUI (`ov dashboard`)** to alternatywa działająca w terminalu na żywo [6]. Jest renderowany za pomocą znaków ANSI (z wykorzystaniem biblioteki Chalk) i umożliwia bezpośredni monitoring stanu agentów bez potrzeby uruchamiania przeglądarki [3, 6].

### 2. Jak pi-interactive-subagents robi tmux panes + mid-run steering?

Rozszerzenie **pi-interactive-subagents** kładzie główny nacisk na pełną przejrzystość działań w terminalu. 
* **Tmux/Zellij panes**: Każdy nowo uruchomiony subagent otrzymuje własny, oddzielny panel w multiplekserze terminala (tmux lub zellij). Użytkownik widzi proces na żywo, co gwarantuje całkowitą widoczność pracy [7].
* **Mid-run steering**: Dzięki fizycznej izolacji paneli, użytkownik może w dowolnym momencie nacisnąć odpowiedni klawisz ucieczki (escape), aby przełączyć się do panelu wybranego agenta, przejąć nad nim bezpośrednią kontrolę i zmodyfikować jego zachowanie w trakcie działania [7]. (Warto zauważyć, że Overstory posiada podobną "furtkę bezpieczeństwa" – tryb `tmux attach` dla interwencji na żywo [5, 8, 9]).

### 3. Jak pi-actors implementuje inspector, triage, room messaging?

Platforma **pi-actors** kompresuje zarządzanie aktorami (agentami lub skryptami) za pomocą wbudowanych mechanizmów koordynacji:

* **Inspector**: Narzędzie terminalowe (domyślnie ukryte, wyświetlające standardowo 12 wierszy logów), którego używa się do monitorowania aktywnej koordynacji. Oferuje kompaktową tabelę optymistyczną ze skrótami wiadomości oraz listami uczestników [10].
* **Triage**: Mechanizm rozwiązywania problemów polega na wyróżnianiu wiadomości wymagających uwagi w Inspektorze (oznaczonych jako `metadata.requires_response=true`) za pomocą wyraźnego symbolu `!`. Użytkownik może użyć komendy `/actors-inspect <number>`, aby rozwinąć pełen widok takiej wiadomości i na nią zareagować [10]. *(Z kolei w Overstory triage jest automatyzowany i wspomagany przez AI jako Tier 1 [11]).*
* **Room messaging**: Każde zadanie wywołuje utworzenie kanału zaawansowanej komunikacji grupowej (adresowanego np. `room:<run>`). Działa to jako lokalna oś czasu i lista obecności (roster). Aktorzy z tego samego uruchomienia mogą dołączać do pokoju, publikować wiadomości, wychodzić i komunikować się z innymi aktorami za pomocą wspólnego formatu koperty (envelope), co powala ograniczyć powiadomienia wyłącznie do powiązanych kontekstowo wątków [12].

### 4. Mechanizmy wake-up (wybudzania agentów/zadań)

Zarządzanie cyklem życia asynchronicznych zadań wykorzystuje w tych systemach cztery główne mechanizmy:

* **Daemon watchdog (Overstory)**: Ciągłe, pasywne monitorowanie infrastruktury. Overstory uruchamia zmechanizowanego demona "Tier 0" (komenda `ov watch`), który działa w tle i na poziomie systemu operacyjnego sprawdza, czy procesy agentów lub sesje tmux są nadal "żywe" (tmux/pid liveness) [2, 11].
* **`pi.sendMessage` triggerTurn (Pi / pi-intercom)**: Wybudzanie przez akcję. W pi-intercom odbiorca (agent), który pozostaje bezczynny (idle), po otrzymaniu bezpośredniej wiadomości automatycznie otrzymuje nową turę przetwarzania (`idle-gated triggerTurn`). Jeśli agent pracuje, nowa wiadomość zostaje wstrzymana i podana jako podpowiedź od razu po wejściu w stan bezczynności [13, 14].
* **Subprocess spawn (Claude, OpenCode)**: Overstory przy tworzeniu nowych instancji workerów bezpośrednio "rozmnaża" procesy CLI agentów (np. `claude`, `opencode`) jako podprocesy (headless subprocess). Każdy agent budzi się w swoim izolowanym obszarze roboczym `git worktree` i przesyła swój status przez `stdout` (strumieniowanie JSON) [4, 5].
* **Webhook / callback (A2A Protocol)**: A2A zaprojektowano dla zdalnych, długotrwałych usług. Klient wywołuje akcję `CreateTaskPushNotificationConfig`, dostarczając adres URL webhooka. Kiedy status odległego zadania ulegnie zmianie, serwer asynchronicznie wybudza klienta wysyłając pod wskazany URL powiadomienie HTTP POST (z payloadem `StreamResponse`) i wykorzystując autoryzację zdefiniowaną podczas rejestracji webhooka [15-18].

### 5. Proponowany system observability dla "OverDrop"

Łącząc mocne strony Overstory, rozszerzeń środowiska Pi oraz natywnych rozwiązań DropSite i A2A, nowoczesny system monitorowania dla architektur opartych o pliki i wiele modeli (OverDrop) powinien składać się z następujących elementów:

* **Live TUI (tmux / zellij)**: Podobnie jak w *pi-interactive-subagents*, architekturę uruchomieniową warto oprzeć na izolacji procesów w panelach. Pozwala to na fizyczne dołączenie użytkownika (`tmux attach`) do powłoki agenta w ramach bezpiecznej ewakuacji (escape hatch), kiedy proces wpada w pętlę lub działa destrukcyjnie [7, 9]. 
* **Web dashboard**: Stanowiący zunifikowane "centrum dowodzenia". Podobnie jak `ov serve` połączony przez HTTP i WebSockets, powinien mapować zmiany ze struktury katalogów na wizualne statystyki (np. odczyty ze statusów pokoi wiadomości *pi-actors* lub struktury logów NDJSON). Będzie czytelniejszy od TUI do szerokiego przeglądu działania roju [1, 2, 4].
* **Log aggregation**: Architektura bezgłowa wymusza ustrukturyzowane logowanie. Każdy podproces agenta powinien zapisywać swoją historię i aktywności w plikach `.jsonl` (lub strumieniować po `stdout` jak Claude Code w Overstory). System w tle musi zbierać logi z całego roju, dostarczając spójnego, filtrowanego interfejsu z opcjami takimi jak narzędzie `Inspector` [4, 5, 10].
* **Mid-run steering**: Integracja możliwości przejmowania kontroli "w biegu" – zarówno na poziomie ręcznego terminalowego TUI (wchodzenie w zellij/tmux) [7], jak i semantycznego wpływania na sesje (np. wysyłanie wiadomości-bodźców jak `ov nudge` z Overstory lub przesyłanie nowych instrukcji konwersacyjnych po wybudzających kanałach intercom, aby zablokować bądź skierować agenta na inne tory bez restartowania sesji [9, 19, 20]).
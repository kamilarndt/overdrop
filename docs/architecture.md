# OverDrop - Complete Design Document

Oto kompletny dokument architektoniczny systemu **OverDrop**, łączący szybkość bazy danych z przejrzystością systemu plików, przygotowany na podstawie dostarczonych badań i specyfikacji protokołów.

# DOKUMENT ARCHITEKTURY SYSTEMU: OVERDROP

## 1. Manifest systemu
* **Nazwa:** OverDrop (Overstory + DropSite)
* **Filozofia:** Best of both worlds. Hybryda, która wykorzystuje szybkość SQLite WAL do typowanego routingu wiadomości i kolejkowania, a bezstanowy, atomowy system plików POSIX do zarządzania stanem, artefaktami i izolacją przestrzeni roboczej [1-3].
* **Cel:** Stworzenie uniwersalnego, zwinnego standardu komunikacji między-agentowej. Lżejszego i mniej zbiurokratyzowanego niż enterprise'owe Google A2A [4, 5], szerszego niż oparte na narzędziach MCP [6] i bardziej ustrukturyzowanego niż czysty DropSite [7, 8].

## 2. Architektura warstwowa

Architektura składa się z trzech współpracujących warstw [3]:

```text
[ WARSTWA 3: OBSERVALILITY & CONTROL ] -> Web UI (NDJSON over HTTP/WS) / TUI (tmux/zellij)
-----------------------------------------------------------------------------------------
[ WARSTWA 2: AGENT RUNTIME ] -> Adaptery (Hermes, Claude, Pi, OpenCode) z egzekwowaniem "guards"
-----------------------------------------------------------------------------------------
[ WARSTWA 1: HYBRID DATA PLANE ] 
  ├─ SQLite WAL: Szybki message bus, kolejka scalania (FIFO), broadcast
  └─ Filesystem (DropSite + Git): Foldery (inbox, active, done), JSONL, Git Worktrees
```
* **Opis:** Baza SQLite służy jako "warstwa transportowa i dyspozytorska" (obsługująca typowane wiadomości takie jak `worker_done`, `merge_ready` i powiadomienia grupowe w 1-5ms) [3, 9]. System plików to główny rejestr: tutaj leżą surowe pliki zadań i logi, a agenci wykonują rzeczywistą pracę w izolowanych kopiach repozytorium (git worktrees) [3, 10].

## 3. Schemat bazy SQLite
System pocztowy i zarządzania zdarzeniami operuje na SQLite w trybie WAL dla uniknięcia blokad (locks) [9].

```sql
-- Główna szyna wiadomości (Mail Bus)
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL, -- np. 'dispatch', 'worker_done', 'escalation', 'merge_ready'
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL, -- wspiera adresowanie '@all', 'room:<run>'
    payload JSON,
    read BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_recipient_unread ON messages(recipient, read);

-- Kolejka scalania Git Worktrees (Merge Queue)
CREATE TABLE merge_queue (
    task_id TEXT PRIMARY KEY,
    branch_name TEXT NOT NULL,
    agent_id TEXT,
    priority INTEGER DEFAULT 5,
    status TEXT DEFAULT 'pending' -- pending, dry_run, resolving, merged, failed
);
```

## 4. Protokół komunikacyjny
Protokół oddziela zarządzanie stanem pliku zadania od komunikacji operacyjnej:
* **Formaty:** Pliki stanu i artefaktów to czysty `.json` lub `.jsonl` na dysku. Wiadomości wymiany (np. prośby o wsparcie) przesyłane są via SQLite [2, 3].
* **Adresowanie:** Wsparcie precyzyjnego routingu P2P z `pi-intercom` [11, 12] (wiadomości prywatne 1:1) oraz powiadomień grupowych (np. `@builders`, `room:<run>`) [9, 13, 14].
* **Rezerwacje:** Do unikania wyścigów używamy atomowych zmian w nazewnictwie plików POSIX: `os.rename()` przy przejmowaniu zadań z `/inbox` do `/active` [2, 15]. Dla powiązanych plików stosuje się File Reservation System (na wzór `pi-messenger`) [16].

## 5. AgentRuntime interface (Specyfikacja adapterów)
Niezależny od środowiska kontrakt pozwalający na swapowanie agentów, abstrakcję podprocesów i nakładanie zabezpieczeń (guards) przed nieautoryzowanym działaniem [17-19].

```typescript
interface AgentRuntime {
  spawn(task: TaskContext): Promise<ProcessHandle>; // Tworzy podproces/wątek
  deployConfig(worktreePath: string): void;         // Inicjalizuje środowisko
  enforceGuards(role: string): void;                // Ogranicza narzędzia (np. brak 'bash')
  parseTranscript(stream: Readable): AsyncIterable<ParsedEvent>; // NDJSON do web UI
}
```

* **Adapter Claude Code / OpenCode:** Uruchamia proces potomny z flagami (np. `-p --output-format stream-json`), aby parsować wyjście w czasie rzeczywistym [19].
* **Adapter Pi:** Narzuca ograniczenia za pomocą wtyczek w `.pi/extensions/` [20]. Komunikacja odbywa się przez lokalny broker (IPC na gniazdach uniksowych z protokołem length-prefixed JSON) z narzędziem `contact_supervisor` [20, 21].
* **Adapter Hermes (Broker Natywny):** Jako środowisko pisane w Pythonie, może operować z pominięciem interfejsu CLI. Zamiast otwierać podprocesy i parsować ich stdout, bezpośrednio wywołuje natywnego `AgentLoop`, operując na pętli asynchronicznej nasłuchującej bazy SQLite i `os.rename()` na systemie plików [22, 23].

## 6. Task lifecycle (stany, przejścia, timeouty)
Hybryda stanów DropSite oraz wielo-agentowych orkiestracji z pi-subagents/Overstory [24, 25]:

1. **PENDING_DEPENDENCIES**: Zadanie czeka na rozwiązanie DAG [25].
2. **INBOX**: Wrzucenie pliku zadania JSON. Agent używa `os.rename()`, by przenieść plik do **ACTIVE** [24, 25].
3. **ACTIVE**: Start izolowanego `git worktree`. Obowiązuje timeout dla zadań (np. 7200 sekund) [25, 26]. 
4. **INTERRUPTED (needs_decision / blocked)**: Jeśli agent utknie lub wymaga ludzkiego/nadrzędnego zatwierdzenia, ląduje w folderze `/blocked` z wysłaniem webhooka/TUI prompta [24, 25].
5. **MERGE_READY**: Zadanie wędruje do SQLite FIFO queue. Start "dry-run" predykcji. Overstory stosuje 4-poziomowe rozwiązywanie konfliktów na gałęziach [25, 27].
6. **NEEDS_REVIEW**: Opcjonalna weryfikacja przez `pi-subagents` Acceptance Gates przed scaleniem [25, 28].
7. **DONE / FAILED**: Stan ostateczny. Jeśli FAILED (i limit `max_retries` niewyczerpany), leci z powrotem do INBOX. Zacięte zadania wymiatane są z powrotem przez systemowy "Stale Task Reaper" po zadanym czasie bezczynności [10, 24].
8. **NEEDS_ATTENTION**: Zadanie wykonane, ale nie wywołało submit_result. Można powtórzyć/zbadać awarię bez paraliżowania reszty DAG-a (zjawisko crash recovery flagą `--recover`) [28-30].

## 7. DAG i orchestracja
Zadania łączą się w grafy acykliczne. Definicja DAG opiera się na dwóch kluczowych tagach:
* `needs: [task_1]`: Zależność warunkowana sukcesem (task odpali się tylko, jeśli task_1 = DONE).
* `after: [task_2]`: Zależność sekwencyjna (konsumuje dowody nawet gdy task_2 zawiodło) [29].

Dla prac wsadowych zastosowano mechanizm Task Groups. Koordynator dekomponuje cel, rozdziela pracę przez SQLite Bus, przypisuje izolowane Git Worktree z węzłami ujścia (sink steps), a po wszystkim cała grupa jest automatycznie zamykana [10, 29]. Zmiany są wcielane asynchronicznie poprzez SQLite-backed FIFO merge queue z gwarancją rozwiązywania konfliktów (chroni to np. pracujących jednocześnie agentów Builder i Reviewer) [27].

## 8. Mechanizmy wake-up
Budzenie asynchroniczne łączy trzy różne podejścia [31, 32]:
* **Tier 0 Watchdog Daemon:** Mechaniczny skrypt systemowy sprawdzający żywotność (liveness) procesów (PID/Tmux pane) [30, 31].
* **Idle-Gated triggerTurn:** Wzorzec z `pi-intercom` – wiadomość zaadresowana do aktywnego agenta zostaje zakolejkowana; gdy agent wchodzi w stan bezczynności (idle), dostaje natychmiastowo nową turę z doręczoną instrukcją bez ubijania sesji [31, 33].
* **Webhooks (A2A-style):** Dla środowisk rozproszonych lub powiadomień UI, używany jest webhook HTTP POST wysyłany po zmianie `TaskState` [31, 34].

## 9. Observability system
Struktura monitoringu łączy interfejs maszynowy, jak i fizyczną kontrolę dla człowieka:
* **Terminal live TUI + Panes:** Każdy agent otrzymuje swój osobny panel (pane) w multiplekserze (tmux lub zellij). Jeśli agent "zwariuje", człowiek wciska Escape (escape hatch), przechodzi do panelu i przejmuje kontrolę do modyfikacji logiki w tzw. _mid-run steering_ [32, 35]. Można też wysyłać komunikaty korygujące przez `ov nudge` lub wejść w overlay `pi-intercom` [32, 36, 37].
* **Web Dashboard (`ov serve`):** Zbiera zdarzenia NDJSON strumieniowane na standardowe wyjście ze wszystkich środowisk typu _headless subprocess_ (tak uruchamiany jest Claude i OpenCode w Overstory), z wykorzystaniem HTTP i WebSockets do wizualizacji roju i osi czasu [32, 38].
* **Triage / Actor Inspector:** Umożliwia filtrowanie wątków w koordynacji grupowej (oznaczone flagami `metadata.requires_response=true`) dzięki symbolowi `!` na UI [39]. 

## 10. Co NIE wchodzi w zakres
* **Złożone polityki uwierzytelniania HTTP/OAuth:** Wymiana certyfikatów, podpisywanie kart agentów (JWS), czy rygorystyczne ramy Security/OAuth2 to domena korporacyjnego protokołu Google A2A [40-42]. OverDrop pracuje wewnątrz lokalnego środowiska lub chmury zaufanej [8].
* **Niskopoziomowy dostęp do narzędzi (API Tools Protocol):** Interfejs łączenia agenta bezpśrednio z bazami danych czy IDE nie jest przedmiotem protokołu. Do integracji narzędzi/zasobów należy używać standardu MCP (Model Context Protocol), który jest komplementarny względem P2P komunikacji w OverDrop [6, 43].
* **Skomplikowane kolejki Messaage Broker'ów (Kafka, RabbitMQ):** Zastąpione wbudowanym mechanizmem bazy dyskowej (SQLite WAL) i podziałem plików (DropSite), redukując zależności (zero-dependency na poziomie serwera poza SQLite) [1, 44].
# Research 2: AgentRuntime Adaptery

**1. Jak Overstory implementuje pluggable AgentRuntime interface dla Claude Code, Pi, Gemini, OpenCode?**

Overstory definiuje niezależny od środowiska kontrakt `AgentRuntime` (w pliku `src/runtimes/types.ts`), który pozwala na dynamiczną wymianę "silnika" agenta. Obsługuje on 11 różnych środowisk, w tym Claude Code, Pi, Gemini CLI oraz OpenCode [1, 2]. 

Interfejs ten abstrahuje wszystkie specyficzne dla danego CLI operacje. Każdy z adapterów (`ClaudeRuntime`, `PiRuntime` itd.) jest odpowiedzialny za [2]:
*   **Uruchamianie (spawning):** Tworzenie procesu CLI z odpowiednimi flagami.
*   **Wdrażanie konfiguracji (config deployment):** Przygotowanie plików konfiguracyjnych w izolowanym worktree [2].
*   **Egzekwowanie ograniczeń (guard enforcement):** Blokowanie niebezpiecznych narzędzi (np. modyfikacji plików dla ról tylko do odczytu) przy użyciu mechanizmów natywnych dla danego CLI.
*   **Parsowanie logów (transcript parsing):** Odczytywanie aktywności agenta, zużycia tokenów i kosztów [2].

Domyślne środowisko można ustawić w pliku `config.yaml` lub nadpisać dla konkretnego zadania przy użyciu flagi `ov sling --runtime <name>` [2].

**2. Jak adapter dla Claude Code działa (headless, NDJSON streaming)?**

Adapter Claude Code (`claude` CLI) działa domyślnie w trybie **headless** w nowo tworzonych projektach [2]. 
*   **Subprocess i strumieniowanie:** Overstory uruchamia proces potomny z flagami `-p --output-format stream-json`, co zmusza Claude do ciągłego zwracania zdarzeń NDJSON (Newline Delimited JSON) na standardowe wyjście (`stdout`) [2].
*   **Parsowanie:** Zdarzenia te są w locie parsowane przez metodę `ClaudeRuntime.parseEvents` i przesyłane do serwera `ov serve`, gdzie użytkownik widzi je z pełną precyzją (strukturalne logi) w interfejsie webowym [2].
*   **Guards:** Mechanizmy blokujące (tool-call guards) są wstrzykiwane przez specjalne hooki do pliku `.claude/settings.local.json` [3].
*   Zawsze dostępny jest też tryb `tmux` (`--no-headless`), który pozwala operatorowi przypiąć się do terminala (live attach) i ręcznie sterować agentem [2].

**3. Jak adapter dla Pi działa przez IPC/extensions?**

Adapter dla środowiska Pi (`pi` CLI) jest obecnie w fazie eksperymentalnej i działa odmiennie od Claude:
*   **Brak Headless:** Adapter ten nie implementuje metody `buildDirectSpawn` i wprost **odrzuca flagę `--headless`** [2]. Agenci Pi działają więc najczęściej w podpiętych sesjach (np. tmux).
*   **Guards:** Ograniczenia i zasady bezpieczeństwa narzędzi są implementowane za pomocą dedykowanego **rozszerzenia (guard extension)** umieszczanego w katalogu `.pi/extensions/` [3]. 
*   **IPC (Z ekosystemu Pi):** Co warto zauważyć w szerszym kontekście ekosystemu Pi, narzędzia takie jak `pi-intercom` wykorzystują architekturę opartą na IPC do zarządzania agentami. Posiadają lokalnego brokera działającego na gniazdach uniksowych (macOS/Linux) lub nazwanych potokach (Windows), przesyłając komunikaty jako JSON z prefiksem wielkości danych (length-prefixed JSON) w celu koordynacji procesów potomnych [4]. Adapter Overstory dla Pi prawdopodobnie wykorzystuje podobne, wbudowane rozszerzenia do sterowania agentami.

**4. Jak adapter dla OpenCode/Gemini CLI działa przez subprocess?**

Adaptery te wywołują polecenia z wiersza poleceń w formie prostego podprocesu [5].
*   **Gemini:** Proces CLI jest uruchamiany w bezpiecznym trybie przez przekazanie flagi `--sandbox`, która pełni w tym przypadku rolę wbudowanego mechanizmu "guard" blokującego niebezpieczne akcje [3].
*   **OpenCode:** Uruchamiane jest po prostu polecenie `opencode` CLI. Jest to implementacja o podstawowej funkcjonalności (brak jakiegokolwiek wbudowanego mechanizmu guard w tej chwili - oznaczone jako `(none)`) [3].

**5. Propozycja interfejsu AgentRuntime dla OverDrop**

DropSite działa z pominięciem API, RPC czy kolejek – jego mechanika opiera się w 100% na systemie plików: plikach JSON i katalogach `inbox/`, `active/`, `done/`, `failed/` z atomową zmianą nazwy plików przy przypisywaniu zadań [6, 7]. W systemie Overstory, zamiast spawnować podproces CLI (jak w przypadku Claude/Pi), adapter *OverDrop* zapisywałby zadania na dysku.

```typescript
// Konceptualny interfejs adaptera OverDrop 
// łączący interfejs AgentRuntime (Overstory) z mechaniką DropSite (System plików)

export class DropSiteRuntime implements AgentRuntime {
  workspaceDir: string;

  constructor(workspaceDir: string = "./workspace") {
    this.workspaceDir = workspaceDir;
  }

  // Zamiast spawn() odpalać proces w tmux/headless, tworzy JSON w inbox/
  async spawn(taskSpec: TaskSpec): Promise<string> {
    const taskId = generateUUID();
    const dropFile = `${this.workspaceDir}/inbox/${taskId}.json`;
    
    const dropSiteTask = {
      id: taskId,
      title: taskSpec.description,
      assignee: taskSpec.capability, // np. 'builder' lub 'scout'
      context: taskSpec.files,
      status: "pending"
    };

    await fs.writeFile(dropFile, JSON.stringify(dropSiteTask));
    return taskId;
  }

  // Wymuszenie mechanizmów guards działałoby przez dodanie tagów lub polityk do taska
  async deployConfig(worktreePath: string, guards: GuardRules): Promise<void> {
     // Wpisanie "allowed_tools" w context zadania przekazywanego w DropSite
  }

  // Zamiast NDJSON stream z stdout, adapter używa watchera na foldery DropSite
  watchEvents(taskId: string, callback: (event: OverstoryEvent) => void): void {
     // Watcher sprawdza czy plik przeniósł się z inbox/ -> active/ -> done/
     // i wyzwala zdarzenia kompatybilne z webowym UI (ov serve)
     fs.watch(this.workspaceDir, (eventType, filename) => {
        if (filename.includes(taskId) && eventType === 'rename') {
           callback(this.translateToNDJSON(filename));
        }
     });
  }
}
```

**6. Hermes jako specjalny przypadek (Native Python / Broker)**

*(Uwaga: Informacje na temat brokera "Hermes" nie znajdują się w przekazanych źródłach. Poniższe wnioski wywodzę ze wzorców architektonicznych pasujących do opisanego stosu).*

Jeśli "Hermes" jest zaprogramowanym w Pythonie natywnym brokerem zadań, idealnie wpasowuje się w ekosystem **DropSite** jako "orkiestrator" (ponieważ cały kod DropSite to w zasadzie jeden plik Python `dropsite.py` niewymagający zewnętrznych zależności [8]). 

Zamiast funkcjonować jako kolejny proces owinięty przez Overstory, Hermes mógłby bezpośrednio importować bibliotekę DropSite i pełnić rolę natywnego obserwatora pętli (`AgentLoop`) [9, 10]:

```python
# Teoretyczny przykład wykorzystania Hermesa wewnątrz infrastruktury DropSite
from dropsite import DropSite, AgentLoop, TaskBuilder

class HermesBroker:
    def __init__(self, workspace="./workspace"):
        self.ds = DropSite(workspace)
        
    def orchestrate(self, incoming_overstory_task):
        # Hermes tłumaczy abstrakcję Overstory na język DropSite
        task = (TaskBuilder(incoming_overstory_task.title, "hermes-broker")
                .assign(incoming_overstory_task.assigned_agent)
                .context(incoming_overstory_task.context)
                .build())
        self.ds.submit(task)

    def run_worker_loop(self, agent_name, handler_function):
        # Hermes bezpośrednio puszcza pętle bez narzutu CLI subprocess
        loop = AgentLoop(self.ds, agent_name, handler=handler_function)
        loop.run()
```
Taka natywna integracja w Pythonie uczyniłaby Hermesa centralną szyną danych (brokerem). Hermes nie potrzebowałby REST API ani IPC – mógłby w pełni korzystać z odczytu atomowych modyfikacji plików dokonywanych przez `os.rename()` w katalogu DropSite, zapewniając zero-dependency komunikację pomiędzy agentami [7]. W kontekście Overstory, Hermes byłby po prostu potężniejszym `AgentRuntime`, pozwalającym na bezpośrednią egzekucję modelu bez narzutu CLI CLI-to-stdout.
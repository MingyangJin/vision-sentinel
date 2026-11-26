export type ThemeMode = "dark" | "light";
export type ActiveView = "live" | "topics" | "alerts";
export type GridLayout = "auto" | "1" | "2" | "3";
export type StreamMode = "mjpeg" | "snapshot";
export type TopicSubTab = "ticks" | "config" | "state";

export interface AlertFilterState {
  severity: "all" | "urgent" | "warning" | "info";
  unackedOnly: boolean;
  topicId: string;
}

export interface ChatActionItem {
  name: string;
  args?: Record<string, unknown>;
  ok?: boolean;
  result?: unknown;
  error?: string;
}

export interface TokenUsage {
  input: number;
  cached: number;
  output: number;
  thinking: number;
  duration_s: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "error";
  content: string;
  timestamp: string;
  attachments?: string[];
  actions?: ChatActionItem[];
  usage?: TokenUsage;
}

const KEYS = {
  theme: "vs.ui.theme",
  activeView: "vs.ui.activeView",
  selectedTopicId: "vs.ui.selectedTopicId",
  selectedCameraId: "vs.ui.selectedCameraId",
  gridLayout: "vs.ui.gridLayout",
  streamMode: "vs.ui.streamMode",
  topicSubTab: "vs.ui.topicSubTab",
  chatDrafts: "vs.ui.chatDrafts",
  chatHistory: "vs.ui.chatHistory",
  alertFilter: "vs.ui.alertFilter",
} as const;

function readJSON<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJSON(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore quota / private mode failures gracefully
  }
}

export const storage = {
  getTheme(): ThemeMode {
    const val = readJSON<string>(KEYS.theme, "dark");
    return val === "light" ? "light" : "dark";
  },
  setTheme(v: ThemeMode): void {
    writeJSON(KEYS.theme, v);
  },

  getActiveView(): ActiveView {
    const val = readJSON<string>(KEYS.activeView, "live");
    return val === "topics" || val === "alerts" ? val : "live";
  },
  setActiveView(v: ActiveView): void {
    writeJSON(KEYS.activeView, v);
  },

  getSelectedTopicId(): string | null {
    return readJSON<string | null>(KEYS.selectedTopicId, null);
  },
  setSelectedTopicId(id: string | null): void {
    writeJSON(KEYS.selectedTopicId, id);
  },

  getSelectedCameraId(): string | null {
    return readJSON<string | null>(KEYS.selectedCameraId, null);
  },
  setSelectedCameraId(id: string | null): void {
    writeJSON(KEYS.selectedCameraId, id);
  },

  getGridLayout(): GridLayout {
    const v = readJSON<string>(KEYS.gridLayout, "auto");
    return v === "1" || v === "2" || v === "3" ? v : "auto";
  },
  setGridLayout(v: GridLayout): void {
    writeJSON(KEYS.gridLayout, v);
  },

  getStreamMode(): StreamMode {
    const v = readJSON<string>(KEYS.streamMode, "mjpeg");
    return v === "snapshot" ? "snapshot" : "mjpeg";
  },
  setStreamMode(v: StreamMode): void {
    writeJSON(KEYS.streamMode, v);
  },

  getTopicSubTab(): TopicSubTab {
    const v = readJSON<string>(KEYS.topicSubTab, "ticks");
    return v === "config" || v === "state" ? v : "ticks";
  },
  setTopicSubTab(v: TopicSubTab): void {
    writeJSON(KEYS.topicSubTab, v);
  },

  getChatDrafts(): Record<string, string> {
    return readJSON<Record<string, string>>(KEYS.chatDrafts, {});
  },
  setChatDraft(topicId: string, draft: string): void {
    const all = storage.getChatDrafts();
    if (!draft) {
      delete all[topicId];
    } else {
      all[topicId] = draft;
    }
    writeJSON(KEYS.chatDrafts, all);
  },

  getChatHistory(): Record<string, ChatMessage[]> {
    return readJSON<Record<string, ChatMessage[]>>(KEYS.chatHistory, {});
  },
  setChatHistory(topicId: string, messages: ChatMessage[]): void {
    const all = storage.getChatHistory();
    all[topicId] = messages.slice(-60);
    writeJSON(KEYS.chatHistory, all);
  },

  getAlertFilter(): AlertFilterState {
    const base: AlertFilterState = { severity: "all", unackedOnly: false, topicId: "all" };
    return { ...base, ...readJSON<Partial<AlertFilterState>>(KEYS.alertFilter, base) };
  },
  setAlertFilter(filter: AlertFilterState): void {
    writeJSON(KEYS.alertFilter, filter);
  },
};

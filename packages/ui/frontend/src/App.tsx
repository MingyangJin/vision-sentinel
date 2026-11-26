import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Bell,
  Bot,
  Camera,
  Menu,
  Moon,
  Sun,
} from "lucide-react";
import {
  api,
  type AlertItem,
  type CameraInfo,
  type SystemHealth,
  type TopicPreset,
  type TopicSummary,
} from "@/lib/api";
import {
  storage,
  type ActiveView,
  type AlertFilterState,
  type ChatMessage,
  type GridLayout,
  type StreamMode,
  type ThemeMode,
  type TopicSubTab,
} from "@/lib/storage";
import { cn } from "@/lib/utils";
import { AlertInboxView } from "@/components/AlertInboxView";
import { LiveWallView } from "@/components/LiveWallView";
import { TopicsWorkspaceView, type TeachContext } from "@/components/TopicsWorkspaceView";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export default function App() {
  // Persisted per-device UI state
  const [theme, setTheme] = useState<ThemeMode>(() => storage.getTheme());
  const [activeView, setActiveView] = useState<ActiveView>(() => storage.getActiveView());
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(() =>
    storage.getSelectedTopicId(),
  );
  const [selectedCameraId, setSelectedCameraId] = useState<string | null>(() =>
    storage.getSelectedCameraId(),
  );
  const [gridLayout, setGridLayout] = useState<GridLayout>(() => storage.getGridLayout());
  const [streamMode, setStreamMode] = useState<StreamMode>(() => storage.getStreamMode());
  const [topicSubTab, setTopicSubTab] = useState<TopicSubTab>(() => storage.getTopicSubTab());
  const [chatDrafts, setChatDrafts] = useState<Record<string, string>>(() =>
    storage.getChatDrafts(),
  );
  const [chatHistory, setChatHistory] = useState<Record<string, ChatMessage[]>>(() =>
    storage.getChatHistory(),
  );
  const [alertFilter, setAlertFilter] = useState<AlertFilterState>(() =>
    storage.getAlertFilter(),
  );

  // Server data
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [cameras, setCameras] = useState<CameraInfo[]>([]);
  const [topics, setTopics] = useState<TopicSummary[]>([]);
  const [presets, setPresets] = useState<TopicPreset[]>([]);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);

  // Transient UI state
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [teachContext, setTeachContext] = useState<TeachContext | null>(null);
  const [toast, setToast] = useState<{ msg: string; variant: "default" | "error" } | null>(null);

  const showToast = useCallback((msg: string, variant: "default" | "error" = "default") => {
    setToast({ msg, variant });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(t);
  }, [toast]);

  // Apply dark/light theme class to <html>
  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    storage.setTheme(theme);
  }, [theme]);

  const handleViewChange = (v: ActiveView) => {
    setActiveView(v);
    storage.setActiveView(v);
    setMobileMenuOpen(false);
  };

  const handleSelectTopic = useCallback((id: string) => {
    setSelectedTopicId(id);
    storage.setSelectedTopicId(id);
  }, []);

  const handleSelectCamera = useCallback((id: string) => {
    setSelectedCameraId(id);
    storage.setSelectedCameraId(id);
  }, []);

  const handleGridLayout = (layout: GridLayout) => {
    setGridLayout(layout);
    storage.setGridLayout(layout);
  };

  const handleStreamMode = (mode: StreamMode) => {
    setStreamMode(mode);
    storage.setStreamMode(mode);
  };

  const handleTopicSubTab = (tab: TopicSubTab) => {
    setTopicSubTab(tab);
    storage.setTopicSubTab(tab);
  };

  const handleChatDraftChange = useCallback((topicId: string, draft: string) => {
    storage.setChatDraft(topicId, draft);
    setChatDrafts((prev) => {
      const next = { ...prev };
      if (!draft) delete next[topicId];
      else next[topicId] = draft;
      return next;
    });
  }, []);

  const handleChatHistoryChange = useCallback((topicId: string, msgs: ChatMessage[]) => {
    storage.setChatHistory(topicId, msgs);
    setChatHistory((prev) => ({ ...prev, [topicId]: msgs }));
  }, []);

  const handleAlertFilterChange = (next: AlertFilterState) => {
    setAlertFilter(next);
    storage.setAlertFilter(next);
  };

  const refreshAll = useCallback(async () => {
    try {
      const [h, cams, tps, als] = await Promise.all([
        api.health(),
        api.cameras(),
        api.topics(),
        api.alerts({ limit: 100 }),
      ]);
      setHealth(h);
      setCameras(cams);
      setTopics(tps);
      setAlerts(als);
    } catch {
      // Keep last known UI state on transient poll failure
    }
  }, []);

  useEffect(() => {
    void refreshAll();
    void api.presets().then(setPresets).catch(() => {});
    const id = window.setInterval(() => void refreshAll(), 10_000);
    return () => window.clearInterval(id);
  }, [refreshAll]);

  const unackedCount = alerts.filter((a) => !a.acked_at).length;

  const navItems: Array<{
    id: ActiveView;
    label: string;
    icon: typeof Camera;
    badge?: number;
  }> = [
    { id: "live", label: "Live Wall", icon: Camera, badge: cameras.length || undefined },
    { id: "topics", label: "Topics & Chat", icon: Bot, badge: topics.length || undefined },
    { id: "alerts", label: "Alert Inbox", icon: Bell, badge: unackedCount || undefined },
  ];

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      {/* Desktop Left Navigation Rail */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-card/60 lg:flex">
        <div className="flex h-14 items-center gap-2.5 border-b border-border px-4">
          <img
            src="/favicon.png"
            alt="Vision Sentinel"
            className="h-8 w-8 rounded-lg shadow-xs"
          />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight">Vision Sentinel</div>
            <div className="text-[10px] text-muted-foreground">Unified Local Console</div>
          </div>
        </div>

        <nav className="flex-1 space-y-1 p-3">
          {navItems.map(({ id, label, icon: Icon, badge }) => {
            const active = activeView === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => handleViewChange(id)}
                className={cn(
                  "flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm font-medium transition-colors cursor-pointer",
                  active
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <span className="flex items-center gap-2.5">
                  <Icon className="h-4 w-4" />
                  {label}
                </span>
                {badge !== undefined && (
                  <Badge
                    variant={id === "alerts" && badge > 0 ? "danger" : "secondary"}
                    className="tabular-nums"
                  >
                    {badge}
                  </Badge>
                )}
              </button>
            );
          })}
        </nav>

        {/* Footer status + Theme toggle */}
        <div className="space-y-3 border-t border-border p-3">
          <div className="rounded-lg border border-border bg-muted/40 p-2.5 text-[11px] space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Stream Hub</span>
              <span className="font-mono tabular-nums text-foreground">
                {health ? `${health.cameras_ok}/${health.cameras_total} ok` : "…"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Active Topics</span>
              <span className="font-mono tabular-nums text-foreground">
                {health ? `${health.topics_active}/${health.topics_total}` : "…"}
              </span>
            </div>
          </div>

          <Button
            type="button"
            variant="outline"
            size="sm"
            className="w-full justify-between"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            <span className="flex items-center gap-2">
              {theme === "dark" ? <Moon className="h-3.5 w-3.5" /> : <Sun className="h-3.5 w-3.5" />}
              {theme === "dark" ? "Dark Mode" : "Light Mode"}
            </span>
            <span className="text-[10px] text-muted-foreground">Switch</span>
          </Button>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile Top Header */}
        <header className="sticky top-0 z-30 flex h-13 items-center justify-between border-b border-border bg-card/90 px-3 backdrop-blur-md lg:hidden">
          <div className="flex items-center gap-2">
            <Button
              size="icon"
              variant="ghost"
              aria-label="Open menu"
              onClick={() => setMobileMenuOpen(true)}
            >
              <Menu className="h-5 w-5" />
            </Button>
            <div className="flex items-center gap-2">
              <img src="/favicon.png" alt="Vision Sentinel" className="h-6 w-6 rounded-md" />
              <span className="text-sm font-semibold">Vision Sentinel</span>
            </div>
          </div>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Toggle theme"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </header>

        {/* Non-loopback Security Warning Banner (§5.7) */}
        {health?.non_loopback_warning && (
          <div className="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-700 dark:text-amber-300">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>
              Web UI is bound to <code className="font-mono">{health.bind_host}</code> with{" "}
              <strong>no login in MVP</strong>. Keep this port on your trusted LAN or private tunnel
              — never expose it to the public internet.
            </span>
          </div>
        )}

        {/* Active View Canvas */}
        <main className="mx-auto w-full max-w-[1600px] flex-1 p-4 pb-20 lg:p-6 lg:pb-6">
          {activeView === "live" && (
            <LiveWallView
              cameras={cameras}
              gridLayout={gridLayout}
              onGridLayoutChange={handleGridLayout}
              streamMode={streamMode}
              onStreamModeChange={handleStreamMode}
              selectedCameraId={selectedCameraId}
              onSelectCamera={handleSelectCamera}
              onRefresh={() => void refreshAll()}
            />
          )}

          {activeView === "topics" && (
            <TopicsWorkspaceView
              topics={topics}
              cameras={cameras}
              presets={presets}
              selectedTopicId={selectedTopicId}
              onSelectTopic={handleSelectTopic}
              topicSubTab={topicSubTab}
              onTopicSubTabChange={handleTopicSubTab}
              streamMode={streamMode}
              chatDrafts={chatDrafts}
              onChatDraftChange={handleChatDraftChange}
              chatHistory={chatHistory}
              onChatHistoryChange={handleChatHistoryChange}
              teachContext={teachContext}
              onConsumeTeachContext={() => setTeachContext(null)}
              onTopicsChanged={() => void refreshAll()}
              onToast={showToast}
            />
          )}

          {activeView === "alerts" && (
            <AlertInboxView
              alerts={alerts}
              topics={topics}
              filter={alertFilter}
              onFilterChange={handleAlertFilterChange}
              onRefresh={() => void refreshAll()}
              onTeachAgent={(ctx) => {
                setTeachContext(ctx);
                handleViewChange("topics");
              }}
              onToast={showToast}
            />
          )}
        </main>

        {/* Mobile Bottom Tab Bar (touch-friendly) */}
        <nav className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-3 border-t border-border bg-card/95 backdrop-blur-md lg:hidden">
          {navItems.map(({ id, label, icon: Icon, badge }) => {
            const active = activeView === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => handleViewChange(id)}
                className={cn(
                  "relative flex flex-col items-center justify-center gap-0.5 py-2.5 text-[11px] font-medium cursor-pointer",
                  active ? "text-primary" : "text-muted-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                <span>{label}</span>
                {id === "alerts" && badge !== undefined && badge > 0 && (
                  <span className="absolute right-6 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white">
                    {badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Collapsible Mobile Nav Drawer */}
      <Dialog open={mobileMenuOpen} onOpenChange={setMobileMenuOpen}>
        <DialogContent className="max-w-xs">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <img src="/favicon.png" alt="Vision Sentinel" className="h-6 w-6 rounded-md" />
              Vision Sentinel
            </DialogTitle>
          </DialogHeader>
          <div className="mt-3 space-y-1.5">
            {navItems.map(({ id, label, icon: Icon, badge }) => (
              <button
                key={id}
                type="button"
                onClick={() => handleViewChange(id)}
                className={cn(
                  "flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-sm font-medium cursor-pointer",
                  activeView === id
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <span className="flex items-center gap-2.5">
                  <Icon className="h-4 w-4" />
                  {label}
                </span>
                {badge !== undefined && <Badge variant="secondary">{badge}</Badge>}
              </button>
            ))}
          </div>
          <div className="mt-4 border-t border-border pt-3 text-xs text-muted-foreground space-y-1">
            <div>
              Cameras: {health ? `${health.cameras_ok}/${health.cameras_total} ok` : "—"}
            </div>
            <div>
              Active Topics: {health ? `${health.topics_active}/${health.topics_total}` : "—"}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Lightweight Toast */}
      {toast && (
        <div
          role="status"
          className={cn(
            "fixed bottom-16 right-4 z-50 rounded-lg border px-3.5 py-2 text-xs font-medium shadow-lg lg:bottom-5",
            toast.variant === "error"
              ? "border-destructive/40 bg-destructive text-destructive-foreground"
              : "border-border bg-popover text-popover-foreground",
          )}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}

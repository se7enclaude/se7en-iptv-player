import { AlertOctagon } from "lucide-react";
import { ReactNode, useCallback, useEffect, useState } from "react";
import { Splash } from "./components/Splash";
import { Toasts } from "./components/Toasts";
import { Wordmark } from "./components/TopBar";
import { Button, Modal } from "./components/ui";
import { SHORTCUTS, useGlobalShortcuts } from "./hooks/useShortcuts";
import { resolveLang, translate, useT } from "./i18n";
import { UpdateBanner } from "./components/UpdateBanner";
import { UpdateLaunchNotice } from "./components/UpdateLaunchNotice";
import { LogsPage } from "./pages/LogsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { GuidePage } from "./pages/stream/GuidePage";
import { HomePage, Welcome } from "./pages/stream/HomePage";
import { LivePage } from "./pages/stream/LivePage";
import { RecordingsPage } from "./pages/stream/RecordingsPage";
import { SearchPage } from "./pages/stream/SearchPage";
import { VodPage } from "./pages/stream/VodPage";
import { WatchPage } from "./pages/stream/WatchPage";
import { useStream } from "./stream/store";
import { Ambient, Dock, TopBar } from "./stream/Shell";
import { MyListPage, ProfilesPage } from "./pages/stream/MyListPage";
import { Page as PageId, bootstrap, setTranslator, useApp } from "./store/app";

const PAGES: Partial<Record<PageId, () => ReactNode>> = {
  home: HomePage,
  live: LivePage,
  guide: GuidePage,
  movies: () => <VodPage kind="movie" />,
  series: () => <VodPage kind="series" />,
  watch: WatchPage,
  recordings: RecordingsPage,
  search: SearchPage,
  mylist: MyListPage,
  profiles: ProfilesPage,
  settings: SettingsPage,
  logs: LogsPage,
};
// Playback lives in the backend: pages can be unmounted freely.
const KEEP_ALIVE: PageId[] = [];
// Pages that fill the window (their own columns, no centered column)
const BLEED: PageId[] = ["live", "guide", "movies", "series", "watch"];
// Pages that draw their own padding (home: sidebar + feed like the reference)
const FLUSH: PageId[] = ["home"];
const SPLASH_KEY = "se7eniptv.splash";

function splashEnabled(): boolean {
  try {
    return localStorage.getItem(SPLASH_KEY) !== "0";
  } catch {
    return true;
  }
}

export function App() {
  const t = useT();
  const ready = useApp((s) => s.ready);
  const bootError = useApp((s) => s.bootError);
  const page = useApp((s) => s.page);
  const settings = useApp((s) => s.settings);
  const version = useApp((s) => s.info?.version);
  const [help, setHelp] = useState(false);
  const [splash, setSplash] = useState(splashEnabled);
  const openHelp = useCallback(() => setHelp(true), []);
  useGlobalShortcuts(openHelp);

  const streamLoaded = useStream((s) => s.loaded);
  const hasAccount = useStream((s) => !!s.state?.accounts.length);
  const theater = useStream((s) => s.theater);
  useEffect(() => {
    bootstrap();
  }, []);
  useEffect(() => {
    if (ready) useStream.getState().load().catch(() => useStream.setState({ loaded: true }));
  }, [ready]);

  // theme, motion, language, launch animation preference
  useEffect(() => {
    const g = settings?.general;
    if (!g) return;
    const apply = () => {
      // the design system is dark only (glass over cinema)
      document.documentElement.dataset.theme = "dark";
    };
    apply();
    document.documentElement.dataset.motion = g.reduce_motion ? "reduced" : "full";
    const lang = resolveLang(g.language);
    document.documentElement.lang = lang;
    setTranslator((k, v) => translate(lang, k, v));
    try {
      localStorage.setItem(SPLASH_KEY, g.launch_animation === false ? "0" : "1");
    } catch {
      /* ignore */
    }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [settings?.general]);

  // Settings → "Replay" (launch animation + its sound)
  useEffect(() => {
    const replay = () => setSplash(true);
    window.addEventListener("se7en:replay-splash", replay);
    return () => window.removeEventListener("se7en:replay-splash", replay);
  }, []);

  const [visited, setVisited] = useState<PageId[]>([]);
  useEffect(() => {
    if (KEEP_ALIVE.includes(page) && !visited.includes(page)) setVisited((v) => [...v, page]);
  }, [page, visited]);
  // The splash always sits at the same place in the tree: switching from "loading" to the app must not
  // remount it (that restarted the animation and its sound).
  const splashNode = splash && <Splash version={version} ready={ready || !!bootError} onDone={() => setSplash(false)} />;
  return (
    <>
      {body()}
      {splashNode}
    </>
  );

  function body() {
    if (bootError)
      return (
        <div className="app" style={{ display: "grid", placeItems: "center" }}>
          <div className="card card-pad rise" style={{ maxWidth: 480 }}>
            <div className="eyebrow row" style={{ gap: 8, marginBottom: 12 }}>
              <AlertOctagon size={14} /> {t("app.bootErrorEyebrow")}
            </div>
            <div className="modal-title">{t("app.bootError")}</div>
            <p className="muted" style={{ marginTop: 6 }}>
              {bootError}
            </p>
            <Button variant="primary" style={{ marginTop: 18 }} onClick={() => location.reload()}>
              {t("common.retry")}
            </Button>
          </div>
        </div>
      );

    if (!ready || !streamLoaded)
      return (
        <div className="app" style={{ display: "grid", placeItems: "center" }}>
          {!splash && (
            <div className="col rise" style={{ alignItems: "center", gap: 16 }}>
              <Wordmark size={44} />
              <span className="cursor-block" />
            </div>
          )}
        </div>
      );

    const welcome = !hasAccount && page !== "settings" && page !== "logs";
    const Page = welcome ? Welcome : PAGES[page] ?? HomePage;
    const bleed = !welcome && BLEED.includes(page);
    const flush = !welcome && FLUSH.includes(page);
    return (
      <div className={`sx-root ${theater ? "is-theater" : ""}`}>
        <Ambient />
        {hasAccount && <Dock />}
        <div className="ip-panel ip-panel--r-3xl sx-window" style={hasAccount ? undefined : { left: 24 }}>
          <TopBar />
          <main className="sx-main">
            <UpdateBanner />
            <UpdateLaunchNotice blocked={splash} />
            <div key={welcome ? "welcome" : page} className={`page page-enter ${bleed ? "st-bleed" : ""} ${flush ? "sx-flush" : ""}`}>
              <Page />
            </div>
          </main>
        </div>
        <Toasts />
        <Modal open={help} onClose={() => setHelp(false)} eyebrow="se7en" title={t("shortcuts.title")}>
          <div className="col" style={{ gap: 0, marginTop: 10 }}>
            {SHORTCUTS.map((s) => (
              <div key={s.key} className="toggle-row">
                <span className="toggle-row-text">{t(s.key)}</span>
                <span className="kbd">{s.keys}</span>
              </div>
            ))}
          </div>
        </Modal>
      </div>
    );
  }
}

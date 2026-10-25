import clsx from "clsx";
import { Baby, CalendarDays, CircleDot, Clapperboard, Home, Loader2, MonitorPlay, Play, ScrollText, Search, Settings as SettingsIcon, Square, Tv, UserRound } from "lucide-react";
import { ReactNode } from "react";
import { useT } from "../i18n";
import { Page, useApp } from "../store/app";
import { useStream } from "../stream/store";
import { Button } from "./ui";

export function Wordmark({ size = 26, onClick }: { size?: number; onClick?: () => void }) {
  return (
    <span className="wordmark" style={{ fontSize: size }} onClick={onClick} role={onClick ? "button" : undefined} title={onClick ? "se7en" : undefined}>
      se<span className="seven">7</span>en<span className="wordmark-sub"> iptv player</span>
    </span>
  );
}

export const TABS: { id: Page; n: string; key: string; icon: ReactNode }[] = [
  { id: "home", n: "01", key: "nav.home", icon: <Home size={14} /> },
  { id: "live", n: "02", key: "nav.live", icon: <Tv size={14} /> },
  { id: "guide", n: "03", key: "nav.guide", icon: <CalendarDays size={14} /> },
  { id: "movies", n: "04", key: "nav.movies", icon: <Clapperboard size={14} /> },
  { id: "series", n: "05", key: "nav.series", icon: <MonitorPlay size={14} /> },
  { id: "recordings", n: "06", key: "nav.recordings", icon: <CircleDot size={14} /> },
];

/** Glass top bar: wordmark · tabs · what is playing · profile · search · logs · settings. */
export function TopBar({ onLogo }: { onLogo: () => void }) {
  const t = useT();
  const page = useApp((s) => s.page);
  const setPage = useApp((s) => s.setPage);
  const connected = useApp((s) => s.connected);
  const state = useStream((s) => s.state);
  const now = useStream((s) => s.now);
  const sync = useStream((s) => s.sync);
  const hasAccount = !!state?.accounts.length;
  const profile = state?.profiles.find((p) => p.id === state.profile);
  const syncing = Object.values(sync)[0];
  const onPlayerPage = page === "live" || page === "watch";

  return (
    <header className="topbar">
      <div className="topbar-l">
        <button className="brand-pill glass" onClick={onLogo} title="se7en">
          <Wordmark size={22} />
        </button>
        {hasAccount && (
          <nav className="navpills glass">
            {TABS.map((tab) => {
              const on = page === tab.id;
              return (
                <button key={tab.id} className={clsx("navpill", on && "on")} onClick={() => setPage(tab.id)} data-tip={on ? undefined : t(tab.key)} aria-current={on ? "page" : undefined}>
                  {tab.icon}
                  {on && <span className="navpill-label">{t(tab.key)}</span>}
                </button>
              );
            })}
          </nav>
        )}
      </div>
      <div className="spacer" />
      <div className="topbar-r">
        {!connected && (
          <span className="status-pill glass">
            <span className="status-dot off" />
            {t("app.reconnecting")}
          </span>
        )}
        {connected && syncing && (
          <span className="status-pill glass" data-tip={syncing.label}>
            <Loader2 size={13} className="spin" /> Mise à jour · {Math.round(syncing.percent)} %
          </span>
        )}
        {now && !onPlayerPage && (
          <span className="status-pill glass st-nowpill">
            <button className="st-nowpill-main" onClick={() => setPage((now.kind === "live" ? "live" : "watch") as Page)} data-tip="Revenir à la lecture">
              <Play size={13} /> <span className="st-nowpill-title">{now.title}</span>
            </button>
            <button className="st-nowpill-stop" onClick={() => useStream.getState().stop()} aria-label="Arrêter">
              <Square size={11} />
            </button>
          </span>
        )}
        {hasAccount && profile && (
          <button className="status-pill glass st-profile-pill" onClick={() => useApp.getState().goSettings("profiles")} data-tip="Changer de profil">
            <span className="st-profile-dot" style={{ background: profile.color || "#7FA7AC" }}>{profile.kids ? <Baby size={11} /> : <UserRound size={11} />}</span>
            {profile.name}
          </button>
        )}
        {hasAccount && <Button iconOnly size="sm" className="disc" icon={<Search size={16} />} tip={`${t("nav.search")} (Ctrl+F)`} active={page === "search"} onClick={() => setPage("search")} />}
        <Button iconOnly size="sm" className="disc" icon={<ScrollText size={16} />} tip={t("nav.logs")} active={page === "logs"} onClick={() => setPage("logs")} />
        <Button iconOnly size="sm" className="disc" icon={<SettingsIcon size={16} />} tip={t("nav.settings")} active={page === "settings"} onClick={() => setPage("settings")} />
      </div>
    </header>
  );
}

import clsx from "clsx";
import { Bell, ChevronDown, Download, Heart, House, Lock, Play, ScrollText, Search, Settings, Square, User, X } from "lucide-react";
import { ReactNode, useEffect, useRef, useState } from "react";
import { Page, useApp } from "../store/app";
import { stream } from "./api";
import { toastError, useStream } from "./store";

const COLORS = ["#7FA7AC", "#E9A29A", "#FF8A6B", "#8FB98B", "#B39DDB", "#F2C66D"];
export const profileColor = (c?: string | null, i = 0) => c || COLORS[i % COLORS.length];

/** The room behind the glass: the current hero artwork, blurred and vignetted. */
export function Ambient() {
  const art = useStream((s) => s.ambient);
  return (
    <div className="sx-ambient" aria-hidden>
      {art && <div className="sx-ambient-art" style={{ backgroundImage: `url("${art}")` }} />}
      <div className="sx-ambient-shade" />
      <div className="sx-ambient-lamp" />
    </div>
  );
}

const DOCK: { id: Page; label: string; icon: ReactNode }[] = [
  { id: "home", label: "Accueil", icon: <House size={20} strokeWidth={1.75} /> },
  { id: "mylist", label: "Ma liste", icon: <Heart size={20} strokeWidth={1.75} /> },
  { id: "recordings", label: "Enregistrements", icon: <Download size={20} strokeWidth={1.75} /> },
  { id: "profiles", label: "Profils", icon: <User size={20} strokeWidth={1.75} /> },
];

export function Dock() {
  const page = useApp((s) => s.page);
  const setPage = useApp((s) => s.setPage);
  const recording = useStream((s) => s.recording);
  return (
    <nav className="sx-dock">
      <div className="ip-dock ip-dock--sm ip-dock--light">
        {DOCK.map((d) => (
          <button key={d.id} type="button" className={clsx("ip-dock__item", page === d.id && "is-active")} onClick={() => setPage(d.id)} aria-label={d.label}>
            {d.icon}
            {d.id === "recordings" && recording && <span className="ip-dock__dot" />}
            <span className="ip-dock__tip">{d.label}</span>
          </button>
        ))}
        <span className="ip-dock__sep" />
        <button type="button" className={clsx("ip-dock__item", page === "settings" && "is-active")} onClick={() => setPage("settings")} aria-label="Réglages">
          <Settings size={20} strokeWidth={1.75} />
          <span className="ip-dock__tip">Réglages</span>
        </button>
      </div>
    </nav>
  );
}

const TABS: { id: Page; label: string }[] = [
  { id: "home", label: "Accueil" },
  { id: "movies", label: "Films" },
  { id: "series", label: "Séries" },
  { id: "live", label: "TV en direct" },
  { id: "guide", label: "Guide TV" },
];

function ProfileMenu({ onClose }: { onClose: () => void }) {
  const state = useStream((s) => s.state);
  const setPage = useApp((s) => s.setPage);
  const go = (p: Page) => {
    onClose();
    setPage(p);
  };
  const pick = async (id: string, locked: boolean) => {
    onClose();
    if (locked) return go("profiles");
    try {
      await stream.selectProfile(id);
      await useStream.getState().load();
      useStream.getState().bump();
    } catch (e) {
      toastError(e);
    }
  };
  const pickAccount = async (id: string) => {
    onClose();
    try {
      await stream.selectAccount(id);
      await useStream.getState().load();
      useStream.getState().bump();
    } catch (e) {
      toastError(e);
    }
  };
  return (
    <div className="ip-menu sx-menu" role="menu">
      <div className="ip-menu__title">Profils</div>
      {state?.profiles.map((p, i) => (
        <button key={p.id} type="button" className={clsx("ip-menu__item", p.id === state.profile && "is-selected")} onClick={() => pick(p.id, !!p.locked && p.id !== state.profile)}>
          <span className="sx-profile-avatar" style={{ width: 26, height: 26, fontSize: 11, background: profileColor(p.color, i) }}>{p.name.slice(0, 1).toUpperCase()}</span>
          <span className="ip-menu__body">{p.name}</span>
          {p.locked && <Lock size={13} />}
        </button>
      ))}
      <button type="button" className="ip-menu__item" onClick={() => go("profiles")}>Gérer les profils</button>
      {state && state.accounts.length > 1 && (
        <>
          <div className="ip-menu__title">Abonnements</div>
          {state.accounts.map((a) => (
            <button key={a.id} type="button" className={clsx("ip-menu__item", a.id === state.account && "is-selected")} onClick={() => pickAccount(a.id)}>
              <span className="ip-menu__body">{a.name}</span>
            </button>
          ))}
        </>
      )}
      <div className="ip-menu__title">se7en IPTV Player</div>
      <button type="button" className="ip-menu__item" onClick={() => go("settings")}><Settings size={15} /> Réglages</button>
      <button type="button" className="ip-menu__item" onClick={() => go("logs")}><ScrollText size={15} /> Journal</button>
    </div>
  );
}

export function TopBar() {
  const page = useApp((s) => s.page);
  const setPage = useApp((s) => s.setPage);
  const state = useStream((s) => s.state);
  const now = useStream((s) => s.now);
  const sync = useStream((s) => s.sync);
  const query = useStream((s) => s.query);
  const pending = useStream((s) => s.recording);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const off = (e: MouseEvent) => menuRef.current && !menuRef.current.contains(e.target as Node) && setMenu(false);
    window.addEventListener("mousedown", off);
    return () => window.removeEventListener("mousedown", off);
  }, [menu]);

  const hasAccount = !!state?.accounts.length;
  const pi = state?.profiles.findIndex((p) => p.id === state.profile) ?? 0;
  const profile = state?.profiles[pi];
  const account = state?.accounts.find((a) => a.id === state.account);
  const syncing = Object.values(sync)[0];
  const onPlayer = page === "live" || page === "watch";

  return (
    <header className="sx-top">
      <label className="ip-input ip-input--search sx-search">
        <Search size={18} strokeWidth={1.75} />
        <input
          className="ip-input__el"
          placeholder={hasAccount ? "Rechercher…" : "se7en IPTV Player"}
          disabled={!hasAccount}
          value={query}
          onChange={(e) => {
            useStream.setState({ query: e.target.value });
            if (page !== "search") setPage("search");
          }}
          spellCheck={false}
        />
        {query && (
          <button type="button" className="ip-input__btn" onClick={() => useStream.setState({ query: "" })} aria-label="Effacer"><X size={15} /></button>
        )}
      </label>
      <div className="sx-tabs">
        {hasAccount && (
          <div className="ip-tabs ip-tabs--contained" role="tablist">
            {TABS.map((t) => (
              <button key={t.id} type="button" role="tab" className={clsx("ip-tab", page === t.id && "is-selected")} aria-selected={page === t.id} onClick={() => setPage(t.id)}>
                {t.label}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="sx-right" ref={menuRef}>
        {syncing && !now && <span className="ip-badge ip-badge--glass" title={syncing.label}>Mise à jour · {Math.round(syncing.percent)} %</span>}
        {now && !onPlayer && (
          <span className="ip-btn ip-btn--glass sx-now">
            <button type="button" className="ip-input__btn" style={{ display: "inline-flex", alignItems: "center", gap: 8, margin: 0 }} onClick={() => setPage(now.kind === "live" ? "live" : "watch")}>
              <Play size={13} fill="currentColor" /> <span className="sx-now-title">{now.title}</span>
            </button>
            <button type="button" className="ip-iconbtn ip-iconbtn--ghost ip-iconbtn--sm" onClick={() => useStream.getState().stop()} aria-label="Arrêter"><Square size={12} /></button>
          </span>
        )}
        {hasAccount && (
          <button type="button" className="ip-iconbtn ip-iconbtn--glass" aria-label="Rappels et enregistrements" onClick={() => setPage("recordings")}>
            <Bell size={18} strokeWidth={1.75} />
            {pending && <span className="ip-iconbtn__dot" />}
          </button>
        )}
        {profile && (
          <button type="button" className="ip-btn ip-btn--glass sx-profile" onClick={() => setMenu(!menu)}>
            <span className="sx-profile-avatar" style={{ background: profileColor(profile.color, pi) }}>{profile.name.slice(0, 1).toUpperCase()}</span>
            <span className="sx-profile-text">
              <span className="sx-profile-name">{profile.name}</span>
              <span className="sx-profile-sub">{account?.name ?? "Aucun abonnement"}</span>
            </span>
            <ChevronDown size={16} />
          </button>
        )}
        {menu && <ProfileMenu onClose={() => setMenu(false)} />}
      </div>
    </header>
  );
}

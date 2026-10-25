import clsx from "clsx";
import { Baby, Check, Download, FolderOpen, KeyRound, Lock, Pencil, Plus, RefreshCw, Trash2, Upload, UserRound } from "lucide-react";
import { ReactNode, useEffect, useState } from "react";
import { api } from "../api/client";
import { Badge, Button, Field, Modal, Segmented, Switch } from "../components/ui";
import { useApp } from "../store/app";
import qrcode from "qrcode-generator";
import { Account, Profile, RemoteInfo, StreamSettings, stream } from "./api";
import { AccountForm } from "./AccountForm";
import { Bar, PinField } from "./components";
import { toastError, useStream } from "./store";

function Row({ title, desc, children }: { title: ReactNode; desc?: ReactNode; children: ReactNode }) {
  return (
    <div className="setting">
      <div>
        <div className="setting-title">{title}</div>
        {desc && <div className="setting-desc">{desc}</div>}
      </div>
      <div>{children}</div>
    </div>
  );
}

function useStreamSettings(): [StreamSettings | null, (p: Partial<StreamSettings>) => void] {
  const [s, setS] = useState<StreamSettings | null>(null);
  useEffect(() => {
    stream.settings().then(setS).catch(toastError);
  }, []);
  const upd = (p: Partial<StreamSettings>) => {
    if (s) setS({ ...s, ...p });
    stream.saveSettings(p).then((n) => {
      setS((cur) => ({ ...n, record_default: cur?.record_default, tmdb: cur?.tmdb } as StreamSettings));
      useStream.getState().load();
      if ("group_quality" in p || "quality_pref" in p) useStream.getState().bump(); // lists regrouped
    }).catch(toastError);
  };
  return [s, upd];
}

const fmtDate = (ts?: number | null) => (ts ? new Date(ts * 1000).toLocaleString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "jamais");

// ------------------------------------------------------------------ abonnements
export function AccountsSettings() {
  const state = useStream((s) => s.state);
  const sync = useStream((s) => s.sync);
  const toast = useApp((s) => s.toast);
  const [adding, setAdding] = useState(false);
  const [edit, setEdit] = useState<Account | null>(null);
  const [del, setDel] = useState<Account | null>(null);
  const accounts = state?.accounts ?? [];
  return (
    <>
      <div className="col" style={{ gap: 10 }}>
        {accounts.map((a) => {
          const s = sync[a.id];
          const on = a.id === state?.account;
          return (
            <div key={a.id} className={clsx("st-acc", on && "on")}>
              <div className="st-acc-icon">{a.kind === "xtream" ? <KeyRound size={17} /> : <Download size={17} />}</div>
              <div className="st-acc-text">
                <div className="st-acc-name">{a.name} {on && <Badge tone="accent">Actif</Badge>}</div>
                <div className="muted st-small">
                  {a.kind === "xtream" ? "Xtream Codes" : "Liste M3U"}
                  {a.counts && Object.keys(a.counts).length ? ` · ${(a.counts.live ?? 0).toLocaleString("fr-FR")} chaînes · ${(a.counts.movie ?? 0).toLocaleString("fr-FR")} films${a.kind === "xtream" ? ` · ${(a.counts.series ?? 0).toLocaleString("fr-FR")} séries` : ""}` : ""}
                  {a.status?.expires ? ` · expire le ${new Date(a.status.expires * 1000).toLocaleDateString("fr-FR")}` : ""}
                  {a.status?.max ? ` · ${a.status.max} connexion${a.status.max > 1 ? "s" : ""}` : ""}
                </div>
                <div className="muted st-small">Catalogue : {fmtDate(a.synced)} · Guide : {fmtDate(a.epg_synced)}</div>
                {a.status?.problem && <div className="st-err st-small">{a.status.problem}</div>}
                {s && (<div className="st-small" style={{ marginTop: 6 }}>{s.label} · {Math.round(s.percent)} %<Bar value={s.percent / 100} /></div>)}
                {s?.warning && <div className="st-err st-small">{s.warning}</div>}
              </div>
              <div className="row" style={{ gap: 6 }}>
                {!on && <Button size="sm" onClick={() => stream.selectAccount(a.id).then(() => { useStream.getState().load(); useStream.getState().bump(); }).catch(toastError)}>Utiliser</Button>}
                <Button size="sm" iconOnly icon={<RefreshCw size={14} />} tip="Mettre à jour maintenant" disabled={!!s}
                  onClick={() => stream.syncAccount(a.id).then(() => toast({ kind: "info", title: "Mise à jour lancée" })).catch(toastError)} />
                <Button size="sm" iconOnly icon={<Pencil size={14} />} tip="Modifier" onClick={() => setEdit(a)} />
                <Button size="sm" iconOnly icon={<Trash2 size={14} />} tip="Supprimer" onClick={() => setDel(a)} />
              </div>
            </div>
          );
        })}
      </div>
      <Button icon={<Plus size={15} />} style={{ marginTop: 12 }} onClick={() => setAdding(true)}>Ajouter un abonnement</Button>
      <Modal open={adding} onClose={() => setAdding(false)} width={560} title="Ajouter un abonnement">
        <AccountForm compact onDone={() => setAdding(false)} />
      </Modal>
      <Modal open={!!edit} onClose={() => setEdit(null)} width={560} title="Modifier l'abonnement" eyebrow={edit?.name}>
        {edit && <AccountForm compact edit={edit} onDone={() => setEdit(null)} />}
      </Modal>
      <Modal open={!!del} onClose={() => setDel(null)} title="Supprimer cet abonnement ?" eyebrow={del?.name}
        actions={<><Button variant="ghost" onClick={() => setDel(null)}>Annuler</Button><Button variant="danger" onClick={async () => {
          await stream.deleteAccount(del!.id).catch(toastError);
          setDel(null);
          await useStream.getState().load();
          useStream.getState().bump();
        }}>Supprimer</Button></>}>
        <p>Ses identifiants, son catalogue, ses favoris et son historique seront effacés de ce PC. Les fichiers enregistrés restent.</p>
      </Modal>
    </>
  );
}

// ------------------------------------------------------------------ profils et contrôle parental
const COLORS = ["#7FA7AC", "#E9A29A", "#FF8A6B", "#8FB98B", "#B39DDB", "#F2C66D"];

export function ProfilesSettings() {
  const state = useStream((s) => s.state);
  const toast = useApp((s) => s.toast);
  const [form, setForm] = useState<{ id?: string; name: string; kids: boolean; color: string; pin: string } | null>(null);
  const [switching, setSwitching] = useState<Profile | null>(null);
  const [pin, setPin] = useState("");
  const [parental, setParental] = useState<{ cur: string; next: string } | null>(null);
  const [s, upd] = useStreamSettings();
  const reload = async () => { await useStream.getState().load(); useStream.getState().bump(); };

  const select = async (p: Profile, code = "") => {
    try {
      await stream.selectProfile(p.id, code);
      setSwitching(null);
      setPin("");
      await reload();
      toast({ kind: "success", title: `Profil « ${p.name} »` });
    } catch (e) {
      toastError(e);
    }
  };
  return (
    <>
      <div className="st-profiles">
        {state?.profiles.map((p) => (
          <div key={p.id} className={clsx("st-profile", p.id === state.profile && "on")}>
            <button className="st-profile-avatar" style={{ background: p.color || COLORS[0] }} onClick={() => (p.id === state.profile ? null : p.locked ? setSwitching(p) : select(p))}>
              {p.kids ? <Baby size={22} /> : <UserRound size={22} />}
              {p.locked && <Lock size={12} className="st-profile-lock" />}
            </button>
            <span className="st-profile-name">{p.name}</span>
            <span className="muted st-small">{p.id === state.profile ? "Actif" : p.kids ? "Enfant" : ""}</span>
            <div className="row" style={{ gap: 4 }}>
              <Button size="sm" iconOnly icon={<Pencil size={13} />} tip="Modifier" onClick={() => setForm({ id: p.id, name: p.name, kids: !!p.kids, color: p.color || COLORS[0], pin: "" })} />
              {p.id !== "main" && <Button size="sm" iconOnly icon={<Trash2 size={13} />} tip="Supprimer" onClick={() => stream.deleteProfile(p.id).then(reload).catch(toastError)} />}
            </div>
          </div>
        ))}
        <button className="st-profile st-profile-add" onClick={() => setForm({ name: "", kids: false, color: COLORS[1], pin: "" })}>
          <span className="st-profile-avatar"><Plus size={22} /></span>
          <span className="st-profile-name">Nouveau profil</span>
        </button>
      </div>
      <p className="muted st-small" style={{ marginTop: 10 }}>Chaque profil a ses favoris, son historique et ses reprises. Un profil enfant ne montre jamais de contenu adulte.</p>
      {s && (
        <>
          <Row title="Masquer les contenus adultes" desc="Les catégories « adulte / XXX / 18+ » sont cachées. Avec un code parental, on peut les afficher 2 heures.">
            <Switch checked={s.hide_adult} onChange={(v) => upd({ hide_adult: v })} />
          </Row>
          <Row title="Code parental" desc={s.parental_pin ? "Un code est demandé pour afficher les contenus adultes." : "Aucun code : n'importe qui peut les afficher."}>
            <Button size="sm" onClick={() => setParental({ cur: "", next: "" })}>{s.parental_pin ? "Changer" : "Choisir un code"}</Button>
          </Row>
        </>
      )}

      <Modal open={!!form} onClose={() => setForm(null)} title={form?.id ? "Modifier le profil" : "Nouveau profil"}
        actions={<><Button variant="ghost" onClick={() => setForm(null)}>Annuler</Button><Button variant="primary" disabled={!form?.name.trim()} onClick={async () => {
          try {
            if (form!.id) await stream.editProfile(form!.id, { name: form!.name.trim(), kids: form!.kids, color: form!.color });
            else await stream.addProfile({ name: form!.name.trim(), kids: form!.kids, color: form!.color, pin: form!.pin || undefined });
            setForm(null);
            reload();
          } catch (e) { toastError(e); }
        }}>Enregistrer</Button></>}>
        {form && (
          <div className="col" style={{ gap: 14, marginTop: 6 }}>
            <Field label="Nom"><input className="input" autoFocus value={form.name} maxLength={24} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Couleur">
              <div className="row" style={{ gap: 8 }}>
                {COLORS.map((c) => (
                  <button key={c} className={clsx("st-color", form.color === c && "on")} style={{ background: c }} onClick={() => setForm({ ...form, color: c })}>{form.color === c && <Check size={14} />}</button>
                ))}
              </div>
            </Field>
            <Row title="Profil enfant" desc="Jamais de contenu adulte, même avec le code parental."><Switch checked={form.kids} onChange={(v) => setForm({ ...form, kids: v })} /></Row>
            {!form.id && <Field label="Code pour ouvrir ce profil (optionnel)" hint="4 à 8 chiffres"><PinField value={form.pin} onChange={(v) => setForm({ ...form, pin: v })} /></Field>}
          </div>
        )}
      </Modal>
      <Modal open={!!switching} onClose={() => setSwitching(null)} title={`Ouvrir « ${switching?.name} »`} eyebrow={<><Lock size={12} /> Profil protégé</>}
        actions={<><Button variant="ghost" onClick={() => setSwitching(null)}>Annuler</Button><Button variant="primary" onClick={() => select(switching!, pin)}>Ouvrir</Button></>}>
        <form onSubmit={(e) => { e.preventDefault(); select(switching!, pin); }} style={{ marginTop: 10 }}><PinField value={pin} onChange={setPin} autoFocus /></form>
      </Modal>
      <Modal open={!!parental} onClose={() => setParental(null)} title="Code parental"
        actions={<>
          {s?.parental_pin && <Button variant="ghost" onClick={async () => { try { await stream.parentalPin(parental!.cur, ""); setParental(null); upd({}); toast({ kind: "success", title: "Code parental retiré" }); } catch (e) { toastError(e); } }}>Retirer le code</Button>}
          <Button variant="primary" disabled={(parental?.next.length ?? 0) < 4} onClick={async () => {
            try { await stream.parentalPin(parental!.cur, parental!.next); setParental(null); upd({}); toast({ kind: "success", title: "Code parental enregistré" }); } catch (e) { toastError(e); }
          }}>Enregistrer</Button>
        </>}>
        {parental && (
          <div className="col" style={{ gap: 14, marginTop: 6 }}>
            {s?.parental_pin && <Field label="Code actuel"><PinField value={parental.cur} onChange={(v) => setParental({ ...parental, cur: v })} autoFocus /></Field>}
            <Field label="Nouveau code" hint="4 à 8 chiffres"><PinField value={parental.next} onChange={(v) => setParental({ ...parental, next: v })} autoFocus={!s?.parental_pin} /></Field>
          </div>
        )}
      </Modal>
    </>
  );
}

// ------------------------------------------------------------------ lecture
export function PlaybackSettings() {
  const [s, upd] = useStreamSettings();
  const general = useApp((x) => x.settings?.general);
  const saveApp = useApp((x) => x.saveSettings);
  if (!s) return null;
  return (
    <>
      <Row title="Page d'ouverture" desc="Ce que se7en IPTV Player affiche quand tu l'ouvres.">
        <Segmented value={(general?.startup_page as string) ?? "home"} onChange={(v) => saveApp({ general: { startup_page: v as any } }).catch(toastError)}
          options={[{ value: "home", label: "Accueil" }, { value: "live", label: "TV" }, { value: "guide", label: "Guide" }, { value: "last", label: "Dernière" }]} />
      </Row>
      <Row title="Format des chaînes" desc="MPEG-TS démarre plus vite ; HLS passe mieux sur certains réseaux. S'applique aux abonnements Xtream qui laissent le choix.">
        <Segmented value={s.output} onChange={(v) => upd({ output: v })} options={[{ value: "ts", label: "MPEG-TS" }, { value: "m3u8", label: "HLS" }]} />
      </Row>
      <Row title="Zapping instantané" desc="La chaîne suivante est ouverte d'avance, en silence : ↑ ↓ l'affichent tout de suite. Utilise une connexion de plus, seulement si ton abonnement en a au moins 2 ; un enregistrement passe toujours avant.">
        <Switch checked={s.instant_zap} onChange={(v) => upd({ instant_zap: v })} />
      </Row>
      <Row title="Regrouper SD / HD / FHD / 4K" desc="Une seule ligne par chaîne ou par film ; les autres versions se choisissent dans sa fiche ou sous la vidéo.">
        <Switch checked={s.group_quality} onChange={(v) => upd({ group_quality: v })} />
      </Row>
      {s.group_quality && (
        <Row title="Qualité préférée" desc="La version lancée d'office. En dessous de la meilleure, on prend la plus proche (utile avec une petite connexion).">
          <Segmented value={s.quality_pref} onChange={(v) => upd({ quality_pref: v as StreamSettings["quality_pref"] })}
            options={[{ value: "best", label: "Meilleure" }, { value: "fhd", label: "FHD" }, { value: "hd", label: "HD" }, { value: "sd", label: "SD" }]} />
        </Row>
      )}
      <Row title="Décodage matériel" desc="La carte graphique décode la vidéo (moins de processeur, 4K fluide). Désactive-le si l'image a des défauts.">
        <Switch checked={s.hwdec !== "no"} onChange={(v) => upd({ hwdec: v ? "auto-safe" : "no" })} />
      </Row>
      <Row title="Mémoire tampon" desc={`${s.buffer_s} s d'avance : plus haut si le flux coupe souvent, plus bas pour zapper plus vite.`}>
        <input type="range" min={2} max={60} value={s.buffer_s} onChange={(e) => upd({ buffer_s: Number(e.target.value) })} style={{ width: 200 }} />
      </Row>
      <Row title="Langues audio préférées" desc="Dans l'ordre, codes séparés par des virgules (fr, en, ar…).">
        <input className="input mono" defaultValue={s.audio_lang} onBlur={(e) => e.target.value !== s.audio_lang && upd({ audio_lang: e.target.value })} style={{ width: 220 }} />
      </Row>
      <Row title="Langues des sous-titres" desc="Laisse vide pour ne jamais les afficher d'office.">
        <input className="input mono" defaultValue={s.sub_lang} onBlur={(e) => e.target.value !== s.sub_lang && upd({ sub_lang: e.target.value })} style={{ width: 220 }} />
      </Row>
      <Row title="User-Agent" desc="Seulement si ton fournisseur l'exige. Chaque abonnement peut aussi avoir le sien.">
        <input className="input mono" defaultValue={s.user_agent} onBlur={(e) => e.target.value !== s.user_agent && upd({ user_agent: e.target.value })} style={{ width: 220 }} />
      </Row>
      <TmdbRow on={!!s.tmdb} />
    </>
  );
}

/** Your own (free) TMDB key: French summaries, cast photos, backdrops and trailers on film and series sheets. */
function TmdbRow({ on }: { on: boolean }) {
  const [has, setHas] = useState(on);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => setHas(on), [on]);
  const save = async (k: string) => {
    setBusy(true);
    try {
      const r = await stream.tmdbKey(k);
      setHas(r.tmdb);
      setKey("");
      useApp.getState().toast({ kind: "success", title: r.tmdb ? "Fiches enrichies activées" : "Clé TMDB retirée" });
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Row title="Fiches enrichies (TMDB)"
      desc={has ? "Activées : résumés en français, photos des acteurs, fonds et bandes-annonces viennent de TMDB." :
        <>Optionnel : crée un compte gratuit sur <b>themoviedb.org</b>, puis copie ta « clé d'API » (Paramètres → API). Sans clé, les fiches utilisent les infos de ton fournisseur.</>}>
      {has ? (
        <Button size="sm" disabled={busy} onClick={() => save("")}>Retirer la clé</Button>
      ) : (
        <div className="row" style={{ gap: 8 }}>
          <input className="input mono" type="password" placeholder="Clé d'API TMDB" value={key} onChange={(e) => setKey(e.target.value)} style={{ width: 200 }} autoComplete="off" />
          <Button size="sm" variant="primary" loading={busy} disabled={!key.trim()} onClick={() => save(key.trim())}>Activer</Button>
        </div>
      )}
    </Row>
  );
}

// ------------------------------------------------------------------ enregistrements et guide
export function RecordingSettings() {
  const [s, upd] = useStreamSettings();
  if (!s) return null;
  return (
    <>
      <Row title="Dossier des enregistrements" desc={s.record_folder || s.record_default}>
        <div className="row" style={{ gap: 6 }}>
          <Button size="sm" icon={<FolderOpen size={14} />} onClick={async () => {
            const r = await api.post<{ path: string | null }>("/api/system/pick-folder", { initial: s.record_folder || s.record_default }).catch(() => null);
            if (r?.path) upd({ record_folder: r.path });
          }}>Changer</Button>
          {s.record_folder && <Button size="sm" variant="ghost" onClick={() => upd({ record_folder: "" })}>Par défaut</Button>}
        </div>
      </Row>
      <Row title="Marges des enregistrements programmés" desc={`Démarre ${s.margin_before} min avant et finit ${s.margin_after} min après le programme (les horaires sont rarement exacts).`}>
        <div className="row" style={{ gap: 8 }}>
          <input className="input mono" type="number" min={0} max={30} value={s.margin_before} onChange={(e) => upd({ margin_before: Number(e.target.value) })} style={{ width: 70 }} />
          <input className="input mono" type="number" min={0} max={60} value={s.margin_after} onChange={(e) => upd({ margin_after: Number(e.target.value) })} style={{ width: 70 }} />
        </div>
      </Row>
      <Row title="Mettre à jour au démarrage" desc="Le catalogue et le guide se rafraîchissent en arrière-plan à l'ouverture.">
        <Switch checked={s.refresh_on_start} onChange={(v) => upd({ refresh_on_start: v })} />
      </Row>
      <Row title="Mise à jour automatique" desc={`Toutes les ${s.refresh_hours} h tant que l'appli est ouverte.`}>
        <Segmented value={String(s.refresh_hours)} onChange={(v) => upd({ refresh_hours: Number(v) })} options={["6", "12", "24", "48"].map((h) => ({ value: h, label: `${h} h` }))} />
      </Row>
    </>
  );
}

// ------------------------------------------------------------------ sauvegarde
export function BackupSettings() {
  const toast = useApp((s) => s.toast);
  const [mode, setMode] = useState<"export" | "import" | null>(null);
  const [path, setPath] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const start = async (m: "export" | "import") => {
    setPw("");
    if (m === "export") {
      const r = await api.post<{ path: string | null }>("/api/system/pick-folder", {}).catch(() => null);
      if (!r?.path) return;
      setPath(`${r.path}\\se7en IPTV Player - sauvegarde ${new Date().toISOString().slice(0, 10)}.s7s`);
    } else {
      const r = await api.post<{ paths: string[] }>("/api/system/pick-files", { kind: "backup", multiple: false }).catch(() => null);
      if (!r?.paths?.[0]) return;
      setPath(r.paths[0]);
    }
    setMode(m);
  };
  const go = async () => {
    setBusy(true);
    try {
      if (mode === "export") {
        const r = await stream.exportBackup(path, pw);
        toast({ kind: "success", title: "Sauvegarde créée", body: r.path });
      } else {
        const r = await stream.importBackup(path, pw);
        toast({ kind: "success", title: "Sauvegarde restaurée", body: r.accounts ? `${r.accounts} abonnement(s) ajouté(s)` : "Rien de nouveau à ajouter." });
        await useStream.getState().load();
        useStream.getState().bump();
      }
      setMode(null);
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <p className="muted st-small">Un fichier chiffré avec ton mot de passe : abonnements (identifiants compris), profils, favoris, historique et réglages. Pratique pour changer de PC.</p>
      <div className="row" style={{ gap: 10, marginTop: 12 }}>
        <Button icon={<Download size={15} />} onClick={() => start("export")}>Créer une sauvegarde</Button>
        <Button icon={<Upload size={15} />} onClick={() => start("import")}>Restaurer</Button>
      </div>
      <Modal open={!!mode} onClose={() => setMode(null)} title={mode === "export" ? "Créer une sauvegarde" : "Restaurer une sauvegarde"} eyebrow={path.split("\\").pop()}
        actions={<><Button variant="ghost" onClick={() => setMode(null)}>Annuler</Button><Button variant="primary" loading={busy} disabled={pw.length < (mode === "export" ? 6 : 1)} onClick={go}>{mode === "export" ? "Créer" : "Restaurer"}</Button></>}>
        <form onSubmit={(e) => { e.preventDefault(); go(); }} style={{ marginTop: 10 }}>
          <Field label="Mot de passe de la sauvegarde" hint={mode === "export" ? "Au moins 6 caractères. Sans lui, la sauvegarde est illisible : note-le." : undefined}>
            <input className="input" type="password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" />
          </Field>
        </form>
      </Modal>
    </>
  );
}

// ------------------------------------------------------------------ télécommande (téléphone)
function Qr({ text, size = 176 }: { text: string; size?: number }) {
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  const n = qr.getModuleCount();
  let d = "";
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (qr.isDark(y, x)) d += `M${x} ${y}h1v1h-1z`;
  return (
    <svg className="st-qr" width={size} height={size} viewBox={`-2 -2 ${n + 4} ${n + 4}`} role="img" aria-label="QR code de la télécommande" shapeRendering="crispEdges">
      <rect x={-2} y={-2} width={n + 4} height={n + 4} fill="#fff" />
      <path d={d} fill="#0b0b0e" />
    </svg>
  );
}

export function RemoteSettings() {
  const [r, setR] = useState<RemoteInfo | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    stream.remote().then(setR).catch(toastError);
  }, []);
  const set = async (enabled: boolean, reset = false) => {
    setBusy(true);
    try {
      const n = await stream.setRemote(enabled, reset);
      setR(n);
      if (enabled && !n.enabled) useApp.getState().toast({ kind: "error", title: n.error || "La télécommande n'a pas pu démarrer." });
    } catch (e) {
      toastError(e);
    } finally {
      setBusy(false);
    }
  };
  if (!r) return null;
  return (
    <>
      <Row title="Télécommande sur le téléphone" desc="Zappe, règle le son, mets en pause et choisis une chaîne depuis ton téléphone, sur le même Wi-Fi que ce PC. Rien à installer : le téléphone ouvre une page web.">
        <Switch checked={r.enabled} disabled={busy} onChange={(v) => set(v)} />
      </Row>
      {r.enabled && (
        <div className="st-remote">
          <Qr text={r.url} />
          <div className="col" style={{ gap: 8, minWidth: 0 }}>
            <b>Scanne ce code avec l'appareil photo du téléphone</b>
            <span className="muted st-small">ou ouvre cette adresse dans son navigateur :</span>
            <code className="st-remote-url">{r.url}</code>
            <span className="muted st-small">
              Le lien contient une clé secrète : garde-le pour toi. Si Windows demande l'accès au réseau, autorise-le sur les
              réseaux privés.
            </span>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <Button size="sm" onClick={() => navigator.clipboard?.writeText(r.url).then(() => useApp.getState().toast({ kind: "success", title: "Lien copié" }))}>Copier le lien</Button>
              <Button size="sm" disabled={busy} onClick={() => set(true, true)}>Nouveau lien (l'ancien ne marchera plus)</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

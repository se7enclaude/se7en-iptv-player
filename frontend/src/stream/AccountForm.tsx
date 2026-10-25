import { CheckCircle2, Eye, EyeOff, FolderOpen, KeyRound, ListVideo, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { Button, Field, Segmented } from "../components/ui";
import { useApp } from "../store/app";
import { Account, AccountForm as Form, TestResult, stream } from "./api";
import { useStream } from "./store";

const EMPTY: Form = { kind: "xtream", name: "", server: "", username: "", password: "", m3u_url: "", epg_url: "", output: "ts", user_agent: "" };

function fmtDate(ts?: number | null): string {
  if (!ts) return "illimité";
  return new Date(ts * 1000).toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
}

export function Summary({ r }: { r: TestResult }) {
  const n = (v?: number | null) => (v == null ? "—" : v.toLocaleString("fr-FR"));
  return (
    <div className="st-summary rise">
      <div className="row" style={{ gap: 8 }}>
        <CheckCircle2 size={18} className="st-ok" />
        <strong>{r.kind === "m3u" ? "Liste lue" : r.problem ? r.problem : "Connexion réussie"}</strong>
      </div>
      <div className="st-summary-grid">
        <span>Chaînes</span><b>{n(r.counts.live)}</b>
        <span>Films</span><b>{n(r.counts.movie)}</b>
        {r.kind === "xtream" && (<><span>Séries</span><b>{n(r.counts.series)}</b></>)}
        {r.kind === "xtream" && (<><span>Expire le</span><b>{fmtDate(r.expires)}{r.trial ? " (essai)" : ""}</b></>)}
        {r.kind === "xtream" && (<><span>Connexions</span><b>{r.max == null ? "—" : `${r.active ?? 0} / ${r.max}`}</b></>)}
      </div>
    </div>
  );
}

/** Add (or edit) a subscription: Xtream Codes or M3U list, tested before it is saved. */
export function AccountForm({ edit, onDone, compact }: { edit?: Account; onDone?: (a: Account) => void; compact?: boolean }) {
  const toast = useApp((s) => s.toast);
  const [f, setF] = useState<Form>(() => (edit ? { ...EMPTY, ...edit, password: "" } as Form : EMPTY));
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState<"" | "test" | "save">("");
  const [err, setErr] = useState<{ message: string; hint?: string } | null>(null);
  const [res, setRes] = useState<TestResult | null>(null);
  const [more, setMore] = useState(false);
  const set = (p: Partial<Form>) => {
    setF({ ...f, ...p });
    setRes(null);
    setErr(null);
  };
  const ready = f.kind === "xtream" ? f.server.trim() && f.username.trim() && (f.password || edit?.has_password) : f.m3u_url.trim();

  const body = (): Partial<Form> => {
    const b: Partial<Form> = { ...f };
    if (edit && !f.password) delete b.password;
    return b;
  };
  const test = async () => {
    setBusy("test");
    setErr(null);
    try {
      if (edit && !f.password && f.kind === "xtream") setRes(null);
      else setRes(await stream.testAccount(body()));
    } catch (e: any) {
      setErr({ message: e?.message ?? String(e), hint: e?.hint });
    } finally {
      setBusy("");
    }
  };
  const save = async () => {
    setBusy("save");
    setErr(null);
    try {
      const a = edit ? await stream.editAccount(edit.id, body()) : (await stream.addAccount(body())).account;
      toast({ kind: "success", title: edit ? "Abonnement modifié" : "Abonnement ajouté", body: "Le catalogue se charge en arrière-plan." });
      await useStream.getState().load();
      onDone?.(a);
    } catch (e: any) {
      setErr({ message: e?.message ?? String(e), hint: e?.hint });
    } finally {
      setBusy("");
    }
  };
  const pickM3u = async () => {
    const r = await api.post<{ paths: string[] }>("/api/system/pick-files", { kind: "m3u", multiple: false }).catch(() => null);
    if (r?.paths?.[0]) set({ m3u_url: r.paths[0] });
  };

  return (
    <form className="col st-form" style={{ gap: 14 }} onSubmit={(e) => { e.preventDefault(); save(); }}>
      {!edit && (
        <Segmented
          value={f.kind}
          onChange={(k) => set({ kind: k as Form["kind"] })}
          options={[
            { value: "xtream", label: "Xtream Codes", icon: <KeyRound size={14} /> },
            { value: "m3u", label: "Liste M3U", icon: <ListVideo size={14} /> },
          ]}
        />
      )}
      {f.kind === "xtream" ? (
        <>
          <Field label="Serveur" hint={compact ? undefined : "L'adresse donnée par ton fournisseur, par exemple http://exemple.com:8080"}>
            <input className="input" value={f.server} onChange={(e) => set({ server: e.target.value })} placeholder="http://…" autoComplete="off" spellCheck={false} />
          </Field>
          <div className="st-form-2">
            <Field label="Nom d'utilisateur">
              <input className="input" value={f.username} onChange={(e) => set({ username: e.target.value })} autoComplete="off" spellCheck={false} />
            </Field>
            <Field label="Mot de passe">
              <div className="st-pass">
                <input className="input" type={show ? "text" : "password"} value={f.password} onChange={(e) => set({ password: e.target.value })}
                  placeholder={edit?.has_password ? "inchangé" : ""} autoComplete="new-password" spellCheck={false} />
                <button type="button" className="st-pass-eye" onClick={() => setShow(!show)} aria-label={show ? "Masquer" : "Afficher"}>
                  {show ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </Field>
          </div>
        </>
      ) : (
        <Field label="Lien ou fichier de la liste" hint={compact ? undefined : "Un lien http(s) vers ta liste .m3u / .m3u8, ou un fichier sur ton PC"}>
          <div className="row" style={{ gap: 8 }}>
            <input className="input" style={{ flex: 1 }} value={f.m3u_url} onChange={(e) => set({ m3u_url: e.target.value })} placeholder="https://… ou C:\…" spellCheck={false} />
            <Button type="button" icon={<FolderOpen size={15} />} onClick={pickM3u}>Fichier</Button>
          </div>
        </Field>
      )}
      <button type="button" className="st-link" onClick={() => setMore(!more)}>{more ? "Moins d'options" : "Plus d'options (nom, guide XMLTV, format…)"}</button>
      {more && (
        <div className="col rise" style={{ gap: 14 }}>
          <Field label="Nom affiché">
            <input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder={f.kind === "xtream" ? "Mon abonnement" : "Ma liste"} />
          </Field>
          <Field label="Guide des programmes (XMLTV)" hint="Optionnel : remplace le guide fourni par le serveur">
            <input className="input" value={f.epg_url} onChange={(e) => set({ epg_url: e.target.value })} placeholder="https://…/epg.xml.gz" spellCheck={false} />
          </Field>
          {f.kind === "xtream" && (
            <Field label="Format des chaînes">
              <Segmented value={f.output} onChange={(v) => set({ output: v })} options={[{ value: "ts", label: "MPEG-TS (conseillé)" }, { value: "m3u8", label: "HLS" }]} />
            </Field>
          )}
          <Field label="User-Agent" hint="À remplir seulement si ton fournisseur en demande un précis">
            <input className="input" value={f.user_agent} onChange={(e) => set({ user_agent: e.target.value })} spellCheck={false} />
          </Field>
        </div>
      )}
      {err && (
        <div className="alert alert-error">
          <div>
            <div className="alert-title">{err.message}</div>
            {err.hint && <div className="alert-body">{err.hint}</div>}
          </div>
        </div>
      )}
      {res && <Summary r={res} />}
      <div className="row" style={{ gap: 10, justifyContent: "flex-end", flexWrap: "wrap" }}>
        <Button type="button" disabled={!ready} loading={busy === "test"} onClick={test}>Tester</Button>
        <Button type="submit" variant="primary" disabled={!ready} loading={busy === "save"}>{edit ? "Enregistrer" : "Ajouter l'abonnement"}</Button>
      </div>
      {!compact && (
        <p className="st-fine"><ShieldCheck size={13} /> Tes identifiants sont chiffrés par Windows et ne sont envoyés qu'au serveur de ton abonnement.</p>
      )}
    </form>
  );
}

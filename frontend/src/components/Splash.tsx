import { useEffect, useRef, useState } from "react";
import { useT } from "../i18n";
import "./splash-stream.css";

/* ---------------------------------------------------------------------------
   se7en IPTV Player launch animation — « allumage de la télé », silent, about 2.3 s:
   a thin line of light flashes across the black (an old screen switching on) →
   it opens into a liquid-glass screen full of static → a channel number ticks →
   the static clears on the se7en IPTV Player wordmark and a red LIVE dot starts to
   pulse. On exit the screen collapses back into a line, then a dot.
   Click (or any key) to skip once the engine is ready.
   --------------------------------------------------------------------------- */

const HOLD = 2300; // the wordmark is on screen: the splash may leave from here (once the engine is ready)
const OUT = 620; // exit (screen switching off)

function Static() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const w = (c.width = 160);
    const h = (c.height = 90);
    const img = ctx.createImageData(w, h);
    let raf = 0;
    const draw = () => {
      const d = img.data;
      for (let i = 0; i < d.length; i += 4) {
        const v = (Math.random() * 255) | 0;
        d[i] = d[i + 1] = d[i + 2] = v;
        d[i + 3] = 255;
      }
      ctx.putImageData(img, 0, 0);
      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, []);
  return <canvas ref={ref} className="sps-static" />;
}

export function Splash({ onDone, version, ready }: { onDone: () => void; version?: string; ready?: boolean }) {
  const t = useT();
  const [leaving, setLeaving] = useState(false);
  const [held, setHeld] = useState(false);
  const [ch, setCh] = useState(1);
  const done = useRef(false);
  const readyRef = useRef(ready);
  readyRef.current = ready;

  const leave = () => {
    if (done.current) return;
    done.current = true;
    setLeaving(true);
    window.setTimeout(onDone, OUT);
  };

  useEffect(() => {
    const id = window.setTimeout(() => setHeld(true), HOLD);
    // the channel number runs while the static is on, and lands on 7
    const ticks = [2, 4, 3, 5, 6, 7].map((n, i) => window.setTimeout(() => setCh(n), 760 + i * 95));
    const key = () => readyRef.current && leave();
    window.addEventListener("keydown", key);
    return () => {
      window.clearTimeout(id);
      ticks.forEach(window.clearTimeout);
      window.removeEventListener("keydown", key);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (held && ready) leave();
  }, [held, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className={`splash sps ${leaving ? "leaving" : ""}`} onClick={() => ready && leave()}>
      <div className="sps-glow" />
      <div className="sps-screen">
        <Static />
        <div className="sps-scan" />
        <span className="sps-ch">CH {String(ch).padStart(2, "0")}</span>
        <div className="sps-brand">
          <span className="sps-word">se<span className="sps-seven">7</span>en</span>
          <span className="sps-app">IPTV Player</span>
        </div>
        <span className="sps-live"><i /> EN DIRECT</span>
      </div>
      <div className="sps-tag">{t("splash.tagline")}</div>
      <div className="sps-foot">
        <span>v{version ?? ""}</span>
        <span className="sps-bar"><span className={ready ? "full" : ""} /></span>
        <span>{ready ? t("splash.skip") : t("splash.init")}</span>
      </div>
    </div>
  );
}

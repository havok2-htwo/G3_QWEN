export function LandingPage() {
  return (
    <main className="app-shell">
      <section className="hero-card hero-landing">
        <div className="hero-layout">
          <div className="hero-copy">
            <p className="eyebrow">Powered by SONS</p>
            <h1>G3 QWEN TTS</h1>
            <p>
              Eine aufgeraeumte Front fuer QWEN mit offenem Demo-Client, einem einzigen Adminpanel
              und einer Scheduler-Logik fuer sauberes Satz-Batching auf genau einem GPU-Worker.
            </p>
            <div className="hero-actions">
              <a className="primary-button" href="/demo">
                Demo oeffnen
              </a>
              <a className="secondary-button" href="/admin">
                Adminpanel
              </a>
              <a className="ghost-button" href="/docs">
                API Docs
              </a>
            </div>
          </div>

          <aside className="hero-bubble">
            <p className="eyebrow">QWEN</p>
            <h2>Ein klarer Einstieg statt Kartenfriedhof</h2>
            <p>
              Demo fuer Nutzer, Admin fuer Betrieb. Sonst nichts. Stimmen, Modelle, Queue und Key-Rotation
              liegen gesammelt im Control Panel.
            </p>
          </aside>
        </div>

        <div className="status-row">
          <div className="status-pill">
            <span>Public Demo</span>
            <strong>ohne API-Key</strong>
          </div>
          <div className="status-pill">
            <span>Adminschutz</span>
            <strong>X-Admin-Key</strong>
          </div>
          <div className="status-pill">
            <span>Inference</span>
            <strong>1 GPU-Worker + Batch Scheduler</strong>
          </div>
        </div>
      </section>
    </main>
  );
}

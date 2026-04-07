from pathlib import Path

base = Path(__file__).resolve().parent
idx = (base / "index.html").read_text(encoding="utf-8")
frag = (base / "_risk_dialog_fragment.html").read_text(encoding="utf-8")
start = idx.index('    <div id="dlg-risk"')
end = idx.index('  <script src="/assets/js/trading-ui.js"', start)
new = idx[:start] + frag + "\n  <script src=\"/assets/js/risk-modal.js\" defer></script>\n  " + idx[end:]
(base / "index.html").write_text(new, encoding="utf-8")
print("patched", len(new))

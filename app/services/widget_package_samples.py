"""HTML/JS samples for built-in WidgetPackages.

The frontend keeps an identical-looking copy in
``testing-agent-frontend/src/pages/WidgetPackagesPage.tsx::EXAMPLE_PACKAGE_HTML``
so that the «Insert example» button in the package editor offers the
same starting point as the seeded demo package.

If you change the HTML here, mirror it in the frontend (or vice versa).
The two copies drift slowly because they target different audiences:

  - This file is the runtime source for `example-kpi-v1` shipped with
    every workspace via :func:`app.seed.seed_demo_widget_packages`.
  - The frontend copy is what a user sees when they click «Вставить
    пример» in the package editor — they may then customize and save
    under a different code.

Keeping them in sync is intentional, not enforced.
"""

# Minimal "hello, widget" KPI: shows the last value of the first series
# big and centered, plus the widget title underneath. Inline CSS only,
# no external dependencies — runs inside the iframe sandbox.
EXAMPLE_KPI_HTML = """<!doctype html>
<html>
<head>
<style>
  html, body { margin: 0; padding: 0; font-family: ui-sans-serif, system-ui, sans-serif; }
  body { display: flex; flex-direction: column; align-items: center;
         justify-content: center; height: 100vh; color: #222; }
  .value { font-size: 48px; font-weight: 700; letter-spacing: -1px; }
  .label { opacity: 0.7; font-size: 13px; margin-top: 4px; }
  @media (prefers-color-scheme: dark) {
    body { color: #eaeaea; background: transparent; }
  }
</style>
</head>
<body>
  <div class="value" id="v">—</div>
  <div class="label" id="l">загрузка…</div>
<script>
  window.render = function (payload) {
    var widget = payload.widget;
    var data = payload.data;
    var vals = (data.series && data.series[0] && data.series[0].data) || [];
    var last = vals.length ? vals[vals.length - 1] : null;
    document.getElementById("v").textContent = last == null ? "—" : last.toLocaleString("ru-RU");
    document.getElementById("l").textContent = widget.title;
  };
</script>
</body>
</html>"""


# Manifest sidecar shipped with the example package. Both fields are
# decorative for now (allowed_sources isn't enforced; config_fields
# isn't rendered as a form), but populating them sets a precedent for
# future packages and gives the user a worked example to copy.
EXAMPLE_KPI_MANIFEST = {
    "allowed_sources": ["*"],
    "config_fields": [],
}

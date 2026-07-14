# Grafana for EDA TopoView: the two required plugins are baked IN (so the app
# installs on an air-gapped cluster with no grafana.com access), and Grafana's
# chrome is hidden so users only see the fabric menu + dashboards.
FROM grafana/grafana:12.0.2
LABEL org.opencontainers.image.source="https://github.com/kkayhan/edaapp_grafana"
LABEL org.opencontainers.image.description="Grafana for EDA TopoView — flow-panel + infinity baked in (air-gap ready), chrome hidden"
LABEL org.opencontainers.image.license="AGPL-3.0-only"
USER root
# Bake plugins into a path OUTSIDE /var/lib/grafana (the Deployment mounts an
# emptyDir there, which would otherwise shadow the default plugin dir).
ENV GF_PATHS_PLUGINS=/opt/grafana-plugins-bundled
RUN mkdir -p "$GF_PATHS_PLUGINS" && \
    grafana-cli --pluginsDir "$GF_PATHS_PLUGINS" plugins install andrewbmchugh-flow-panel && \
    grafana-cli --pluginsDir "$GF_PATHS_PLUGINS" plugins install yesoreyeram-infinity-datasource && \
    chown -R 472:0 "$GF_PATHS_PLUGINS" && chmod -R g+rX "$GF_PATHS_PLUGINS"
# Inject the chrome-hiding CSS into the served index.html (before </head>).
COPY topoview-kiosk.css /tmp/topoview-kiosk.css
RUN INDEX=/usr/share/grafana/public/views/index.html && \
    { echo '<style>'; cat /tmp/topoview-kiosk.css; echo '</style>'; } > /tmp/inject.html && \
    awk '/<\/head>/{while((getline l < "/tmp/inject.html")>0) print l; close("/tmp/inject.html")} {print}' "$INDEX" > "$INDEX.new" && \
    mv "$INDEX.new" "$INDEX" && \
    grep -q "TopoView kiosk" "$INDEX" && echo "CSS injected OK" && \
    rm -f /tmp/inject.html /tmp/topoview-kiosk.css
USER 472

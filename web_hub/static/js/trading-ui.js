/**
 * تبويبات + شارت — انتظار تحميل LightweightCharts (يتأخر أحياناً عن defer)
 */
(function () {
  function waitForLWC(done, left) {
    if (typeof LightweightCharts !== "undefined") {
      done(null);
      return;
    }
    if (left <= 0) {
      done(new Error("تعذر تحميل مكتبة الشارت — تحقق من الإنترنت أو حجب CDN"));
      return;
    }
    setTimeout(function () {
      waitForLWC(done, left - 1);
    }, 100);
  }

  function addCandleSeries(chart) {
    if (typeof chart.addCandlestickSeries === "function") {
      return chart.addCandlestickSeries({
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderVisible: false,
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
      });
    }
    if (LightweightCharts.CandlestickSeries && typeof chart.addSeries === "function") {
      return chart.addSeries(LightweightCharts.CandlestickSeries, {
        upColor: "#22c55e",
        downColor: "#ef4444",
        borderVisible: false,
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
      });
    }
    throw new Error("واجهة مكتبة الشارت غير مدعومة");
  }

  var SYMBOLS_DEFAULT = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
  ];

  function applyRiskPillFromStorage() {
    if (window.CryptoWebHubRisk) {
      window.CryptoWebHubRisk.updateQuickPills(window.CryptoWebHubRisk.loadMerged());
    }
  }

  function initHubDialogs() {
    var root = document.getElementById("modal-root");
    var dlgS = document.getElementById("dlg-settings");
    var dlgR = document.getElementById("dlg-risk");
    if (!root || !dlgS || !dlgR) return;

    function closeModals() {
      root.classList.add("is-hidden");
      root.setAttribute("hidden", "");
      root.setAttribute("aria-hidden", "true");
      dlgS.classList.add("is-hidden");
      dlgR.classList.add("is-hidden");
    }

    function openModal(which) {
      dlgS.classList.toggle("is-hidden", which !== "settings");
      dlgR.classList.toggle("is-hidden", which !== "risk");
      root.classList.remove("is-hidden");
      root.removeAttribute("hidden");
      root.setAttribute("aria-hidden", "false");
    }

    root.querySelectorAll("[data-close-modal]").forEach(function (el) {
      el.addEventListener("click", closeModals);
    });

    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !root.classList.contains("is-hidden")) {
        closeModals();
      }
    });

    var btnSet = document.getElementById("btn-settings");
    if (btnSet) btnSet.addEventListener("click", function () { openModal("settings"); });

    if (window.CryptoWebHubRisk) {
      window.CryptoWebHubRisk.initRiskModal(dlgR, closeModals);
    }

    var btnRisk = document.getElementById("btn-risk-settings");
    if (btnRisk) {
      btnRisk.addEventListener("click", function () {
        if (window.CryptoWebHubRisk) {
          window.CryptoWebHubRisk.openRiskDialog(dlgR);
        }
        openModal("risk");
      });
    }

    var out = document.getElementById("settings-diag-out");
    var btnH = document.getElementById("btn-check-health");
    if (btnH && out) {
      btnH.addEventListener("click", async function () {
        out.textContent = "جاري…";
        try {
          var r = await fetch("/api/health");
          var t = await r.text();
          out.textContent = "GET /api/health → HTTP " + r.status + "\n" + t;
        } catch (e) {
          out.textContent = String((e && e.message) || e);
        }
      });
    }
    var btnB = document.getElementById("btn-check-binance");
    if (btnB && out) {
      btnB.addEventListener("click", async function () {
        out.textContent = "جاري…";
        try {
          var r = await fetch("/api/binance-check");
          var j = await r.json();
          out.textContent = JSON.stringify(j, null, 2);
        } catch (e) {
          out.textContent = String((e && e.message) || e);
        }
      });
    }
    var btnE = document.getElementById("btn-check-etoro");
    if (btnE && out) {
      btnE.addEventListener("click", async function () {
        out.textContent = "جاري…";
        try {
          var r = await fetch("/api/etoro/status");
          var j = await r.json();
          out.textContent = JSON.stringify(j, null, 2);
        } catch (e) {
          out.textContent = String((e && e.message) || e);
        }
      });
    }

    var balBtn = document.getElementById("btn-balance-refresh");
    if (balBtn) {
      balBtn.addEventListener("click", async function () {
        var lab = document.getElementById("balance-label");
        if (!lab) return;
        lab.textContent = "جاري…";
        try {
          var r = await fetch("/api/health");
          var j = await r.json();
          if (j && j.ok) lab.textContent = "الخادم: يعمل";
          else lab.textContent = "الخادم: رد غير متوقع";
        } catch (e) {
          lab.textContent = "لا اتصال بالخادم المحلي";
        }
      });
    }
  }

  function initTabs() {
    var nav = document.getElementById("tabs-nav");
    if (!nav) return;
    nav.querySelectorAll("button[data-tab]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var tab = btn.getAttribute("data-tab");
        nav.querySelectorAll("button[data-tab]").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
        document.querySelectorAll(".tab-panel").forEach(function (p) {
          p.classList.toggle("active", p.id === "tab-" + tab);
        });
        window.dispatchEvent(new Event("resize"));
        if (tab === "chart" && typeof window.__cwHubChartResize === "function") {
          setTimeout(window.__cwHubChartResize, 50);
          setTimeout(window.__cwHubChartResize, 200);
        }
      });
    });
  }

  function fillSymbolSelect(sel, symbols, preferred) {
    var cur = preferred || (sel.value || "BTCUSDT");
    sel.innerHTML = "";
    var found = false;
    symbols.forEach(function (s) {
      var o = document.createElement("option");
      o.value = s;
      o.textContent = s;
      if (s === cur) found = true;
      sel.appendChild(o);
    });
    if (!found && symbols.length) {
      sel.value = symbols[0];
    } else if (found) {
      sel.value = cur;
    }
  }

  function main() {
    initTabs();
    applyRiskPillFromStorage();
    initHubDialogs();

    var chartEl = document.getElementById("chart-container");
    var fallback = document.getElementById("chart-fallback");
    var selSymbol = document.getElementById("sel-symbol");
    var selInterval = document.getElementById("sel-interval");
    var selExchange = document.getElementById("sel-exchange");
    var symbolSearch = document.getElementById("symbol-search");
    var priceEl = document.getElementById("last-price");
    var statusEl = document.getElementById("chart-status");
    var modeEl = document.getElementById("mode-label");
    var exchangeLine = document.getElementById("exchange-line");
    var connLabel = document.getElementById("conn-label");
    var dayPctEl = document.getElementById("day-pct");
    var aiRec = document.getElementById("ai-rec");
    var aiConf = document.getElementById("ai-conf");
    var aiStrategy = document.getElementById("ai-strategy");
    var aiPositions = document.getElementById("ai-positions");
    var badgeLiq = document.getElementById("badge-liq");
    var badgeCb = document.getElementById("badge-cb");
    var marketIndBlock = document.getElementById("market-indicators-block");
    var summaryLivePre = document.getElementById("summary-live-pre");
    var botToggle = document.getElementById("btn-bot-toggle");
    var botStatusLine = document.getElementById("bot-status-line");

    function applyBotDemoUi(on) {
      if (modeEl) modeEl.textContent = on ? "البوت: ON (ويب — عرض فقط)" : "البوت: OFF (ويب)";
      if (botToggle) {
        botToggle.textContent = on ? "ON" : "OFF";
        botToggle.classList.toggle("toggle-off", !on);
        botToggle.classList.toggle("toggle-on", on);
      }
      if (botStatusLine) {
        botStatusLine.textContent = on
          ? "تجريبي: لا تنفيذ تداول من المتصفح — استخدم تطبيق سطح المكتب."
          : "البوت متوقف — واجهة ويب (عرض فقط)";
      }
    }
    var botDemoOn = false;
    try {
      botDemoOn = localStorage.getItem("cw_hub_bot_demo") === "1";
    } catch (e1) {}
    applyBotDemoUi(botDemoOn);
    if (botToggle) {
      botToggle.addEventListener("click", function () {
        botDemoOn = !botDemoOn;
        try {
          localStorage.setItem("cw_hub_bot_demo", botDemoOn ? "1" : "0");
        } catch (e2) {}
        applyBotDemoUi(botDemoOn);
      });
    }

    if (selExchange) {
      selExchange.removeAttribute("disabled");
      var etOpt = selExchange.querySelector('option[value="etoro"]');
      if (etOpt) {
        etOpt.disabled = false;
        etOpt.removeAttribute("disabled");
      }
    }

    if (!selSymbol || !selInterval) return;

    var UI_BUILD = "20260410";
    try {
      var hv = document.getElementById("hub-ui-ver");
      if (hv) hv.textContent = "UI " + UI_BUILD;
    } catch (e0) {}
    if (typeof console !== "undefined" && console.info) {
      console.info("[CryptoWeb Hub]", UI_BUILD);
    }

    var hubSnapPollId = null;
    function applySnapshotPanels(j, priceOverride, opts) {
      if (!j || !j.ok) return;
      var etoroHdr = opts && opts.etoroPrice;
      var px = priceOverride != null ? priceOverride : j.price;
      if (priceEl && px != null) {
        priceEl.textContent = Number(px).toLocaleString("en-US", {
          maximumFractionDigits: 8,
        });
      }
      if (dayPctEl && j.change_pct != null) {
        var p = Number(j.change_pct);
        dayPctEl.textContent = (p >= 0 ? "+" : "") + p.toFixed(2) + "%";
        dayPctEl.style.color = p >= 0 ? "#22c55e" : "#ef4444";
      }
      if (aiRec && j.suggestion_ar) {
        aiRec.textContent = j.suggestion_ar;
        var sug = j.suggestion_ar;
        if (sug.indexOf("حذر") !== -1 || sug.indexOf("تعذر") !== -1) {
          aiRec.style.color = "#f87171";
        } else if (sug.indexOf("إيجابي") !== -1 || sug.indexOf("شراء") !== -1) {
          aiRec.style.color = "#4ade80";
        } else if (sug.indexOf("بيع") !== -1) {
          aiRec.style.color = "#f87171";
        } else {
          aiRec.style.color = "";
        }
      }
      if (aiConf && j.confidence != null) {
        aiConf.textContent = String(j.confidence) + "%";
      }
      if (aiStrategy && j.strategy_ar) {
        aiStrategy.textContent = j.strategy_ar;
        aiStrategy.className = "v";
      }
      if (badgeLiq && j.quote_volume_short) {
        badgeLiq.textContent = "Vol " + j.quote_volume_short;
      }
      if (badgeCb && j.cb_badge) {
        badgeCb.textContent = "CB " + j.cb_badge;
      }
      if (marketIndBlock) {
        if (j.market_indicators_html_ar) {
          marketIndBlock.innerHTML = j.market_indicators_html_ar;
        } else {
          var rsiT = j.rsi_14 != null ? "RSI=" + j.rsi_14 : "RSI —";
          var smaT =
            j.sma_20 != null ? "SMA20≈" + Number(j.sma_20).toFixed(4) : "SMA20 —";
          var macdT = "";
          var stT = "";
          if (j.indicators && typeof j.indicators === "object") {
            var ind = j.indicators;
            if (ind.hist != null) {
              macdT = " · MACD hist " + Number(ind.hist).toFixed(4);
            }
            if (ind.stoch_rsi_k != null && ind.stoch_rsi_d != null) {
              stT =
                " · StochRSI " +
                Number(ind.stoch_rsi_k).toFixed(0) +
                "/" +
                Number(ind.stoch_rsi_d).toFixed(0);
            }
          }
          var rg =
            j.range_pct_24h != null ? "تذبذب 24h " + j.range_pct_24h + "%" : "";
          var eng =
            j.indicators_engine === "framestream"
              ? "FrameStream"
              : j.indicators_engine || "";
          var hdr = etoroHdr
            ? "مؤشرات (مرجعية + سعر منصة العرض): "
            : "مؤشرات (بيانات مرجعية للشارت): ";
          marketIndBlock.textContent =
            hdr +
            rsiT +
            " · " +
            smaT +
            macdT +
            stT +
            (eng ? " · " + eng : "") +
            (rg ? " · " + rg : "");
        }
      }
      if (summaryLivePre) {
        var sumBody =
          (j.suggestion_ar || "—") +
          " — ثقة " +
          (j.confidence != null ? String(j.confidence) + "%" : "—") +
          "\n\n" +
          (j.market_text_ar || "");
        if (j.recommendation_detail_ar) {
          sumBody += "\n\n---\n" + j.recommendation_detail_ar;
        }
        if (j.indicators_text_ar) {
          sumBody += "\n\n---\n" + j.indicators_text_ar;
        }
        summaryLivePre.textContent = sumBody;
      }
    }
    async function hubSnapPollTick() {
      var symbol = selSymbol.value || "BTCUSDT";
      var iv = selInterval ? selInterval.value : "15m";
      var ex = selExchange ? selExchange.value : "binance";
      var snapUrl =
        "/api/snapshot?symbol=" +
        encodeURIComponent(symbol) +
        "&interval=" +
        encodeURIComponent(iv);
      try {
        var rS = await fetch(snapUrl);
        var j = await rS.json();
        if (!j || !j.ok) {
          var hint =
            j && j.detail
              ? "الخادم: " + JSON.stringify(j.detail)
              : j && j.error
                ? String(j.error)
                : "لا /api/snapshot — أعد تشغيل uvicorn من مجلد web_hub ثم حدّث الصفحة (Ctrl+F5)";
          if (aiRec) {
            aiRec.textContent = "بيانات غير متاحة";
            aiRec.style.color = "#fbbf24";
          }
          if (aiConf) aiConf.textContent = "—";
          if (marketIndBlock) {
            marketIndBlock.textContent = hint.slice(0, 140);
          }
          var errPre = hint.slice(0, 400);
          if (indicatorsLivePre) indicatorsLivePre.textContent = errPre;
          if (marketLivePre) marketLivePre.textContent = errPre;
          if (aiPanelLivePre) aiPanelLivePre.textContent = errPre;
          if (summaryLivePre) summaryLivePre.textContent = errPre;
          return;
        }
        if (ex === "etoro") {
          try {
            var rE = await fetch("/api/etoro/price?symbol=" + encodeURIComponent(symbol));
            var jE = await rE.json();
            var etPx = jE.ok && jE.price != null ? Number(jE.price) : null;
            applySnapshotPanels(j, etPx, { etoroPrice: true });
          } catch (eE) {
            applySnapshotPanels(j, null, { etoroPrice: true });
          }
          try {
            var rSt = await fetch("/api/etoro/status");
            var st = await rSt.json();
            if (aiPositions) {
              aiPositions.textContent =
                st.ok && st.open_positions != null
                  ? String(st.open_positions)
                  : "—";
              aiPositions.className = "v";
            }
          } catch (eSt) {
            if (aiPositions) aiPositions.textContent = "—";
          }
          return;
        }
        applySnapshotPanels(j, null, null);
        if (aiPositions) {
          aiPositions.textContent = "—";
          aiPositions.className = "v muted";
        }
      } catch (eP) {
        if (aiRec) {
          aiRec.textContent = "خطأ شبكة";
          aiRec.style.color = "#fbbf24";
        }
        if (marketIndBlock) {
          marketIndBlock.textContent = String((eP && eP.message) || eP);
        }
        var em = String((eP && eP.message) || eP);
        if (summaryLivePre) summaryLivePre.textContent = em;
      }
    }
    function stopHubSnapPoll() {
      if (hubSnapPollId != null) {
        clearInterval(hubSnapPollId);
        hubSnapPollId = null;
      }
    }
    function startHubSnapPoll() {
      stopHubSnapPoll();
      hubSnapPollId = setInterval(hubSnapPollTick, 4000);
      hubSnapPollTick();
    }
    startHubSnapPoll();

    function applySymbolFilter() {
      var q = (symbolSearch && symbolSearch.value) || "";
      q = q.trim().toUpperCase();
      var list = !q
        ? SYMBOLS_DEFAULT.slice()
        : SYMBOLS_DEFAULT.filter(function (s) {
            return s.indexOf(q) !== -1;
          });
      if (!list.length) {
        list = SYMBOLS_DEFAULT.slice();
      }
      fillSymbolSelect(selSymbol, list, selSymbol.value || "BTCUSDT");
    }

    fillSymbolSelect(selSymbol, SYMBOLS_DEFAULT, "BTCUSDT");
    if (symbolSearch) {
      symbolSearch.placeholder = "إبحث…";
      symbolSearch.addEventListener("input", applySymbolFilter);
      symbolSearch.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" && selSymbol.options.length) {
          selSymbol.selectedIndex = 0;
          selSymbol.dispatchEvent(new Event("change"));
        }
      });
    }
    selSymbol.addEventListener("change", function () {
      if (symbolSearch) symbolSearch.value = selSymbol.value || "";
      hubSnapPollTick();
    });

    function syncExchangeLine() {
      if (!selExchange || !exchangeLine) return;
      var labels = { binance: "Binance", bitget: "Bitget", etoro: "eToro" };
      exchangeLine.textContent = labels[selExchange.value] || selExchange.value;
    }
    syncExchangeLine();
    function onExchangeChanged() {
      syncExchangeLine();
      hubSnapPollTick();
      if (typeof window.__cwHubReload === "function") {
        window.__cwHubReload();
      }
    }
    if (selExchange) selExchange.addEventListener("change", onExchangeChanged);

    if (!chartEl) {
      if (statusEl) statusEl.textContent = "لا يوجد عنصر الشارت — راجع القالب";
      return;
    }

    waitForLWC(function (err) {
      if (err) {
        if (fallback) {
          fallback.classList.add("err");
          fallback.textContent = err.message;
        }
        if (statusEl) statusEl.textContent = err.message;
        hubSnapPollTick();
        return;
      }

      while (chartEl.firstChild) {
        chartEl.removeChild(chartEl.firstChild);
      }

      var chart;
      var series;
      try {
        chart = LightweightCharts.createChart(chartEl, {
          layout: {
            background: { type: "solid", color: "#0f1318" },
            textColor: "#9aa5b8",
            fontFamily: "Segoe UI, system-ui, sans-serif",
          },
          grid: {
            vertLines: { color: "#283044" },
            horzLines: { color: "#283044" },
          },
          crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
          rightPriceScale: { borderColor: "#283044" },
          timeScale: { borderColor: "#283044", timeVisible: true, secondsVisible: false },
        });
        series = addCandleSeries(chart);
      } catch (e) {
        if (statusEl) statusEl.textContent = "خطأ شارت: " + e.message;
        chartEl.textContent = "";
        var errDiv = document.createElement("div");
        errDiv.className = "chart-fallback err";
        errDiv.textContent = e.message;
        chartEl.appendChild(errDiv);
        return;
      }

      function resize() {
        var r = chartEl.getBoundingClientRect();
        var w = Math.max(0, Math.floor(r.width));
        var h = Math.max(0, Math.floor(r.height));
        if (w > 0 && h > 0) {
          chart.applyOptions({ width: w, height: h });
        }
      }

      window.__cwHubChartResize = resize;
      new ResizeObserver(resize).observe(chartEl);
      var wrap = chartEl.closest(".chart-main-col");
      if (wrap) {
        new ResizeObserver(resize).observe(wrap);
      }
      window.addEventListener("resize", resize);
      setTimeout(resize, 0);
      setTimeout(resize, 50);
      setTimeout(resize, 300);

      async function refreshEtoroUi(symbol, statusBase) {
        var ex = selExchange ? selExchange.value : "binance";
        if (ex !== "etoro") return;
        try {
          var rs = await fetch("/api/etoro/status");
          var sj = await rs.json();
          if (connLabel) {
            if (sj.ok) {
              connLabel.textContent =
                "eToro: متصل" +
                (sj.demo ? " (Demo)" : "") +
                " · رصيد USDT: " +
                (sj.balance_usdt != null ? sj.balance_usdt : "—") +
                " · مراكز مفتوحة: " +
                (sj.open_positions != null ? sj.open_positions : "—");
              connLabel.className = "conn-ok";
            } else if (sj.hint) {
              connLabel.textContent = "eToro غير مهيأ على الخادم — افتح «إعدادات» ثم «فحص eToro» للتفاصيل";
              connLabel.className = "conn-bad";
            } else {
              connLabel.textContent = "eToro: " + (sj.error || "خطأ غير معروف");
              connLabel.className = "conn-bad";
            }
          }
          if (statusBase) {
            var rp = await fetch("/api/etoro/price?symbol=" + encodeURIComponent(symbol));
            var pj = await rp.json();
            if (pj.ok && pj.price != null && statusEl) {
              statusEl.textContent =
                statusBase + " · سعر تقريبي eToro: " + String(pj.price);
            }
          }
        } catch (e) {
          if (connLabel) {
            connLabel.textContent = "تعذر طلب /api/etoro/* — " + String((e && e.message) || e);
            connLabel.className = "conn-bad";
          }
        }
      }

      async function load() {
        var symbol = selSymbol.value || "BTCUSDT";
        var interval = selInterval.value || "15m";
        var ex = selExchange ? selExchange.value : "binance";
        var url =
          "/api/klines?symbol=" +
          encodeURIComponent(symbol) +
          "&interval=" +
          encodeURIComponent(interval) +
          "&limit=400";
        statusEl.textContent = "جاري التحميل…";
        if (connLabel) {
          connLabel.textContent = "جاري الاتصال بالخادم المحلي…";
          connLabel.className = "conn-wait";
        }
        try {
          var r;
          try {
            r = await fetch(url);
          } catch (fe) {
            var fm = (fe && fe.message) || String(fe);
            if (connLabel) {
              connLabel.textContent =
                "المتصفح لا يصل لـ /api/klines — غالباً uvicorn غير شغّال أو منفذ/مسار خاطئ (ليس بالضرورة انقطاع إنترنت)";
              connLabel.className = "conn-bad";
            }
            throw new Error("تعذر الطلب: " + fm);
          }
          var j;
          try {
            j = await r.json();
          } catch (je) {
            if (connLabel) {
              connLabel.textContent =
                "استجابة ليست JSON — قد يكون على المنفذ تطبيق آخر وليس CryptoWebHub";
              connLabel.className = "conn-bad";
            }
            throw new Error("استجابة غير صالحة من الخادم");
          }
          if (!r.ok) {
            var backendErr = (j && j.error) || r.statusText || "HTTP " + r.status;
            if (connLabel) {
              connLabel.textContent =
                "الخادم يعمل لكن فشل جلب Binance: " +
                (backendErr.length > 80 ? backendErr.slice(0, 77) + "…" : backendErr);
              connLabel.className = "conn-bad";
            }
            throw new Error(
              backendErr + " — افتح «إعدادات» ثم «فحص اتصال Binance من الخادم»"
            );
          }
          var candles = j.candles || [];
          var data = candles.map(function (c) {
            return { time: c.t, open: c.o, high: c.h, low: c.l, close: c.c };
          });
          try {
            series.setData(data);
          } catch (se) {
            if (connLabel) {
              connLabel.textContent = "بيانات شارت غير صالحة";
              connLabel.className = "conn-bad";
            }
            throw new Error("بيانات شارت غير صالحة: " + se.message);
          }
          chart.timeScale().fitContent();
          resize();
          if (data.length) {
            var last = data[data.length - 1];
            priceEl.textContent = last.close.toLocaleString("en-US", { maximumFractionDigits: 8 });
          } else {
            priceEl.textContent = "—";
          }
          var statusBase =
            symbol +
            " · " +
            interval +
            " · " +
            data.length +
            " شمعة" +
            (ex === "etoro" ? " · الشموع مرجع Binance" : "");
          statusEl.textContent = statusBase;
          var demoOn = false;
          try {
            demoOn = localStorage.getItem("cw_hub_bot_demo") === "1";
          } catch (e3) {}
          if (modeEl) {
            modeEl.textContent = demoOn ? "البوت: ON (ويب — عرض فقط)" : "البوت: OFF (ويب)";
          }
          if (connLabel) {
            connLabel.textContent = "المتصفح ↔ الخادم OK · الشموع من مصدر السوق المرجعي";
            connLabel.className = "conn-ok";
          }
          if (ex === "etoro") {
            await refreshEtoroUi(symbol, statusBase);
          }
        } catch (e) {
          statusEl.textContent =
            "تفاصيل: " + e.message + " — من جذر المشروع: cd web_hub ثم python -m uvicorn app.main:app";
          priceEl.textContent = "—";
          if (connLabel && connLabel.className === "conn-wait") {
            connLabel.textContent = "فشل الطلب — راجع السطر أدناه والكونسول";
            connLabel.className = "conn-bad";
          }
          if (ex === "etoro") {
            await refreshEtoroUi(symbol, null);
          }
        } finally {
          hubSnapPollTick();
        }
      }

      selSymbol.addEventListener("change", load);
      selInterval.addEventListener("change", load);
      window.__cwHubReload = load;
      load();
    }, 50);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
